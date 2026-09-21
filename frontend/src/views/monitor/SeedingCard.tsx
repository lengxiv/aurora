import { useEffect, useMemo, useState } from 'react'
import { useMetrics, fetchTorrentPeers, type TorrentPeers } from '../../lib/api'
import { fmtGb, fmtRate, pct, fmtBytes, Skeleton, EmptyState, flagFor, peerIsoCode } from '../../components/ui'
import { Upload, Users } from 'lucide-react'

export function SeedingCard() {
  const { data, source } = useMetrics(2000)
  const [peers, setPeers] = useState<TorrentPeers | null>(null)

  const seeding = useMemo(
    () => data.torrents.filter((t) => t.state === 'seeding' && !!t.hash),
    [data.torrents],
  )
  const totalUp = seeding.reduce((a, t) => a + (t.upGb || 0), 0)
  const totalDown = seeding.reduce((a, t) => a + (t.downGb || 0), 0)
  const upSpeed = seeding.reduce((a, t) => a + (t.upspeed || 0), 0)
  const ratio = totalDown > 0 ? (totalUp / totalDown).toFixed(2) : totalUp > 0 ? '∞' : '—'
  const top = useMemo(() => {
    if (seeding.length === 0) return undefined
    return seeding.reduce((a, b) => ((b.upspeed || 0) > (a.upspeed || 0) ? b : a))
  }, [seeding])

  useEffect(() => {
    if (!top) { setPeers(null); return }
    let on = true
    const tick = async () => {
      const p = await fetchTorrentPeers(top.hash)
      if (on) setPeers(p)
    }
    tick()
    const id = setInterval(tick, 4000)
    return () => { on = false; clearInterval(id) }
  }, [top])

  const pulling = (peers?.peers || []).filter((p) => p.up_speed > 0)

  return (
    <div className="panel px-5 py-5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.2em] text-dim">做种状态</span>
        <span className="num text-xs text-teal-300">{fmtRate(upSpeed)}</span>
      </div>

      {source === 'mock' ? (
        <div className="mt-4 flex flex-col gap-2.5">
          <Skeleton h="2.6rem" /><Skeleton h="2.6rem" />
        </div>
      ) : seeding.length === 0 ? (
        <div className="mt-2">
          <EmptyState icon={<Upload size={20} />} title="暂无做种任务" hint="下载完成的任务会自动进入做种" />
        </div>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white/4 px-2.5 py-1 text-[11px] text-fg">
              <Upload size={11} className="text-aurora-1" /> 做种 <span className="num">{seeding.length}</span>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white/4 px-2.5 py-1 text-[11px] text-fg">
              分享率 <span className="num text-aurora-1">{ratio}</span>
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white/4 px-2.5 py-1 text-[11px] text-fg">
              已传 <span className="num text-teal-300">{fmtGb(totalUp)}</span>
            </span>
          </div>

          <div className="mt-4 flex items-center gap-2 text-[11px] uppercase tracking-wider text-dim">
            <Users size={12} /> 正在被下载
            <span className="num ml-auto normal-case">{top ? top.name.slice(0, 26) : ''}</span>
          </div>
          <div className="mt-2 flex flex-col gap-1.5">
            {pulling.length === 0 ? (
              <div className="py-4 text-center text-xs text-dim">暂无对等方从你这里拉取</div>
            ) : (
              pulling.slice(0, 4).map((p) => (
                <div key={p.ip} className="card-in flex items-center justify-between gap-2 rounded-lg border border-line bg-white/3 px-3 py-1.5 text-xs">
                  <span className="truncate text-fg">{p.client}</span>
                  <span className="shrink-0 text-dim" title={p.country}>
                    {flagFor(p) && <span className="mr-1 text-xs leading-none">{flagFor(p)}</span>}
                    {peerIsoCode(p) || '—'}
                  </span>
                  <span className="num shrink-0 text-dim">{pct(p.progress)}</span>
                  <span className="num shrink-0 text-teal-300">{fmtBytes(p.up_speed)}/s</span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}
