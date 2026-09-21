import { MonitorPlay, Tv, Smartphone, Laptop, Loader } from 'lucide-react'
import { useMetrics } from '../../lib/api'
import { Tag, Skeleton, EmptyState, STREAM_ZH, CLIENT_ZH } from '../../components/ui'

const clientIcon = { tv: Tv, ios: Smartphone, android: Smartphone, web: Laptop }

export function LiveStreams() {
  const { data, source } = useMetrics(2000)
  return (
    <div className="panel px-5 py-5">
      <div className="text-[11px] uppercase tracking-[0.2em] text-dim">在线播放</div>
      <div className="mt-4 flex flex-col gap-2.5">
        {source === 'mock' ? (
          [1, 2, 3].map((i) => <Skeleton key={i} h="2.9rem" />)
        ) : data.streams.length === 0 ? (
          <EmptyState icon={<MonitorPlay size={20} />} title="暂无在线播放" hint="启动 Jellyfin 后自动显示" />
        ) : data.streams.map((s) => {
          const Icon = clientIcon[s.client as keyof typeof clientIcon] ?? Laptop
          return (
            <div key={s.id} className="card-in flex items-center gap-3 rounded-lg border border-line bg-white/3 px-3 py-2.5">
              <div className="grid h-8 w-8 place-items-center rounded-lg bg-white/5 text-dim">
                {s.status === 'buffering' ? <Loader size={15} className="animate-spin text-amber-300" /> : <Icon size={15} />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-xs text-fg">{s.title}</span>
                  <Tag tone={s.status === 'live' ? 'ok' : 'warn'}>
                    {s.status === 'live' ? <MonitorPlay size={11} /> : <Loader size={11} className="animate-spin" />}
                    {STREAM_ZH[s.status] ?? s.status}
                  </Tag>
                </div>
                <div className="text-[11px] text-dim">{s.source} · {CLIENT_ZH[s.client] ?? s.client}</div>
              </div>
              <div className="num text-right text-[11px] text-teal-300">{s.bitrate} Mb/s</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}