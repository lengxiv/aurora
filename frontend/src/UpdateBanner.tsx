import { useEffect, useState } from 'react'
import { ArrowUpCircle, ChevronDown, ChevronUp, Copy, X } from 'lucide-react'
import { checkUpdate, type UpdateStatus } from './lib/api'
import { useToast } from './toast'

// 按版本号记忆关闭状态：同版本不再打扰，新版本出现才会再次提醒
const dismissKey = (tag: string) => `aurora-update-dismissed-${tag}`

function deployBlocks(version: string, mode: string): { label: string; cmd: string }[] {
  const docker = [
    'cd /opt/aurora',
    `# 先在 .env 中设置 AURORA_IMAGE_TAG=${version}`,
    'docker compose pull aurora',
    'docker compose up -d aurora',
  ].join('\n')
  const systemd = [
    'cd /opt/aurora',
    'git pull',
    'backend/.venv/bin/pip install -r backend/requirements.txt',
    'npm --prefix frontend ci && npm --prefix frontend run build',
    'systemctl restart aurora',
  ].join('\n')
  // 与当前部署方式匹配的排在前面
  return mode === 'docker'
    ? [{ label: 'Docker Compose 部署', cmd: docker }, { label: 'systemd 部署', cmd: systemd }]
    : [{ label: 'systemd 部署', cmd: systemd }, { label: 'Docker Compose 部署', cmd: docker }]
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // 非 HTTPS 环境回退 execCommand
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

export default function UpdateBanner() {
  const toast = useToast()
  const [update, setUpdate] = useState<UpdateStatus | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const [expanded, setExpanded] = useState(false)

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

  const version = update.latest || update.tag || ''
  const blocks = deployBlocks(version, update.deploy || 'systemd')

  return (
    <div className="border-b border-teal-400/25 bg-teal-400/10 text-xs text-teal-100">
      <div className="flex items-center justify-between gap-3 px-4 py-2 md:px-8">
        <div className="flex min-w-0 items-center gap-2">
          <ArrowUpCircle size={14} className="shrink-0" />
          <span className="truncate">
            发现新版本 <span className="num font-medium">{version}</span>
            （当前 {update.current || '—'}）。
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button type="button" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded} className="inline-flex items-center gap-1 rounded-md border border-teal-300/30 bg-teal-400/10 px-2 py-1 text-[11px] text-teal-200 hover:bg-teal-400/20">
            升级方法 {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
          {update.url && (
            <a href={update.url} target="_blank" rel="noreferrer" className="rounded-md border border-teal-300/30 bg-teal-400/10 px-2 py-1 text-[11px] text-teal-200 hover:bg-teal-400/20">
              发布说明
            </a>
          )}
          <button type="button" onClick={() => { localStorage.setItem(dismissKey(update.tag || ''), '1'); setDismissed(true) }} aria-label="关闭更新提示" title="本版本内不再提醒" className="grid h-6 w-6 place-items-center rounded-md text-teal-200/70 hover:text-fg">
            <X size={13} />
          </button>
        </div>
      </div>
      {expanded && (
        <div className="flex flex-col gap-3 px-4 pb-3 md:px-8">
          {blocks.map((block, index) => (
            <div key={block.label} className="rounded-lg border border-teal-300/20 bg-ink-2/60 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium text-fg">
                  {block.label}
                  {index === 0 && <span className="ml-1.5 text-[10px] text-teal-300">· 与当前部署方式匹配</span>}
                </span>
                <button type="button" onClick={async () => { const ok = await copyText(block.cmd); toast(ok ? '升级命令已复制' : '复制失败，请手动选择复制', ok ? 'ok' : 'bad') }} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2 py-1 text-[11px] text-dim hover:text-fg">
                  <Copy size={12} /> 复制
                </button>
              </div>
              <pre className="num mt-2 overflow-x-auto whitespace-pre text-[11px] leading-relaxed text-teal-100">{block.cmd}</pre>
            </div>
          ))}
          <div className="text-[11px] text-dim/80">升级不影响数据：运行状态在卷里，媒体在下载目录。完整步骤见 docs/DEPLOYMENT.md 第 13.4 节。</div>
        </div>
      )}
    </div>
  )
}
