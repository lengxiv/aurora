import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import { Search, LayoutGrid, MonitorPlay, Activity, Settings as SettingsIcon, LogOut, RefreshCw, CornerDownLeft, Upload, Rss, Cloud } from 'lucide-react'

interface Cmd {
  id: string
  label: string
  sub?: string
  icon: LucideIcon
  run: () => void
}

export default function CommandPalette({ onLogout }: { onLogout: () => void }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [idx, setIdx] = useState(0)
  const nav = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); setOpen((o) => !o); setQ(''); setIdx(0)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 10) }, [open])

  const cmds = useMemo<Cmd[]>(() => [
    { id: 'home', label: '资源管理台', sub: '挂载 / 磁力 / 磁盘', icon: LayoutGrid, run: () => nav('/') },
    { id: 'seeding', label: '做种监控', sub: '分享率 / 上传 / 对等方', icon: Upload, run: () => nav('/seeding') },
    { id: 'rss', label: 'RSS 订阅', sub: '订阅源 / 自动下载规则', icon: Rss, run: () => nav('/rss') },
    { id: 'netdisk', label: '网盘', sub: 'remote / 传输', icon: Cloud, run: () => nav('/netdisk') },
    { id: 'player', label: '媒资库', sub: '浏览 / 播放', icon: MonitorPlay, run: () => nav('/player') },
    { id: 'monitor', label: '实时监控大屏', sub: '带宽 / 队列 / 日志', icon: Activity, run: () => nav('/monitor') },
    { id: 'settings', label: '设置', sub: '服务 / 数据源', icon: SettingsIcon, run: () => nav('/settings') },
    { id: 'refresh', label: '刷新当前页', sub: 'reload', icon: RefreshCw, run: () => window.location.reload() },
    { id: 'logout', label: '退出登录', sub: '返回登录页', icon: LogOut, run: onLogout },
  ], [nav, onLogout])

  const filtered = useMemo(() => cmds.filter((c) => !q || c.label.toLowerCase().includes(q.toLowerCase())), [cmds, q])
  useEffect(() => setIdx(0), [q])
  useEffect(() => setIdx((i) => Math.max(0, Math.min(filtered.length - 1, i))), [filtered.length])

  const run = (c: Cmd) => { setOpen(false); c.run() }

  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(filtered.length - 1, i + 1)) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)) }
      else if (e.key === 'Enter' && filtered[idx]) { e.preventDefault(); run(filtered[idx]) }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, filtered, idx]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null
  return (
    <div className="overlay-in fixed inset-0 z-[80] grid place-items-start justify-center bg-black/50 px-4 pt-[12vh] backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false) }}>
      <div className="panel w-[min(560px,100%)] overflow-hidden">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <Search size={16} className="shrink-0 text-dim" />
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索命令…（↑↓ 选择 · Enter 执行）"
            className="w-full bg-transparent text-sm text-fg placeholder:text-dim/60 focus:outline-none" />
        </div>
        <div className="max-h-[52vh] overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-dim">无匹配命令</div>
          ) : filtered.map((c, i) => (
            <div key={c.id} onClick={() => run(c)} onMouseEnter={() => setIdx(i)}
              className={`flex cursor-pointer items-center gap-3 px-4 py-2.5 ${i === idx ? 'bg-white/6' : ''}`}>
              <div className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${i === idx ? 'grad-bar text-ink' : 'border border-line bg-white/4 text-dim'}`}>
                <c.icon size={15} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-fg">{c.label}</div>
                {c.sub && <div className="text-[11px] text-dim">{c.sub}</div>}
              </div>
              {i === idx && <CornerDownLeft size={14} className="shrink-0 text-dim" />}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}