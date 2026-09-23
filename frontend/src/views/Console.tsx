import { useState, useEffect, useRef, useCallback, type ReactNode } from 'react'
import { Cloud, CloudOff, AlertTriangle, ArrowDownToLine, Gauge as GaugeIcon, Plus, RefreshCw, Magnet, FileUp, FolderPlus, X, Play, Pause, Trash2, Search, RotateCcw, ChevronUp, Folder } from 'lucide-react'
import { useMetrics, addTorrent, addTorrentFile, createMediaDir, fetchMediaDirs, fetchRcloneFiles, fetchRcloneRemotes, retryTorrentDestination, torrentAction, batchAction, type MediaDir, type RcloneEntry, type RcloneRemote, type Torrent, type MountStatus } from '../lib/api'
import { StatCard, Bar, Tag, fmtGb, fmtRate, pct, fmtBytes, SourceBadge, STATE_ZH, STATUS_ZH, fmtMountReads, MountLatency } from '../components/ui'
import { useToast } from '../toast'

const statusTone: Record<MountStatus, 'ok' | 'warn' | 'bad'> = { online: 'ok', degraded: 'warn', offline: 'bad' }
const stateTone: Record<Torrent['state'], 'ok' | 'warn' | 'bad' | 'muted'> = {
  downloading: 'ok', stalled: 'warn', seeding: 'warn', queued: 'muted', error: 'bad', done: 'muted', paused: 'warn',
}
const destinationStatus: Record<string, string> = { waiting: '等待下载完成', uploading: '上传中', done: '已上传', error: '上传失败', orphaned: '任务已不存在' }

function RowBtn({ children, onClick, title, danger }: { children: ReactNode; onClick: () => void; title?: string; danger?: boolean }) {
  return (
    <button onClick={onClick} title={title} aria-label={title}
      className={`inline-flex h-7 w-7 items-center justify-center rounded-md border border-line bg-white/4 text-dim transition-colors ${danger ? 'hover:border-rose-400/40 hover:text-rose-300' : 'hover:border-aurora-2/40 hover:text-aurora-1'}`}>
      {children}
    </button>
  )
}

export default function ConsoleView() {
  const { data, sources } = useMetrics()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [addMode, setAddMode] = useState<'magnet' | 'file'>('magnet')
  const [magnet, setMagnet] = useState('')
  const [torrentFile, setTorrentFile] = useState<File | null>(null)
  const [savePath, setSavePath] = useState('')
  const [mediaDirs, setMediaDirs] = useState<MediaDir[]>([])
  const [dirsBusy, setDirsBusy] = useState(false)
  const [showNewDir, setShowNewDir] = useState(false)
  const [newDir, setNewDir] = useState('')
  const [dirMsg, setDirMsg] = useState('')
  const [dirMsgBad, setDirMsgBad] = useState(false)
  const [targetMode, setTargetMode] = useState<'local' | 'remote'>('local')
  const [rcloneRemotes, setRcloneRemotes] = useState<RcloneRemote[]>([])
  const [remoteBusy, setRemoteBusy] = useState(false)
  const [targetRemote, setTargetRemote] = useState('')
  const [targetRemotePath, setTargetRemotePath] = useState('')
  const [remoteBrowsePath, setRemoteBrowsePath] = useState('')
  const [remoteDirs, setRemoteDirs] = useState<RcloneEntry[]>([])
  const [remotePathBusy, setRemotePathBusy] = useState(false)
  const [remotePathMsg, setRemotePathMsg] = useState('')
  const targetRemotePathRef = useRef('')
  targetRemotePathRef.current = targetRemotePath
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [msgBad, setMsgBad] = useState(false)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const totalReads = data.mounts.reduce((a, m) => a + (m.reads || 0), 0)
  const totalUsed = data.mounts.reduce((a, m) => a + m.usedGb, 0)
  const totalCap = data.mounts.reduce((a, m) => a + m.capGb, 0)
  const activeDl = data.torrents.filter((t) => t.state === 'downloading').length
  const dlSpeed = data.torrents.reduce((a, t) => a + (t.state === 'downloading' ? t.speed : 0), 0)
  // 挂载聚合「实时读取」：统一按 local 读字节速率呈现（多挂载求和也以字节为准）
  const readText = data.mounts.length > 0
    ? (data.mounts.every((m) => m.driver === 'local') ? fmtBytes(totalReads) + '/s' : `${totalReads}`)
    : '—'

  const loadMediaDirs = async () => {
    setDirsBusy(true)
    const dirs = await fetchMediaDirs()
    setMediaDirs(dirs)
    setDirsBusy(false)
  }

  const loadRcloneRemotes = async () => {
    setRemoteBusy(true)
    const result = await fetchRcloneRemotes()
    const next = result?.remotes || []
    setRcloneRemotes(next)
    setTargetRemote((current) => next.some((item) => item.name === current) ? current : (next[0]?.name || ''))
    setRemoteBusy(false)
  }

  const loadRemotePath = useCallback(async (name: string, nextPath: string) => {
    if (!name) return
    setRemotePathBusy(true)
    setRemotePathMsg('')
    const result = await fetchRcloneFiles(name, nextPath)
    if (result) {
      setRemoteBrowsePath(nextPath)
      setRemoteDirs((result.items || []).filter((item) => item.isDir))
      setRemotePathMsg(result.detail || '')
    } else {
      setRemoteDirs([])
      setRemotePathMsg('目录读取失败，请检查网盘连接')
    }
    setRemotePathBusy(false)
  }, [])

  useEffect(() => {
    // Preload remotes so the target selector is ready when the dialog opens.
    void loadRcloneRemotes()
  }, [])

  useEffect(() => {
    if (targetMode !== 'remote' || !targetRemote) {
      setRemoteDirs([])
      setRemotePathMsg('')
      return
    }
    const initial = targetRemotePathRef.current.trim()
    setRemoteBrowsePath(initial)
    void loadRemotePath(targetRemote, initial)
  }, [loadRemotePath, targetMode, targetRemote])

  const openAdd = () => {
    setOpen(true)
    setDirMsg('')
    setDirMsgBad(false)
    void Promise.all([loadMediaDirs(), loadRcloneRemotes()])
  }

  const closeAdd = () => {
    if (busy) return
    setOpen(false)
    setAddMode('magnet')
    setMagnet('')
    setTorrentFile(null)
    setSavePath('')
    setShowNewDir(false)
    setNewDir('')
    setMsg('')
    setMsgBad(false)
    setDirMsg('')
    setDirMsgBad(false)
    setTargetMode('local')
    setTargetRemote('')
    setTargetRemotePath('')
    setRemoteBrowsePath('')
    setRemoteDirs([])
    setRemotePathMsg('')
  }

  const chooseMode = (mode: 'magnet' | 'file') => {
    setAddMode(mode)
    setMsg('')
    setMsgBad(false)
  }

  const selectTorrentFile = (file?: File) => {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.torrent')) {
      setTorrentFile(null)
      setMsgBad(true)
      setMsg('请选择 .torrent 文件')
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      setTorrentFile(null)
      setMsgBad(true)
      setMsg('种子文件不能超过 20 MB')
      return
    }
    setTorrentFile(file)
    setMsg('')
    setMsgBad(false)
  }

  const createDownloadDir = async () => {
    const path = newDir.trim().replace(/\\/g, '/')
    const parts = path.split('/')
    if (!path || path.startsWith('/') || parts.some((part) => !part || part === '.' || part === '..')) {
      setDirMsgBad(true)
      setDirMsg('请输入下载盘内的相对目录，例如：电影/2026')
      return
    }
    setDirsBusy(true)
    const r = await createMediaDir(path)
    if (r.ok) {
      await loadMediaDirs()
      setSavePath(r.path)
      setShowNewDir(false)
      setNewDir('')
      setDirMsgBad(false)
      setDirMsg('目录已创建，并已选中')
    } else {
      setDirsBusy(false)
      setDirMsgBad(true)
      setDirMsg(r.detail)
    }
  }

  const submit = async () => {
    if (addMode === 'magnet' && !magnet.trim()) return
    if (addMode === 'file' && !torrentFile) return
    if (targetMode === 'remote' && !targetRemote) {
      setMsgBad(true)
      setMsg('请先选择已接入的网盘')
      return
    }
    setBusy(true); setMsg(''); setMsgBad(false)
    const remote = targetMode === 'remote' ? targetRemote : ''
    const remotePath = targetMode === 'remote' ? targetRemotePath.trim() : ''
    const r = addMode === 'magnet'
      ? await addTorrent(magnet.trim(), savePath, remote, remotePath)
      : await addTorrentFile(torrentFile!, savePath, remote, remotePath)
    const target = targetMode === 'remote'
      ? `完成后上传到 ${remote}:${remotePath || '/'}`
      : `本地 · ${savePath || '下载根目录'}`
    setMsg(r.ok ? (r.mode === 'qbittorrent' ? `已提交到 qBittorrent · ${target}` : `已加入队列（演示） · ${target}`) : r.detail)
    setMsgBad(!r.ok)
    if (r.ok) {
      setMagnet('')
      setTorrentFile(null)
    }
    setBusy(false)
  }

  const qn = q.trim().toLowerCase()
  const torrents = data.torrents.filter((t) => !qn || t.name.toLowerCase().includes(qn))

  const doAction = async (id: string, action: string, label: string) => {
    if (action === 'remove' && !window.confirm('仅从 qBittorrent 移除任务，已下载文件会保留。继续吗？')) return
    const r = await torrentAction(id, action)
    toast(r.ok ? `${label}成功` + (r.mode === 'demo' ? '（演示）' : '') : `${label}失败：${r.detail}`, r.ok ? 'ok' : 'bad')
  }

  const retryDestination = async (id: string) => {
    const r = await retryTorrentDestination(id)
    toast(r.ok ? '网盘转存已加入重试队列' : `转存重试失败：${r.detail}`, r.ok ? 'ok' : 'bad')
  }

  const toggleSel = (id: string) => setSel((s) => {
    const next = new Set(s)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const toggleAll = () => setSel(torrents.length === sel.size ? new Set() : new Set(torrents.map((t) => t.id)))
  const doBatch = async (action: string, label: string) => {
    if (sel.size === 0) return
    if (action === 'remove' && !window.confirm(`仅移除选中的 ${sel.size} 个任务，已下载文件会保留。继续吗？`)) return
    const r = await batchAction([...sel], action)
    toast(r.failed ? `${label}：成功 ${r.done} / 失败 ${r.failed}` : `${label}成功 ${r.done} 项`, r.failed ? 'warn' : 'ok')
    setSel(new Set())
  }

  return (
    <div className="min-w-0 px-4 py-6 md:px-10 md:py-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.3em] text-dim">控制台</div>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">资源管理台</h1>
        </div>
        <div className="flex flex-col items-end gap-2">
          <SourceBadge sources={sources} />
          <button onClick={openAdd} className="panel panel-hover flex items-center gap-2 px-4 py-2 text-sm text-fg">
            <Plus size={16} /> 添加任务
          </button>
        </div>
      </header>

      {/* stat row */}
      <section className="mt-8 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="挂载存储" value={fmtGb(totalUsed)} accent
          sub={<span className="num">{totalCap > 0 ? `${fmtGb(totalCap)} 容量 · ${pct(totalUsed / totalCap)} 占用` : '—'}</span>} />
        <StatCard label="下载速率" value={fmtRate(dlSpeed)} accent
          sub={<span className="inline-flex items-center gap-1.5"><span className="live-dot bg-teal-400 text-teal-400" /> {activeDl} 个进行中</span>} />
        <StatCard label="实时读取" value={readText}
          sub={<span className="num text-dim">{data.mounts.filter((m) => m.status === 'online').length}/{data.mounts.length} 挂载在线</span>} />
        <StatCard label="本地磁盘" value={fmtGb(data.disk.usedGb)}
          sub={<span className="num">总计 {fmtGb(data.disk.capGb)} · 可用 {fmtGb(Math.max(0, data.disk.capGb - data.disk.usedGb))}</span>} />
      </section>

      <div className="mt-6 grid grid-cols-1 gap-6 xl:grid-cols-3">
        {/* mounts */}
        <section className="panel px-6 py-5 xl:col-span-2">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-fg">挂载存储</h2>
            <span title="每 2 秒自动刷新" className="inline-flex items-center gap-1.5 text-xs text-dim"><RefreshCw size={13} /></span>
          </div>
          <div className="mt-4 flex flex-col gap-3">
            {data.mounts.length === 0 && (
              <div className="py-10 text-center text-sm text-dim">暂无已接入的挂载（启动 rclone rc 后自动显示）</div>
            )}
            {data.mounts.map((m) => (
              <div key={m.id} className="panel-hover rounded-xl border border-line bg-white/3 px-4 py-3">
                <div className="flex items-center gap-3">
                  <div className={`grid h-9 w-9 place-items-center rounded-lg ${m.status === 'offline' ? 'bg-rose-500/15 text-rose-300' : 'grad-bar text-ink'}`}>
                    {m.status === 'offline' ? <CloudOff size={18} /> : <Cloud size={18} />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="num text-sm text-fg">{m.name}</span>
                      <Tag tone={statusTone[m.status]}>{STATUS_ZH[m.status] ?? m.status}</Tag>
                    </div>
                    <div className="text-xs text-dim">{m.driver} · {m.provider}</div>
                  </div>
                  <div className="hidden text-right sm:block">
                    <div className="num text-xs text-fg">{fmtGb(m.usedGb)} / {m.capGb > 0 ? fmtGb(m.capGb) : '—'}</div>
                    <div className="mt-0.5 w-28"><Bar p={m.capGb > 0 ? m.usedGb / m.capGb : 0} /></div>
                  </div>
                  <div className="hidden w-28 text-right md:block">
                    <div className="num text-xs text-teal-300">{fmtMountReads(m)}</div>
                    <MountLatency m={m} />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* disk mini gauge */}
        <section className="panel flex flex-col px-6 py-5">
          <h2 className="text-sm font-medium text-fg">中转磁盘</h2>
          <div className="my-auto flex flex-col items-center py-4">
            <div className="num grad-txt text-4xl font-semibold">{pct(data.disk.usedGb / data.disk.capGb)}</div>
            <div className="mt-1 text-xs text-dim">使用率 · {fmtGb(data.disk.usedGb)} / {fmtGb(data.disk.capGb)}</div>
            <div className="mt-4 w-40"><Bar p={data.disk.usedGb / data.disk.capGb} /></div>
          </div>
          <div className="grid grid-cols-2 gap-2 text-center">
            <div className="rounded-lg bg-white/4 py-2"><div className="num text-sm text-fg">{fmtBytes(data.disk.rw)}/s</div><div className="text-[11px] text-dim">读写</div></div>
            <div className="rounded-lg bg-white/4 py-2"><div className="num text-sm text-fg">{fmtGb(data.disk.capGb - data.disk.usedGb)}</div><div className="text-[11px] text-dim">可用</div></div>
          </div>
        </section>
      </div>

      {/* torrents */}
      <section className="panel mt-6 px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-medium text-fg"><ArrowDownToLine size={16} className="text-aurora-1" /> 磁力调度</h2>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dim" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="筛选磁力…"
                className="w-36 rounded-lg border border-line bg-white/4 py-1.5 pl-7 pr-2 text-xs text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none sm:w-48" />
            </div>
            <button onClick={openAdd} className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-white/4 px-3 py-1.5 text-xs text-fg hover:bg-white/8">
              <Plus size={13} /> 新增
            </button>
          </div>
        </div>
        {sel.size > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-aurora-2/30 bg-aurora-2/5 px-3 py-2">
            <span className="text-sm text-fg">已选 <span className="num text-aurora-1">{sel.size}</span> 项</span>
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => doBatch('pause', '暂停')} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-3 py-1.5 text-xs text-fg hover:bg-white/8"><Pause size={12} />暂停</button>
              <button onClick={() => doBatch('resume', '续传')} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-3 py-1.5 text-xs text-fg hover:bg-white/8"><Play size={12} />续传</button>
              <button onClick={() => doBatch('remove', '删除')} className="inline-flex items-center gap-1 rounded-md border border-rose-400/30 bg-rose-400/10 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-400/20"><Trash2 size={12} />删除</button>
              <button onClick={() => setSel(new Set())} className="px-1 text-xs text-dim hover:text-fg">取消</button>
            </div>
          </div>
        )}
        {torrents.length === 0 && (
          <div className="py-8 text-center text-sm text-dim">暂无磁力任务（添加磁力或启动 qBittorrent）</div>
        )}
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-dim">
                <th className="w-8 pb-3 pr-1 font-normal">
                  <input type="checkbox" checked={torrents.length > 0 && sel.size === torrents.length} onChange={toggleAll} className="accent-[#a78bfa]" />
                </th>
                <th className="pb-3 pr-4 font-normal">任务</th>
                <th className="pb-3 pr-4 whitespace-nowrap font-normal">状态</th>
                <th className="pb-3 pr-4 whitespace-nowrap font-normal">进度</th>
                <th className="pb-3 pr-4 whitespace-nowrap text-right font-normal">速率</th>
                <th className="pb-3 pr-4 whitespace-nowrap text-right font-normal">大小</th>
                <th className="pb-3 whitespace-nowrap text-right font-normal">操作</th>
              </tr>
            </thead>
            <tbody>
              {torrents.map((t) => (
                <tr key={t.id} className="border-t border-line/60 group">
                  <td className="py-3.5 pr-1">
                    <input type="checkbox" checked={sel.has(t.id)} onChange={() => toggleSel(t.id)} className="accent-[#a78bfa]" />
                  </td>
                  <td className="max-w-[340px] py-3.5 pr-4">
                    <div className="flex items-center gap-3">
                      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white/5 text-dim group-hover:text-aurora-2 transition-colors">
                        {t.state === 'error' ? <AlertTriangle size={15} className="text-rose-300" /> : <GaugeIcon size={15} />}
                      </div>
                      <div className="min-w-0">
                        <div className="num truncate text-fg" title={t.name}>{t.name}</div>
                        <div className="text-[11px] text-dim">S/L {t.seeders} · P/L {t.leechers}</div>
                        {t.destination && <div className={`flex min-w-0 items-center gap-1 text-[10px] ${t.destination.status === 'error' || t.destination.status === 'orphaned' ? 'text-rose-300' : 'text-aurora-1'}`} title={`${t.destination.remote}:${t.destination.path || '/'}`}>
                          <span className="min-w-0 truncate">网盘 · {t.destination.remote}:{t.destination.path || '/'} · {destinationStatus[t.destination.status] ?? t.destination.status}</span>
                          {t.destination.status === 'error' && <button onClick={() => retryDestination(t.destination!.id)} title="重试网盘转存" aria-label="重试网盘转存" className="grid h-5 w-5 shrink-0 place-items-center rounded text-rose-300 hover:bg-rose-400/10 hover:text-rose-200"><RotateCcw size={11} /></button>}
                        </div>}
                      </div>
                    </div>
                  </td>
                  <td className="py-3.5 pr-4 whitespace-nowrap"><Tag tone={stateTone[t.state]}>{STATE_ZH[t.state] ?? t.state}</Tag></td>
                  <td className="py-3.5 pr-4 whitespace-nowrap">
                    <div className="flex items-center gap-2">
                      <div className="w-24"><Bar p={t.progress} className={t.state === 'error' ? 'bg-rose-400' : 'grad-bar'} /></div>
                      <span className="num text-xs text-dim">{pct(t.progress)}</span>
                    </div>
                  </td>
                  <td className="py-3.5 pr-4 whitespace-nowrap text-right num text-teal-300">{t.speed > 0 ? fmtRate(t.speed) : '—'}</td>
                  <td className="py-3.5 whitespace-nowrap text-right num text-dim">{fmtGb(t.sizeGb)}</td>
                  <td className="py-3.5 whitespace-nowrap text-right">
                    <div className="flex justify-end gap-1">
                      {t.state === 'paused'
                        ? <RowBtn onClick={() => doAction(t.id, 'resume', '恢复')} title="继续"><Play size={12} /></RowBtn>
                        : (t.state === 'downloading' || t.state === 'stalled' || t.state === 'queued' || t.state === 'seeding')
                          ? <RowBtn onClick={() => doAction(t.id, 'pause', '暂停')} title="暂停"><Pause size={12} /></RowBtn>
                          : null}
                      <RowBtn danger onClick={() => doAction(t.id, 'remove', '删除')} title="删除"><Trash2 size={12} /></RowBtn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm" onClick={closeAdd}>
          <div className="panel w-full max-w-lg px-6 py-5" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm font-medium">{addMode === 'magnet' ? <Magnet size={16} className="text-aurora-1" /> : <FileUp size={16} className="text-aurora-1" />} 添加任务</span>
              <button onClick={closeAdd} disabled={busy} aria-label="关闭" title="关闭" className="text-dim hover:text-fg disabled:opacity-40"><X size={16} /></button>
            </div>
            <div role="tablist" aria-label="添加方式" className="mt-4 grid grid-cols-2 gap-1 rounded-lg border border-line bg-white/4 p-1">
              <button type="button" role="tab" aria-selected={addMode === 'magnet'} onClick={() => chooseMode('magnet')} className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs transition-colors ${addMode === 'magnet' ? 'bg-white/10 text-fg' : 'text-dim hover:text-fg'}`}>
                <Magnet size={13} /> 磁力链接
              </button>
              <button type="button" role="tab" aria-selected={addMode === 'file'} onClick={() => chooseMode('file')} className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs transition-colors ${addMode === 'file' ? 'bg-white/10 text-fg' : 'text-dim hover:text-fg'}`}>
                <FileUp size={13} /> 种子文件
              </button>
            </div>
            <div className="mt-4">
              <label htmlFor="torrent-target-mode" className="block text-xs text-dim">下载目标</label>
              <select id="torrent-target-mode" value={targetMode} onChange={(e) => setTargetMode(e.target.value as 'local' | 'remote')} disabled={busy}
                className="aurora-select mt-1.5 w-full rounded-lg border border-line px-3 py-2 text-sm focus:border-aurora-2/50 focus:outline-none disabled:opacity-60">
                <option value="local">本地下载盘</option>
                <option value="remote">下载完成后上传到网盘</option>
              </select>
              {targetMode === 'remote' && (
                <div className="mt-2 grid gap-2">
                  <div className="flex items-center gap-2">
                    <select value={targetRemote} onChange={(e) => { setTargetRemote(e.target.value); setTargetRemotePath('') }} disabled={busy || remoteBusy || !rcloneRemotes.length}
                      className="aurora-select w-full rounded-lg border border-line px-3 py-2 text-sm focus:border-aurora-2/50 focus:outline-none disabled:opacity-60">
                      {!rcloneRemotes.length && <option value="">{remoteBusy ? '正在读取网盘列表…' : '暂无可用网盘'}</option>}
                      {rcloneRemotes.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.type || 'remote'}</option>)}
                    </select>
                    <button type="button" onClick={() => void loadRcloneRemotes()} disabled={busy || remoteBusy} title="刷新网盘列表" aria-label="刷新网盘列表"
                      className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-line bg-white/4 text-dim hover:bg-white/8 hover:text-fg disabled:opacity-40">
                      <RefreshCw size={13} className={remoteBusy ? 'animate-spin' : ''} />
                    </button>
                  </div>
                  {!remoteBusy && !rcloneRemotes.length && <div className="text-xs text-rose-300">没有读取到已接入网盘，请先在网盘设置中完成对接。</div>}
                  <input value={targetRemotePath} onChange={(e) => setTargetRemotePath(e.target.value)} placeholder="网盘目标目录，留空表示根目录" disabled={busy || remoteBusy}
                    className="w-full rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none disabled:opacity-60" />
                  <div className="rounded-lg border border-line bg-white/3 px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <Folder size={13} className="text-aurora-1" />
                      <span className="text-[11px] text-dim">选择网盘目录</span>
                      <button type="button" onClick={() => void loadRemotePath(targetRemote, targetRemotePath.trim())} disabled={busy || remotePathBusy}
                        title="读取目录" aria-label="读取目录" className="ml-auto grid h-6 w-6 place-items-center rounded-md text-dim hover:bg-white/8 hover:text-fg disabled:opacity-40">
                        <RefreshCw size={12} className={remotePathBusy ? 'animate-spin' : ''} />
                      </button>
                    </div>
                    <div className="mt-2 flex items-center gap-1.5 text-[11px]">
                      <button type="button" onClick={() => { const parent = remoteBrowsePath.split('/').filter(Boolean).slice(0, -1).join('/'); void loadRemotePath(targetRemote, parent) }} disabled={busy || remotePathBusy || !remoteBrowsePath}
                        className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2 py-1 text-dim hover:text-fg disabled:opacity-40"><ChevronUp size={11} /> 上级</button>
                      <span className="min-w-0 flex-1 truncate text-dim">当前：{remoteBrowsePath || '/'}</span>
                      <button type="button" onClick={() => setTargetRemotePath(remoteBrowsePath)} disabled={busy || remotePathBusy}
                        className="rounded-md border border-aurora-2/30 bg-aurora-2/8 px-2 py-1 text-aurora-1 hover:bg-aurora-2/15 disabled:opacity-40">选择</button>
                    </div>
                    {remotePathMsg && <div className="mt-2 text-[11px] text-rose-300">{remotePathMsg}</div>}
                    {remoteDirs.length > 0 && <div className="mt-2 grid max-h-28 gap-1 overflow-y-auto">
                      {remoteDirs.map((item) => <button key={item.path} type="button" onClick={() => void loadRemotePath(targetRemote, item.path)} disabled={remotePathBusy}
                        className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] text-dim hover:bg-white/6 hover:text-fg disabled:opacity-50"><Folder size={12} className="shrink-0 text-aurora-1" /><span className="truncate">{item.name}</span></button>)}
                    </div>}
                  </div>
                  <div className="text-[11px] text-dim/70">任务会先下载到本地，完成后自动上传到所选网盘。</div>
                </div>
              )}
            </div>
            <div className="mt-4">
              <div className="flex items-center justify-between gap-3">
                <label htmlFor="torrent-save-path" className="text-xs text-dim">{targetMode === 'remote' ? '本地临时目录' : '保存到'}</label>
                <div className="flex items-center gap-2">
                  <button type="button" onClick={() => { setShowNewDir((v) => !v); setDirMsg(''); setDirMsgBad(false) }} disabled={busy || dirsBusy}
                    className="inline-flex items-center gap-1 text-xs text-dim hover:text-fg disabled:opacity-40">
                    <FolderPlus size={13} /> {showNewDir ? '取消新建' : '新建目录'}
                  </button>
                  <button type="button" onClick={() => void loadMediaDirs()} disabled={busy || dirsBusy} title="刷新目录" aria-label="刷新目录"
                    className="inline-flex h-6 w-6 items-center justify-center rounded-md text-dim hover:bg-white/8 hover:text-fg disabled:opacity-40">
                    <RefreshCw size={13} className={dirsBusy ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>
              <div className="mt-1.5 flex gap-2">
                <select id="torrent-save-path" value={savePath} onChange={(e) => setSavePath(e.target.value)} disabled={busy || dirsBusy}
                  className="aurora-select min-w-0 flex-1 rounded-lg border border-line px-3 py-2 text-sm focus:border-aurora-2/50 focus:outline-none disabled:opacity-60">
                  <option value="">下载根目录（/downloads）</option>
                  {mediaDirs.map((dir) => <option key={dir.path} value={dir.path}>{dir.path}</option>)}
                </select>
              </div>
              {showNewDir && (
                <div className="mt-2 flex gap-2">
                  <input value={newDir} onChange={(e) => setNewDir(e.target.value)} placeholder="例如：电影/2026" disabled={busy || dirsBusy}
                    className="min-w-0 flex-1 rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none disabled:opacity-60" />
                  <button type="button" onClick={() => void createDownloadDir()} disabled={busy || dirsBusy || !newDir.trim()}
                    className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-line bg-white/4 px-3 py-2 text-xs text-fg hover:bg-white/8 disabled:opacity-40">
                    <FolderPlus size={13} /> 创建
                  </button>
                </div>
              )}
              {dirMsg && <div className={`mt-1.5 text-xs ${dirMsgBad ? 'text-rose-300' : 'text-aurora-1'}`}>{dirMsg}</div>}
            </div>
            {addMode === 'magnet' ? (
              <textarea value={magnet} onChange={(e) => setMagnet(e.target.value)} rows={3}
                placeholder="magnet:?xt=urn:btih:…"
                className="mt-3 w-full rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none" />
            ) : (
              <div className="mt-3">
                <input id="torrent-file" type="file" accept=".torrent,application/x-bittorrent" className="sr-only" onChange={(e) => selectTorrentFile(e.target.files?.[0])} />
                <label htmlFor="torrent-file" className="flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-line bg-white/3 px-4 py-8 text-center transition-colors hover:border-aurora-2/50 hover:bg-white/5">
                  <FileUp size={22} className="text-aurora-1" />
                  <span className="mt-2 text-sm text-fg">{torrentFile ? torrentFile.name : '选择 .torrent 文件'}</span>
                  <span className="mt-1 text-xs text-dim">单个文件，最大 20 MB</span>
                </label>
              </div>
            )}
            {msg && <div className={`mt-3 text-xs ${msgBad ? 'text-rose-300' : 'text-aurora-1'}`}>{msg}</div>}
            <div className="mt-4 flex justify-end gap-3">
              <button onClick={closeAdd} disabled={busy} className="rounded-lg border border-line bg-white/4 px-4 py-2 text-sm text-dim hover:text-fg disabled:opacity-40">取消</button>
              <button onClick={submit} disabled={busy || remoteBusy || (targetMode === 'remote' && !targetRemote) || (addMode === 'magnet' ? !magnet.trim() : !torrentFile)} className="rounded-lg grad-bar px-4 py-2 text-sm font-medium text-ink disabled:opacity-40">{busy ? '处理中…' : '加入队列'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
