import { useEffect, useState } from 'react'
import { Upload, HardDriveDownload, Users, RefreshCw, ChevronDown, Wifi, WifiOff } from 'lucide-react'
import { useMetrics, fetchTorrentPeers, type TorrentPeers } from '../lib/api'
import { StatCard, Tag, fmtGb, fmtRate, pct, fmtBytes, SourceBadge, STATE_ZH, EmptyState, flagFor, countryZh } from '../components/ui'

const stateTone: Record<string, 'ok' | 'warn' | 'bad' | 'muted'> = {
  downloading: 'ok', stalled: 'warn', seeding: 'warn', queued: 'muted', error: 'bad', done: 'muted', paused: 'warn', unknown: 'bad',
}

export default function SeedingView() {
  const { data, source, sources } = useMetrics()
  const [selHash, setSelHash] = useState<string | null>(null)
  const [peers, setPeers] = useState<TorrentPeers | null>(null)

  const seeding = data.torrents.filter((t) => t.state === 'seeding' && !!t.hash)
  const totalUp = seeding.reduce((a, t) => a + (t.upGb || 0), 0)
  const totalDown = seeding.reduce((a, t) => a + (t.downGb || 0), 0)
  const upSpeed = seeding.reduce((a, t) => a + (t.upspeed || 0), 0)
  const totalConns = seeding.reduce((a, t) => a + (t.conns || 0), 0)
  const activeCnt = seeding.filter((t) => (t.upspeed || 0) > 0 || (t.leechers || 0) > 0).length
  const ratio = totalDown > 0 ? (totalUp / totalDown).toFixed(2) : totalUp > 0 ? '∞' : '—'

  // 默认选中上传最快的做种任务，方便直接看到谁在下载
  useEffect(() => {
    if (selHash) return
    if (seeding.length === 0) return
    const top = seeding.reduce((a, b) => ((b.upspeed || 0) > (a.upspeed || 0) ? b : a))
    setSelHash(top.hash)
  }, [seeding, selHash])

  useEffect(() => {
    if (!selHash) { setPeers(null); return }
    let on = true
    const tick = async () => {
      const p = await fetchTorrentPeers(selHash)
      if (on) setPeers(p)
    }
    tick()
    const id = setInterval(tick, 4000)
    return () => { on = false; clearInterval(id) }
  }, [selHash])

  const sel = data.torrents.find((t) => t.hash === selHash) || null
  const qbitOff = (sources.torrents || '') !== 'qbittorrent'

  return (
    <div className="min-w-0 px-4 py-6 md:px-10 md:py-8">
      {source === 'mock' && (
        <div className="mb-4 rounded-lg border border-amber-400/30 bg-amber-400/10 px-4 py-2.5 text-xs text-amber-200">
          后端未连接，当前显示空态演示。请检查 Aurora 服务是否运行。
        </div>
      )}
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.3em] text-dim">上传 / 对等方</div>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">做种监控</h1>
        </div>
        <SourceBadge sources={sources} />
      </header>

      {/* stat row */}
      <section className="mt-8 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard label="做种任务" value={`${seeding.length} 个`} accent
          sub={<span className="inline-flex items-center gap-1.5"><span className="live-dot bg-teal-400 text-teal-400" /> {activeCnt} 个正被拉取</span>} />
        <StatCard label="实时上传" value={fmtRate(upSpeed)} accent
          sub={<span className="num text-dim">{totalConns} 个对等方连接</span>} />
        <StatCard label="累计上传" value={fmtGb(totalUp)}
          sub={<span className="num">下载 {fmtGb(totalDown)}</span>} />
        <StatCard label="总分享率" value={ratio}
          sub={<span className="num text-dim">上传 / 下载</span>} />
      </section>

      {/* seeding list */}
      <section className="panel mt-6 px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-medium text-fg"><Upload size={16} className="text-aurora-1" /> 做种列表</h2>
          <span className="text-xs text-dim">每 4 秒自动刷新</span>
        </div>

        {qbitOff ? (
          <EmptyState icon={<WifiOff size={18} />} title="qBittorrent 未接入" hint="启动 qBittorrent 后这里会显示真实做种数据" />
        ) : seeding.length === 0 ? (
          <EmptyState icon={<Upload size={18} />} title="暂无做种任务" hint="下载完成的任务会自动进入做种" />
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-dim">
                  <th className="pb-3 pr-4 font-normal">任务</th>
                  <th className="pb-3 pr-4 font-normal">状态</th>
                  <th className="pb-3 pr-4 text-right font-normal">分享率</th>
                  <th className="pb-3 pr-4 text-right font-normal">已上传</th>
                  <th className="pb-3 pr-4 text-right font-normal">上传速率</th>
                  <th className="pb-3 pr-4 text-right font-normal">连接</th>
                  <th className="pb-3 pr-4 text-right font-normal">S/L</th>
                  <th className="pb-3 text-right font-normal" />
                </tr>
              </thead>
              <tbody>
                {seeding.map((t) => {
                  const open = selHash === t.hash
                  return (
                    <tr key={t.id} onClick={() => setSelHash(open ? null : t.hash)}
                      className={`cursor-pointer border-t border-line/60 transition-colors ${open ? 'bg-white/4' : 'group hover:bg-white/2'}`}>
                      <td className="py-3.5 pr-4">
                        <div className="flex items-center gap-3">
                          <div className={`grid h-8 w-8 place-items-center rounded-lg transition-colors ${(t.upspeed || 0) > 0 ? 'grad-bar text-ink' : 'bg-white/5 text-dim'}`}>
                            <HardDriveDownload size={15} />
                          </div>
                          <div>
                            <div className="num max-w-[340px] truncate text-fg">{t.name}</div>
                            <div className="text-[11px] text-dim">{fmtGb(t.sizeGb)}</div>
                          </div>
                        </div>
                      </td>
                      <td className="py-3.5 pr-4 whitespace-nowrap"><Tag tone={stateTone[t.state]}>{STATE_ZH[t.state] ?? t.state}</Tag></td>
                      <td className="py-3.5 pr-4 text-right num text-fg">{t.ratio > 0 ? t.ratio.toFixed(2) : '—'}</td>
                      <td className="py-3.5 pr-4 text-right num text-teal-300">{fmtGb(t.upGb)}</td>
                      <td className="py-3.5 pr-4 text-right num text-teal-300">{t.upspeed > 0 ? fmtRate(t.upspeed) : '—'}</td>
                      <td className="py-3.5 pr-4 text-right num text-dim">{t.conns}</td>
                      <td className="py-3.5 pr-4 text-right num text-dim">{t.seeders} / {t.leechers}</td>
                      <td className="py-3.5 text-right">
                        <ChevronDown size={15} className={`ml-auto text-dim transition-transform ${open ? 'rotate-180 text-aurora-1' : ''}`} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* peers */}
      <section className="panel mt-6 px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium text-fg">
            <Users size={16} className="shrink-0 text-aurora-1" /> <span className="shrink-0">对等方</span>
            {sel && <span className="num min-w-0 flex-1 truncate text-xs font-normal text-dim">· {sel.name}</span>}
          </h2>
          {peers && (
            <div className="flex items-center gap-3 text-[11px] text-dim">
              <span>已连接 <span className="num text-fg">{peers.connected}</span></span>
              <span>做种者 <span className="num text-fg">{peers.seeds}</span></span>
              <span>下载者 <span className="num text-fg">{peers.leechers}</span></span>
            </div>
          )}
        </div>

        {!sel ? (
          <div className="py-6 text-center text-sm text-dim">选择一个做种任务查看对等方</div>
        ) : !peers ? (
          <div className="py-6 text-center text-sm text-dim">正在读取对等方…</div>
        ) : peers.peers.length === 0 ? (
          <EmptyState icon={<Wifi size={18} />} title="当前无对等方连接" hint="下载者会优先连其他做种者，连到你的才会显示在这里" />
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-dim">
                  <th className="pb-3 pr-4 font-normal">客户端</th>
                  <th className="pb-3 pr-4 font-normal">IP</th>
                  <th className="pb-3 pr-4 font-normal">地区</th>
                  <th className="pb-3 pr-4 text-right font-normal">进度</th>
                  <th className="pb-3 pr-4 text-right font-normal">从你拉取</th>
                  <th className="pb-3 text-right font-normal">累计从你下载</th>
                </tr>
              </thead>
              <tbody>
                {peers.peers.map((p) => (
                  <tr key={p.ip} className="border-t border-line/60">
                    <td className="py-3 pr-4 text-fg">{p.client}</td>
                    <td className="py-3 pr-4 num text-dim">{p.ip}</td>
                    <td className="py-3 pr-4">
                      <span className="inline-flex items-center gap-1.5 text-dim" title={p.country || countryZh(p)}>
                        {flagFor(p) && <span className="text-sm leading-none">{flagFor(p)}</span>}
                        {countryZh(p) || '—'}
                      </span>
                    </td>
                    <td className="py-3 pr-4 text-right num text-dim">{pct(p.progress)}</td>
                    <td className="py-3 pr-4 text-right num text-teal-300">{p.up_speed > 0 ? `${fmtBytes(p.up_speed)}/s` : '—'}</td>
                    <td className="py-3 text-right num text-teal-300">{p.uploaded > 0 ? fmtBytes(p.uploaded) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <footer className="mt-4 flex items-center gap-1.5 text-[11px] text-dim/60">
        <RefreshCw size={11} /> 数据来自 qBittorrent WebAPI 实时轮询，来源以徽标为准
      </footer>
    </div>
  )
}
