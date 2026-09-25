import { useEffect, useState } from 'react'
import { ArrowDownToLine, ArrowUpFromLine } from 'lucide-react'
import { useMetrics } from '../../lib/api'
import { fmtRate, Spark } from '../../components/ui'

export function Bandwidth() {
  const { data } = useMetrics()
  const [hist, setHist] = useState<{ in: number; out: number }[]>([])

  useEffect(() => {
    if (data.bandwidth) {
      setHist((h) => [...h.slice(-59), { in: data.bandwidth.in, out: data.bandwidth.out }])
    }
  }, [data.bandwidth])

  const dlNow = data.bandwidth ? data.bandwidth.in : 0
  const ulNow = data.bandwidth ? data.bandwidth.out : 0
  const peak = hist.length ? Math.max(...hist.map((p) => Math.max(p.in, p.out))) : 0
  const inSeries = hist.map((p) => p.in)
  const outSeries = hist.map((p) => p.out)

  return (
    <div className="panel px-5 py-5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.2em] text-dim">实时带宽</span>
        <span className="live-dot bg-teal-400 text-teal-400" />
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4">
        <div>
          <div className="num grad-txt text-2xl font-semibold">{fmtRate(dlNow)}</div>
          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-dim"><ArrowDownToLine size={12} /> 下行</div>
          {inSeries.length > 1 ? <Spark data={inSeries} w={150} h={44} className="mt-2" /> : <div className="mt-2 h-11 text-[11px] text-dim">等待采样…</div>}
        </div>
        <div>
          <div className="num text-2xl font-semibold text-aurora-2">{fmtRate(ulNow)}</div>
          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-dim"><ArrowUpFromLine size={12} /> 上行</div>
          {outSeries.length > 1 ? <Spark data={outSeries} w={150} h={44} className="mt-2" /> : <div className="mt-2 h-11 text-[11px] text-dim">等待采样…</div>}
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between border-t border-line pt-3 text-[11px] text-dim">
        <span>峰值 <span className="num text-teal-300">{fmtRate(peak)}</span></span>
        <span>合计 <span className="num">{fmtRate(dlNow + ulNow)}</span></span>
        <span>数据源 <span className="num text-dim">eth0 /proc/net</span></span>
      </div>
    </div>
  )
}