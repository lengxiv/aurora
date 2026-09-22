import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check, ChevronRight, Copy, Download, File, Folder, FolderPlus, LoaderCircle,
  Move, Pencil, RefreshCw, Trash2, Upload, X,
} from 'lucide-react'
import {
  cancelRcloneTransfer, copyRcloneFile, deleteRcloneFile, downloadRcloneFile,
  fetchRcloneFiles, fetchRcloneTransfers, mkdirRclone, renameRcloneFile,
  uploadRcloneFile, type RcloneEntry, type RcloneRemote, type RcloneTransfer,
} from '../../lib/api'
import { EmptyState } from '../../components/ui'
import { useToast } from '../../toast'

type DialogKind = 'mkdir' | 'rename' | 'copy' | 'move' | 'download'

interface DialogState {
  kind: DialogKind
  entries: RcloneEntry[]
  value: string
  targetRemote: string
  targetPath: string
}

interface Props {
  remotes: RcloneRemote[]
}

function joinPath(parent: string, child: string) {
  return [parent.replace(/^\/+|\/+$/g, ''), child.replace(/^\/+|\/+$/g, '')]
    .filter(Boolean).join('/')
}

function fmtBytes(value: number) {
  if (!value) return '—'
  if (value >= 1073741824) return `${(value / 1073741824).toFixed(2)} GB`
  if (value >= 1048576) return `${(value / 1048576).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(0)} KB`
  return `${value} B`
}

function fmtSpeed(value: number) {
  return value > 0 ? `${fmtBytes(value)}/s` : '等待中'
}

function fmtDate(value: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function transferStatus(job: RcloneTransfer) {
  if (job.status === 'running') return job.progress === null ? '处理中' : `${Math.round(job.progress * 100)}%`
  if (job.status === 'done') return '完成'
  if (job.status === 'canceled') return '已取消'
  return job.detail || '失败'
}

export default function RcloneBrowser({ remotes }: Props) {
  const toast = useToast()
  const inputRef = useRef<HTMLInputElement>(null)
  const [remote, setRemote] = useState(remotes[0]?.name || '')
  const [path, setPath] = useState('')
  const [items, setItems] = useState<RcloneEntry[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [transfers, setTransfers] = useState<RcloneTransfer[]>([])
  const [showTransfers, setShowTransfers] = useState(false)
  const previousJobs = useRef<Record<string, RcloneTransfer['status']>>({})

  useEffect(() => {
    if (!remotes.some((item) => item.name === remote)) {
      setRemote(remotes[0]?.name || '')
      setPath('')
    }
  }, [remotes, remote])

  const load = useCallback(async () => {
    if (!remote) return
    setLoading(true)
    setError('')
    const data = await fetchRcloneFiles(remote, path)
    if (data) {
      setItems(data.items || [])
      setSelected(new Set())
    } else {
      setError('目录读取失败，请检查网盘连接')
    }
    setLoading(false)
  }, [path, remote])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    let on = true
    const tick = async () => {
      const jobs = await fetchRcloneTransfers()
      if (!on) return
      setTransfers(jobs)
      const changed = jobs.some((job) => {
        const old = previousJobs.current[job.id]
        return old === 'running' && job.status !== 'running'
      })
      previousJobs.current = Object.fromEntries(jobs.map((job) => [job.id, job.status]))
      if (changed) load()
    }
    tick()
    const id = setInterval(tick, 1600)
    return () => { on = false; clearInterval(id) }
  }, [load])

  const activeTransfers = transfers.filter((job) => job.status === 'running')
  const crumbs = useMemo(() => path.split('/').filter(Boolean), [path])
  const allSelected = items.length > 0 && items.every((item) => selected.has(item.path))
  const selectedItems = items.filter((item) => selected.has(item.path))

  const toggle = (item: RcloneEntry) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(item.path)) next.delete(item.path)
      else next.add(item.path)
      return next
    })
  }

  const openDialog = (kind: DialogKind, entries = selectedItems) => {
    if (!entries.length && kind !== 'mkdir') {
      toast('请先选择文件或目录', 'bad')
      return
    }
    setDialog({
      kind,
      entries,
      value: kind === 'rename' ? entries[0]?.name || '' : '',
      targetRemote: remote,
      targetPath: kind === 'download' ? '' : path,
    })
  }

  const closeDialog = () => setDialog(null)

  const runDialog = async () => {
    if (!dialog) return
    if (dialog.kind === 'mkdir') {
      const target = joinPath(path, dialog.value.trim())
      if (!target) { toast('请输入目录名称', 'bad'); return }
      const result = await mkdirRclone(remote, target)
      toast(result.ok ? '目录已创建' : `创建失败：${result.detail}`, result.ok ? 'ok' : 'bad')
      if (result.ok) { closeDialog(); load() }
      return
    }
    if (dialog.kind === 'rename') {
      const name = dialog.value.trim()
      if (!name) { toast('请输入新名称', 'bad'); return }
      const entry = dialog.entries[0]
      const result = await renameRcloneFile(remote, entry.path, name, entry.isDir)
      toast(result.ok ? '重命名任务已提交' : `重命名失败：${result.detail}`, result.ok ? 'ok' : 'bad')
      if (result.ok) closeDialog()
      return
    }

    const results = []
    for (const entry of dialog.entries) {
      const result = dialog.kind === 'download'
        ? await downloadRcloneFile(remote, entry.path, dialog.targetPath.trim(), entry.isDir)
        : await copyRcloneFile(remote, entry.path, dialog.targetRemote, dialog.targetPath.trim(), entry.isDir, dialog.kind)
      results.push(result)
    }
    const success = results.filter((result) => result.ok).length
    const label = dialog.kind === 'download' ? '下载' : dialog.kind === 'copy' ? '复制' : '移动'
    toast(success === results.length ? `${label}任务已提交` : `${label}已提交 ${success}/${results.length} 个`, success ? 'ok' : 'bad')
    if (success) closeDialog()
  }

  const deleteEntries = async (entries: RcloneEntry[]) => {
    if (!entries.length) { toast('请先选择文件或目录', 'bad'); return }
    const label = entries.length === 1 ? `「${entries[0].name}」` : `${entries.length} 个项目`
    if (!window.confirm(`删除${label}？目录会递归删除，操作不可恢复。`)) return
    const results = await Promise.all(entries.map((item) => deleteRcloneFile(remote, item.path, item.isDir)))
    const success = results.filter((result) => result.ok).length
    toast(success === results.length ? '删除任务已提交' : `已提交 ${success}/${results.length} 个删除任务`, success ? 'ok' : 'bad')
    setSelected(new Set())
    load()
  }

  const deleteSelected = () => deleteEntries(selectedItems)

  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length) return
    const results = []
    for (const file of files) results.push(await uploadRcloneFile(remote, path, file))
    const success = results.filter((result) => result.ok).length
    toast(success === files.length ? `已提交 ${success} 个上传任务` : `已提交 ${success}/${files.length} 个上传任务`, success ? 'ok' : 'bad')
    setShowTransfers(true)
  }

  const browse = (next: string) => {
    setPath(next)
    setSelected(new Set())
  }

  if (!remote) return null

  return (
    <section className="mt-8 max-w-6xl">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.2em] text-dim">文件管理</div>
          <h2 className="mt-1 text-xl font-semibold text-fg">网盘文件</h2>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowTransfers((value) => !value)} title="传输任务" aria-label="传输任务"
            className={`relative grid h-8 w-8 place-items-center rounded-lg border ${showTransfers ? 'border-aurora-2/50 text-aurora-1' : 'border-line bg-white/4 text-dim hover:text-fg'}`}>
            <Upload size={14} />
            {activeTransfers.length > 0 && <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-aurora-2 px-1 text-[9px] text-ink">{activeTransfers.length}</span>}
          </button>
          <input ref={inputRef} type="file" multiple className="hidden" onChange={upload} />
          <button onClick={() => inputRef.current?.click()} title="上传文件" aria-label="上传文件"
            className="inline-flex items-center gap-1.5 rounded-lg grad-bar px-3 py-2 text-xs font-medium text-ink"><Upload size={14} /> 上传</button>
        </div>
      </div>

      <div className="panel overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <select value={remote} onChange={(event) => { setRemote(event.target.value); setPath('') }}
            className="min-w-32 rounded-lg border border-line bg-white/4 px-2.5 py-2 text-xs text-fg focus:border-aurora-2/50 focus:outline-none">
            {remotes.map((item) => <option key={item.name} value={item.name}>{item.name} · {item.type || 'remote'}</option>)}
          </select>
          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-xs">
            <button onClick={() => browse('')} className={`shrink-0 rounded-md px-2 py-1.5 ${!path ? 'bg-white/8 text-fg' : 'text-dim hover:text-fg'}`}>根目录</button>
            {crumbs.map((crumb, index) => {
              const crumbPath = crumbs.slice(0, index + 1).join('/')
              return <span key={crumbPath} className="flex shrink-0 items-center gap-1"><ChevronRight size={13} className="text-dim/50" /><button onClick={() => browse(crumbPath)} className={`rounded-md px-2 py-1.5 ${index === crumbs.length - 1 ? 'bg-white/8 text-fg' : 'text-dim hover:text-fg'}`}>{crumb}</button></span>
            })}
          </div>
          <button onClick={load} title="刷新目录" aria-label="刷新目录" className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-line bg-white/4 text-dim hover:text-fg"><RefreshCw size={14} className={loading ? 'animate-spin' : ''} /></button>
          <button onClick={() => openDialog('mkdir')} title="新建目录" aria-label="新建目录" className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-line bg-white/4 text-dim hover:text-fg"><FolderPlus size={14} /></button>
        </div>

        {selectedItems.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-aurora-2/5 px-4 py-2.5">
            <span className="text-xs text-fg">已选 {selectedItems.length} 项</span>
            <button onClick={() => openDialog('download')} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-[11px] text-dim hover:text-fg"><Download size={12} /> 下载</button>
            <button onClick={() => openDialog('copy')} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-[11px] text-dim hover:text-fg"><Copy size={12} /> 复制</button>
            <button onClick={() => openDialog('move')} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-[11px] text-dim hover:text-fg"><Move size={12} /> 移动</button>
            {selectedItems.length === 1 && <button onClick={() => openDialog('rename')} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-[11px] text-dim hover:text-fg"><Pencil size={12} /> 重命名</button>}
            <button onClick={deleteSelected} className="inline-flex items-center gap-1 rounded-md border border-rose-400/30 bg-rose-400/10 px-2.5 py-1.5 text-[11px] text-rose-300"><Trash2 size={12} /> 删除</button>
            <button onClick={() => setSelected(new Set())} title="清除选择" aria-label="清除选择" className="ml-auto grid h-7 w-7 place-items-center rounded-md text-dim hover:text-fg"><X size={13} /></button>
          </div>
        )}

        {showTransfers && (
          <div className="border-b border-line bg-black/10 px-4 py-3">
            <div className="mb-2 flex items-center justify-between"><span className="text-xs font-medium text-fg">传输任务</span><span className="num text-[10px] text-dim">最近 {transfers.length} 条</span></div>
            {transfers.length === 0 ? <div className="py-3 text-center text-xs text-dim">暂无传输任务</div> : <div className="flex max-h-56 flex-col gap-2 overflow-y-auto">
              {transfers.map((job) => (
                <div key={job.id} className="rounded-lg border border-line bg-white/3 px-3 py-2">
                  <div className="flex items-center gap-2 text-xs"><span className={`shrink-0 ${job.status === 'error' ? 'text-rose-300' : job.status === 'done' ? 'text-teal-300' : 'text-aurora-1'}`}>{job.status === 'running' ? <LoaderCircle size={13} className="animate-spin" /> : job.status === 'done' ? <Check size={13} /> : <span className="inline-block w-3 text-center">·</span>}</span><span className="min-w-0 flex-1 truncate text-fg">{job.label}</span><span className="num shrink-0 text-[10px] text-dim">{transferStatus(job)}</span>{job.status === 'running' && <button onClick={async () => { const result = await cancelRcloneTransfer(job.id); if (!result.ok) toast(result.detail, 'bad') }} title="取消任务" aria-label="取消任务" className="grid h-6 w-6 place-items-center text-dim hover:text-rose-300"><X size={12} /></button>}</div>
                  {job.status === 'running' && <div className="mt-1.5 flex items-center gap-2"><div className="h-1 flex-1 overflow-hidden rounded-full bg-white/8"><div className="h-full rounded-full grad-bar transition-all" style={{ width: `${job.progress === null ? 18 : Math.max(2, job.progress * 100)}%` }} /></div><span className="num w-20 text-right text-[10px] text-dim">{fmtSpeed(job.speed)}</span></div>}
                  {job.status === 'error' && job.detail && <div className="mt-1 text-[10px] text-rose-300">{job.detail}</div>}
                </div>
              ))}
            </div>}
          </div>
        )}

        {error ? <div className="px-4 py-10"><EmptyState icon={<Folder size={20} />} title={error} hint="可以刷新目录或检查 remote 连接" /></div> : loading && !items.length ? <div className="flex flex-col gap-2 px-4 py-4"><div className="skeleton h-10 rounded-lg" /><div className="skeleton h-10 rounded-lg" /><div className="skeleton h-10 rounded-lg" /></div> : !items.length ? <div className="px-4 py-10"><EmptyState icon={<Folder size={20} />} title="此目录为空" hint="可以上传文件或新建目录" /></div> : <div>
          <div className="grid grid-cols-[auto_minmax(0,1fr)_6rem_10rem_auto] items-center gap-3 border-b border-line px-4 py-2 text-[10px] uppercase tracking-[0.16em] text-dim/70">
            <input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(items.map((item) => item.path)))} aria-label="全选" className="accent-[#a78bfa]" />
            <span>名称</span><span className="hidden sm:block">大小</span><span className="hidden md:block">修改时间</span><span />
          </div>
          <div className="divide-y divide-line/50">
            {items.map((item) => <div key={item.path} className={`grid grid-cols-[auto_minmax(0,1fr)_6rem_10rem_auto] items-center gap-3 px-4 py-2.5 transition-colors hover:bg-white/4 ${selected.has(item.path) ? 'bg-aurora-2/5' : ''}`}>
              <input type="checkbox" checked={selected.has(item.path)} onChange={() => toggle(item)} aria-label={`选择 ${item.name}`} className="accent-[#a78bfa]" />
              <button onDoubleClick={() => item.isDir && browse(item.path)} onClick={() => item.isDir ? browse(item.path) : toggle(item)} className="flex min-w-0 items-center gap-2 text-left">
                {item.isDir ? <Folder size={16} className="shrink-0 text-aurora-1" /> : <File size={16} className="shrink-0 text-dim" />}
                <span className="truncate text-xs text-fg">{item.name}</span>
              </button>
              <span className="hidden truncate text-right text-[11px] text-dim sm:block">{item.isDir ? '目录' : fmtBytes(item.size)}</span>
              <span className="hidden truncate text-[11px] text-dim md:block">{fmtDate(item.modTime)}</span>
              <div className="flex items-center justify-end gap-1">
                <button onClick={() => { setSelected(new Set([item.path])); openDialog('download', [item]) }} title="下载到本地" aria-label="下载到本地" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:bg-white/6 hover:text-fg"><Download size={13} /></button>
                <button onClick={() => { setSelected(new Set([item.path])); openDialog('copy', [item]) }} title="复制" aria-label="复制" className="hidden h-7 w-7 place-items-center rounded-md text-dim hover:bg-white/6 hover:text-fg sm:grid"><Copy size={13} /></button>
                <button onClick={() => { setSelected(new Set([item.path])); openDialog('rename', [item]) }} title="重命名" aria-label="重命名" className="hidden h-7 w-7 place-items-center rounded-md text-dim hover:bg-white/6 hover:text-fg md:grid"><Pencil size={13} /></button>
                <button onClick={() => deleteEntries([item])} title="删除" aria-label="删除" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:bg-rose-400/10 hover:text-rose-300"><Trash2 size={13} /></button>
              </div>
            </div>)}
          </div>
        </div>}
      </div>

      {dialog && <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm" onClick={closeDialog}>
        <div className="panel w-full max-w-md px-5 py-5" onClick={(event) => event.stopPropagation()}>
          <div className="flex items-center justify-between"><span className="text-sm font-medium text-fg">{dialog.kind === 'mkdir' ? '新建目录' : dialog.kind === 'rename' ? '重命名' : dialog.kind === 'download' ? '下载到本地' : dialog.kind === 'copy' ? '复制到网盘' : '移动到网盘'}</span><button onClick={closeDialog} title="关闭" aria-label="关闭" className="text-dim hover:text-fg"><X size={16} /></button></div>
          {dialog.kind === 'mkdir' || dialog.kind === 'rename' ? <div className="mt-4"><label className="block text-xs text-dim">{dialog.kind === 'mkdir' ? '目录名称' : '新名称'}</label><input autoFocus value={dialog.value} onChange={(event) => setDialog({ ...dialog, value: event.target.value })} onKeyDown={(event) => { if (event.key === 'Enter') runDialog() }} className="mt-1.5 w-full rounded-lg border border-line bg-white/4 px-3 py-2.5 text-sm text-fg focus:border-aurora-2/50 focus:outline-none" /></div> : <div className="mt-4 grid gap-3">
            {dialog.kind !== 'download' && <label className="block text-xs text-dim">目标网盘<select value={dialog.targetRemote} onChange={(event) => setDialog({ ...dialog, targetRemote: event.target.value })} className="mt-1.5 w-full rounded-lg border border-line bg-white/4 px-3 py-2.5 text-sm text-fg focus:border-aurora-2/50 focus:outline-none">{remotes.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>}
            <label className="block text-xs text-dim">{dialog.kind === 'download' ? '本地目标目录（相对下载目录）' : '目标目录'}<input value={dialog.targetPath} onChange={(event) => setDialog({ ...dialog, targetPath: event.target.value })} placeholder={dialog.kind === 'download' ? '留空表示下载目录根目录' : '留空表示根目录'} className="mt-1.5 w-full rounded-lg border border-line bg-white/4 px-3 py-2.5 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none" /></label>
            <div className="text-[11px] leading-relaxed text-dim/70">{dialog.entries.length === 1 ? dialog.entries[0].name : `已选择 ${dialog.entries.length} 项`} · 任务将在后台执行，可在传输任务中查看进度。</div>
          </div>}
          <div className="mt-5 flex justify-end gap-2"><button onClick={closeDialog} className="rounded-lg border border-line bg-white/4 px-3.5 py-2 text-xs text-dim hover:text-fg">取消</button><button onClick={runDialog} className="rounded-lg grad-bar px-3.5 py-2 text-xs font-medium text-ink">提交</button></div>
        </div>
      </div>}
    </section>
  )
}
