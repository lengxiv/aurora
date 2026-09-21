import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useMetrics, fetchStats, fetchLogs, fetchSettings } from '../lib/api'
import { fmtGb, pct, SourceBadge } from '../components/ui'
import { Activity, Maximize2, Minimize2, GripVertical, Eye, EyeOff, Settings2 } from 'lucide-react'
import { Bandwidth } from './monitor/Bandwidth'
import { MountRack } from './monitor/MountRack'
import { Traffic } from './monitor/Traffic'
import { TorrentCarpet } from './monitor/TorrentCarpet'
import { SeedingCard } from './monitor/SeedingCard'
import { LiveStreams } from './monitor/LiveStreams'

const ORDER_KEY = 'aurora:mon:order'
const VIS_KEY = 'aurora:mon:vis'

function load<T>(k: string, fallback: T): T {
  try { const v = JSON.parse(localStorage.getItem(k) || ''); return v ?? fallback } catch { return fallback }
}

export default function MonitorView() {
  const { data, source, sources } = useMetrics(2000)
  const [fullscreen, setFullscreen] = useState(false)
  const [st, setSt] = useState<{ alerts?: { disk?: boolean; diskWarn?: number } } | null>(null)
  useEffect(() => { fetchSettings().then(setSt) }, [])
  const toggleFull = () => {
    if (!document.fullscreenElement) { document.documentElement.requestFullscreen().catch(() => {}); setFullscreen(true) }
    else { document.exitFullscreen(); setFullscreen(false) }
  }
  // 按 Esc 退出全屏时同步按钮状态
  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onFs)
    return () => document.removeEventListener('fullscreenchange', onFs)
  }, [])

  const CARDS: { id: string; title: string; el: ReactNode }[] = [
    { id: 'bw', title: '实时带宽', el: <Bandwidth /> },
    { id: 'tr', title: '近期流量', el: <Traffic /> },
    { id: 'mt', title: '挂载状态', el: <MountRack /> },
    { id: 'tc', title: '下载队列', el: <TorrentCarpet /> },
    { id: 'sd', title: '做种状态', el: <SeedingCard /> },
    { id: 'ls', title: '在线播放', el: <LiveStreams /> },
    { id: 'dk', title: '中转磁盘', el: (
      <div className="panel px-5 py-5">
        <div className="text-[11px] uppercase tracking-[0.2em] text-dim">中转磁盘</div>
        <div className="mt-3 flex items-end justify-between">
          <div className="num grad-txt text-3xl font-semibold">{data.disk.capGb > 0 ? pct(data.disk.usedGb / data.disk.capGb) : '—'}</div>
          <div className="num text-sm text-dim">{fmtGb(data.disk.usedGb)} / {fmtGb(data.disk.capGb)}</div>
        </div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
          <div className="grad-bar h-full" style={{ width: `${Math.min(100, (data.disk.capGb > 0 ? (data.disk.usedGb / data.disk.capGb) * 100 : 0))}%` }} />
        </div>
        <div className="mt-3 flex items-center gap-2 text-xs text-dim">
          <Activity size={13} /> 可用 <span className="num text-teal-300">{fmtGb(data.disk.capGb - data.disk.usedGb)}</span>
        </div>
      </div>
    ) },
    { id: 'st', title: '近 14 天流量 / 磁盘', el: <Report /> },
    { id: 'lg', title: '最近活动', el: <LogsCard /> },
  ]

  // 新卡片（如做种状态）自动追加到已保存布局末尾，老布局不丢
  const [order, setOrder] = useState<string[]>(() => {
    const saved = load<string[]>(ORDER_KEY, CARDS.map((c) => c.id))
    const missing = CARDS.map((c) => c.id).filter((id) => !saved.includes(id))
    return [...saved, ...missing]
  })
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(load<string[]>(VIS_KEY, [])))
  const [manage, setManage] = useState(false)

  const persist = (o: string[], h: Set<string>) => {
    localStorage.setItem(ORDER_KEY, JSON.stringify(o))
    localStorage.setItem(VIS_KEY, JSON.stringify([...h]))
  }
  const setOrderP = (o: string[]) => { setOrder(o); persist(o, hidden) }
  const setHiddenP = (h: Set<string>) => { setHidden(h); persist(order, h) }

  const vis = useMemo(() => order.filter((id) => !hidden.has(id)), [order, hidden])

  const dragIdx = useState<number | null>(null)
  const onDrop = (to: number) => {
    const from = dragIdx[0]
    dragIdx[1](null)
    if (from === null || from === to) return
    const o = [...vis]
    const [m] = o.splice(from, 1)
    o.splice(to, 0, m)
    setOrderP(o)
  }

  const diskPct = data.disk.capGb ? (data.disk.usedGb / data.disk.capGb) * 100 : 0

  return (
    <div className="min-w-0 px-4 py-6 md:px-8 md:py-7">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-[0.3em] text-dim">实时</div>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">实时监控大屏</h1>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="flex items-center gap-3">
            <SourceBadge sources={sources} />
            <button onClick={() => setManage((m) => !m)} title="卡片管理"
              className={`grid h-8 w-8 place-items-center rounded-lg border ${manage ? 'border-aurora-2/50 text-aurora-1' : 'border-line bg-white/4 text-dim hover:text-fg'}`}><Settings2 size={15} /></button>
            <button onClick={toggleFull} aria-label="全屏"
              className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white/4 text-dim hover:text-fg">
              {fullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-dim">数据源 · <span className="num">{source === 'live' ? '后端实时' : '后端未连接'}</span></span>
          </div>
        </div>
      </header>

      {st?.alerts?.disk && diskPct >= (st.alerts.diskWarn ?? 90) && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-rose-400/30 bg-rose-400/10 px-4 py-2.5 text-sm text-rose-300">
          <Activity size={15} /> 磁盘即将占满：{diskPct.toFixed(0)}%（{fmtGb(data.disk.usedGb)}/{fmtGb(data.disk.capGb)}）
        </div>
      )}

      {manage && (
        <div className="panel mt-4 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-dim">卡片显示：</span>
            {CARDS.map((c) => {
              const on = !hidden.has(c.id)
              return (
                <button key={c.id} onClick={() => { const h = new Set(hidden); on ? h.add(c.id) : h.delete(c.id); setHiddenP(h) }}
                  className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs ${on ? 'border-aurora-2/50 text-aurora-1' : 'border-line bg-white/4 text-dim'}`}>
                  {on ? <Eye size={11} /> : <EyeOff size={11} />}{c.title}
                </button>
              )
            })}
          </div>
        </div>
      )}

      <div className="mt-6 grid grid-cols-12 gap-4">
        {vis.map((id, i) => {
          const c = CARDS.find((x) => x.id === id)
          if (!c) return null
          return (
            <div key={id} draggable onDragStart={() => dragIdx[1](i)} onDragOver={(e) => e.preventDefault()} onDrop={() => onDrop(i)}
              className="col-span-12 xl:col-span-4">
              <div className="relative">
                <div className="absolute right-2 top-2 z-10 flex items-center gap-1 text-dim">
                  <span className="cursor-grab rounded-md bg-white/4 p-1.5 hover:text-fg" title="拖动排序"><GripVertical size={13} /></span>
                </div>
                {c.el}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function LogsCard() {
  const [logs, setLogs] = useState<{ ts: number; event: string; detail: string }[]>([])
  const [nowTs, setNowTs] = useState(Date.now())
  useEffect(() => {
    let id: ReturnType<typeof setInterval>
    const load = () => fetchLogs().then(setLogs)
    load(); id = setInterval(load, 5000)
    const t = setInterval(() => setNowTs(Date.now()), 1000)
    return () => { clearInterval(id); clearInterval(t) }
  }, [])
  const EV: Record<string, string> = { 'torrent.add': '添加磁力', 'torrent.remove': '删除磁力', 'torrent.pause': '暂停磁力', 'torrent.resume': '恢复磁力', 'torrent.done': '下载完成', 'torrent.error': '下载失败', 'disk.warn': '磁盘告警' }
  const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return (
    <div className="panel px-5 py-5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.2em] text-dim">最近活动</span>
        <span className="num text-[11px] text-dim">{new Date(nowTs).toLocaleTimeString('zh-CN', { hour12: false })}</span>
      </div>
      <div className="mt-3 flex flex-col gap-1.5">
        {logs.length === 0
          ? <div className="py-6 text-center text-xs text-dim">暂无操作日志</div>
          : logs.map((l, i) => (
              <div key={i} className="flex items-center gap-3 border-b border-line/40 pb-1.5 text-xs">
                <span className="num shrink-0 text-dim">{fmtTs(l.ts)}</span>
                <span className="num shrink-0 text-fg">{EV[l.event] ?? l.event}</span>
                <span className="ml-auto truncate pl-2 text-dim">{l.detail}</span>
              </div>
            ))}
      </div>
    </div>
  )
}

function Report() {
  const [days, setDays] = useState<{ date: string; in_gb: number; out_gb: number; disk_gb: number }[]>([])
  useEffect(() => { fetchStats().then(setDays) }, [])
  const maxTraffic = Math.max(...days.map((d) => Math.max(d.in_gb, d.out_gb)), 1)
  const maxDisk = Math.max(...days.map((d) => d.disk_gb), 1)
  const totalIn = days.reduce((a, d) => a + d.in_gb, 0)
  const totalOut = days.reduce((a, d) => a + d.out_gb, 0)
  return (
    <div className="panel px-5 py-5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] uppercase tracking-[0.2em] text-dim">流量 / 磁盘</span>
        <span className="num text-[11px] text-dim">{days.length} 天记录</span>
      </div>
      {days.length === 0 ? (
        <div className="py-8 text-center text-xs text-dim">统计数据积累中…</div>
      ) : (
        <div className="mt-4 flex flex-col gap-3">
          <div className="flex h-16 items-end gap-px">
            {days.map((d, i) => (
              <div key={i} className="flex flex-1 flex-col justify-end gap-px"
                title={`${d.date} 入 ${d.in_gb.toFixed(1)}G / 出 ${d.out_gb.toFixed(1)}G`}>
                <div className="w-full rounded-sm bg-aurora-2/80 transition-all"
                  style={{ height: `${Math.max(2, (d.in_gb / maxTraffic) * 100)}%` }} />
                <div className="w-full rounded-sm bg-aurora-1/60 transition-all"
                  style={{ height: `${Math.max(2, (d.out_gb / maxTraffic) * 100)}%` }} />
              </div>
            ))}
          </div>
          <div className="flex h-8 items-end gap-px">
            {days.map((d, i) => (
              <div key={i} className="flex-1 rounded-sm bg-aurora-1/50 transition-all"
                style={{ height: `${Math.max(4, (d.disk_gb / maxDisk) * 100)}%` }}
                title={`${d.date} 磁盘 ${d.disk_gb.toFixed(1)}G`} />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-dim">
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-aurora-2" />入</span>
            <span className="num text-teal-300">{totalIn.toFixed(1)}G</span>
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-aurora-1" />出</span>
            <span className="num text-aurora-2">{totalOut.toFixed(1)}G</span>
            <span className="text-dim/40">|</span>
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-aurora-1/50" />磁盘</span>
            <span className="num">{days[days.length - 1]?.disk_gb.toFixed(1)}G</span>
            <span className="num ml-auto">{days[days.length - 1]?.date}</span>
          </div>
        </div>
      )}
    </div>
  )
}