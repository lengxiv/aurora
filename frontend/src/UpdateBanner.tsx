import { useEffect, useState } from 'react'
import { ArrowUpCircle, X } from 'lucide-react'
import { checkUpdate, type UpdateStatus } from './lib/api'

// 按版本号记忆关闭状态：同版本不再打扰，新版本出现才会再次提醒
const dismissKey = (tag: string) => `aurora-update-dismissed-${tag}`

export default function UpdateBanner() {
  const [update, setUpdate] = useState<UpdateStatus | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    // 后端对 GitHub 结果缓存 30 分钟，整个会话只产生一次真实外呼
    checkUpdate().then((r) => {
      if (r?.ok && r.update_available && r.tag) {
        setUpdate(r)
        setDismissed(localStorage.getItem(dismissKey(r.tag)) === '1')
      }
    })
  }, [])

  if (!update?.update_available || dismissed) return null

  return (
    <div className="flex items-center justify-between gap-3 border-b border-teal-400/25 bg-teal-400/10 px-4 py-2 text-xs md:px-8">
      <div className="flex min-w-0 items-center gap-2 text-teal-100">
        <ArrowUpCircle size={14} className="shrink-0" />
        <span className="truncate">
          发现新版本 <span className="num font-medium">{update.latest || update.tag}</span>
          （当前 {update.current || '—'}），已发布新镜像与更新说明。
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {update.url && (
          <a href={update.url} target="_blank" rel="noreferrer" className="rounded-md border border-teal-300/30 bg-teal-400/10 px-2 py-1 text-[11px] text-teal-200 hover:bg-teal-400/20">
            查看发布说明
          </a>
        )}
        <button type="button" onClick={() => { localStorage.setItem(dismissKey(update.tag || ''), '1'); setDismissed(true) }} aria-label="关闭更新提示" title="本版本内不再提醒" className="grid h-6 w-6 place-items-center rounded-md text-teal-200/70 hover:text-fg">
          <X size={13} />
        </button>
      </div>
    </div>
  )
}
