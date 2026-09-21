import { useEffect, useState } from 'react'
import { useMetrics } from '../../lib/api'
import { fmtRate } from '../../components/ui'

const MAX_POINTS = 12

export function Traffic() {
  const { data } = useMetrics(2000)
  const [hist, setHist] = useState<{ in: number; out: number }[]>([])
  useEffect(() => {
    if (data.bandwidth) {
      setHist((h) => [...h.slice(-(MAX_POINTS - 1)), { in: data.bandwidth.in, out: data.bandwidth.out }])
    }
  }, [data.bandwidth])

  const dlIn = data.bandwidth ? data.bandwidth.in : 0
  const ulOut = data.bandwidth ? data.bandwidth.out : 0
  const max = Math.max(...hist.map((p) => Math.max(p.in, p.out)), 1)

  return (
    <div className="panel px-5 py-5">
      <div className="text-[11px] uppercase tracking-[0.2em] text-dim">近期实时流量</div>
      <div className="mt-4 flex h-20 items-end gap-px">
        {hist.length === 0 ? (
          <div className="w-full text-center text-[11px] text-dim pt-6">等待采样…</div>
        ) : (
          hist.map((p, i) => (
            <div key={i} className="flex flex-1 flex-col justify-end gap-px"
              title={`${p.in.toFixed(1)} Mb/s 下 / ${p.out.toFixed(1)} Mb/s 上`}>
              <div className="w-full rounded-sm bg-aurora-2/80 transition-all"
                style={{ height: `${(p.in / max) * 100}%` }} />
              <div className="w-full rounded-sm bg-aurora-1/60 transition-all"
                style={{ height: `${(p.out / max) * 100}%` }} />
            </div>
          ))
        )}
      </div>
      <div className="mt-3 flex items-center gap-3 text-[11px] text-dim">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-aurora-2" />下行</span>
        <span className="num text-teal-300">{fmtRate(dlIn)}</span>
        <span className="text-dim/40">|</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-aurora-1" />上行</span>
        <span className="num text-aurora-2">{fmtRate(ulOut)}</span>
        <span className="ml-auto text-dim/50">{hist.length > 0 ? `峰值 ${fmtRate(max)}` : ''}</span>
      </div>
    </div>
  )
}