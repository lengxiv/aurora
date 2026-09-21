import { Cloud, CloudOff, AlertTriangle } from 'lucide-react'
import { useMetrics } from '../../lib/api'
import { Bar, Tag, Skeleton, EmptyState, STATUS_ZH, fmtMountReads, MountLatency } from '../../components/ui'

export function MountRack() {
  const { data, source } = useMetrics(2000)
  return (
    <div className="panel px-5 py-5">
      <div className="text-[11px] uppercase tracking-[0.2em] text-dim">挂载状态</div>
      <div className="mt-4 flex flex-col gap-3">
        {source === 'mock' ? (
          [1, 2, 3].map((i) => <Skeleton key={i} h="3.1rem" />)
        ) : data.mounts.length === 0 ? (
          <EmptyState icon={<Cloud size={20} />} title="未接入挂载" hint="启动 rclone 后自动显示" />
        ) : data.mounts.map((m) => (
          <div key={m.id} className="card-in rounded-xl border border-line bg-white/3 px-4 py-3">
            <div className="flex items-center gap-3">
              <div className={`grid h-9 w-9 place-items-center rounded-lg ${m.status === 'offline' ? 'bg-rose-500/15 text-rose-300' : 'grad-bar text-ink'}`}>
                {m.status === 'offline' ? <CloudOff size={17} /> : m.status === 'degraded' ? <AlertTriangle size={17} /> : <Cloud size={17} />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="num text-sm text-fg">{m.name}</span>
                  <Tag tone={m.status === 'online' ? 'ok' : m.status === 'degraded' ? 'warn' : 'bad'}>{STATUS_ZH[m.status] ?? m.status}</Tag>
                </div>
                <div className="text-[11px] text-dim">{m.driver} · {m.provider}</div>
              </div>
              <div className="text-right">
                <div className="num text-xs text-teal-300">{fmtMountReads(m)}</div>
                <MountLatency m={m} />
              </div>
            </div>
            <div className="mt-2"><Bar p={m.capGb > 0 ? m.usedGb / m.capGb : 0} /></div>
          </div>
        ))}
      </div>
    </div>
  )
}