import { useMetrics } from '../../lib/api'
import { fmtRate, pct, Skeleton, EmptyState, STATE_ZH } from '../../components/ui'
import { ArrowDownToLine } from 'lucide-react'

export function TorrentCarpet() {
  const { data, source } = useMetrics()
  const dl = data.torrents.filter((t) => t.state === 'downloading')
  const speed = dl.reduce((a, t) => a + t.speed, 0)
  return (
    <div className="panel px-5 py-5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.2em] text-dim">下载队列</span>
        <span className="num text-xs text-teal-300">{fmtRate(speed)}</span>
      </div>
      <div className="mt-4 flex flex-col gap-2.5">
        {source === 'mock' ? (
          [1, 2, 3].map((i) => <Skeleton key={i} h="2.6rem" />)
        ) : data.torrents.length === 0 ? (
          <EmptyState icon={<ArrowDownToLine size={20} />} title="暂无磁力任务" hint="添加磁力或启动 qBittorrent" />
        ) : data.torrents.map((t) => (
          <div key={t.id} className="card-in rounded-lg border border-line bg-white/3 px-3 py-2">
            <div className="flex items-center justify-between">
              <span className="truncate text-xs text-fg">{t.name}</span>
              <span className="num shrink-0 pl-2 text-[11px] text-teal-300">{t.state === 'downloading' ? fmtRate(t.speed) : (STATE_ZH[t.state] ?? t.state)}</span>
            </div>
            <div className="mt-1.5 flex items-center gap-2">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-white/6">
                <div className={`h-full rounded-full ${t.state === 'error' ? 'bg-rose-400' : 'grad-bar'}`} style={{ width: `${Math.min(100, t.progress * 100)}%` }} />
              </div>
              <span className="num w-10 text-right text-[11px] text-dim">{pct(t.progress)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}