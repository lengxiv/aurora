import { useCallback, useEffect, useRef, useState, type PointerEvent as RPE } from 'react'
import { FolderOpen, Search, Play, Pause, Film, Music4, Subtitles, Trash2, Pencil, X, File, Image as ImageIcon, ChevronLeft, ChevronRight, Maximize, GripHorizontal, Copy, Loader, LayoutGrid, List as ListIcon, Check, FolderInput, MonitorPlay, WifiOff, FolderPlus, RotateCcw, PictureInPicture, SkipBack, SkipForward } from 'lucide-react'
import { fetchMedia, mediaStreamUrl, mediaSubtitleUrl, mediaThumbUrl, deleteMedia, renameMedia, moveMedia, moveSeedMedia, fetchDirs, mkdirMedia, rmdirMedia, fetchJellyfinLibrary, jellyfinImageUrl, jellyfinStreamUrl, fetchTrash, restoreTrash, purgeTrash, type TrashItem, type MediaFile, type JellyfinItem, type MediaDir } from '../lib/api'
import { Skeleton, EmptyState } from '../components/ui'
import { useToast } from '../toast'

const KIND_LABEL: Record<string, string> = { video: '视频', audio: '音频', sub: '字幕', image: '图片', file: '文件' }
const KIND_ICON: Record<string, typeof Film> = { video: Film, audio: Music4, sub: Subtitles, image: ImageIcon, file: File }
const FILTERS = ['all', 'video', 'audio', 'sub', 'image', 'file'] as const
const PROGRESS_PREFIX = 'aurora:prog:'

function progressKey(kind: 'local' | 'jellyfin', id: string) {
  return `${PROGRESS_PREFIX}${kind}:${id}`
}

function restoreProgress(el: HTMLMediaElement, key: string) {
  const saved = Number(localStorage.getItem(key) || 0)
  if (saved > 10 && Number.isFinite(el.duration) && el.duration > 0 && saved < el.duration - 5) {
    el.currentTime = saved
  } else if (saved >= el.duration - 5 && saved > 10) {
    localStorage.removeItem(key)
  }
}

function subtitleStem(name: string) {
  return name.replace(/\.(srt|ass|ssa|vtt)$/i, '').toLowerCase()
}

function subtitleLabel(name: string) {
  const stem = subtitleStem(name)
  const suffix = stem.split('.').pop() || ''
  const labels: Record<string, string> = {
    zh: '中文', zho: '中文', chi: '中文', chs: '简中', sc: '简中',
    cht: '繁中', tc: '繁中', en: 'English', eng: 'English',
  }
  return labels[suffix] || suffix || '字幕'
}

function fmtSize(b: number) {
  if (b >= 1073741824) return `${(b / 1073741824).toFixed(2)} GB`
  if (b >= 1048576) return `${(b / 1048576).toFixed(1)} MB`
  if (b >= 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${b} B`
}

export default function PlayerView() {
  const toast = useToast()
  const [base, setBase] = useState('')
  const [files, setFiles] = useState<MediaFile[]>([])
  const [dir, setDir] = useState('')
  const [kind, setKind] = useState<string>('all')
  const [q, setQ] = useState('')
  const [playPath, setPlayPath] = useState<string | null>(null)
  const [img, setImg] = useState<MediaFile | null>(null)
  const [view, setView] = useState<'list' | 'grid'>('list')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [slide, setSlide] = useState(false)
  const [loading, setLoading] = useState(true)
  const [dirsList, setDirsList] = useState<MediaDir[]>([])
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const toggleFold = (path: string) => setCollapsed((s) => {
    const n = new Set(s)
    if (n.has(path)) n.delete(path); else n.add(path)
    return n
  })
  const [moveOpen, setMoveOpen] = useState(false)
  const [moveDest, setMoveDest] = useState('')
  const [moveQuery, setMoveQuery] = useState('')
  const [mode, setMode] = useState<'files' | 'wall' | 'trash'>('files')
  const [trash, setTrash] = useState<TrashItem[]>([])
  const [jf, setJf] = useState<{ online: boolean; items: JellyfinItem[] } | null>(null)
  const [jfPlay, setJfPlay] = useState<JellyfinItem | null>(null)
  const [jfState, setJfState] = useState<'load' | 'ok' | 'err'>('load')
  const [rate, setRate] = useState(1)
  const [retry, setRetry] = useState(0)
  const mediaRef = useRef<HTMLMediaElement | null>(null)

  useEffect(() => { setJfState('load'); setRetry(0) }, [jfPlay])
  useEffect(() => { if (mode === 'trash') fetchTrash().then(setTrash) }, [mode])

  const load = useCallback(async () => {
    const [d, dd] = await Promise.all([fetchMedia(), fetchDirs()])
    if (d) { setBase(d.base); setFiles(d.files || []) }
    if (dd) setDirsList(dd)
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (mode !== 'wall') return
    let on = true
    const tick = async () => {
      const d = await fetchJellyfinLibrary()
      if (on && d) setJf(d)
    }
    tick()
    const id = setInterval(tick, 30000)
    return () => { on = false; clearInterval(id) }
  }, [mode])

  const qn = q.trim().toLowerCase()
  const shown = files.filter((f) => (!dir || f.dir === dir || f.dir.startsWith(dir + '/')) && (kind === 'all' || f.kind === kind) && (!qn || f.name.toLowerCase().includes(qn)))
  const playFile = playPath ? files.find((f) => f.path === playPath) : null
  const isVideo = !!playFile && playFile.kind === 'video'
  const playables = shown.filter((f) => f.kind === 'video' || f.kind === 'audio')
  const subtitleFiles = playFile && isVideo
    ? files.filter((f) => {
        if (f.kind !== 'sub' || f.dir !== playFile.dir) return false
        const videoStem = playFile.name.replace(/\.[^.]+$/, '').toLowerCase()
        const subStem = subtitleStem(f.name)
        return subStem === videoStem || subStem.startsWith(videoStem + '.')
      }).sort((a, b) => {
        const aZh = /\.(zh|zho|chi|chs|sc|cht|tc)(\.|$)/i.test(subtitleStem(a.name)) ? 0 : 1
        const bZh = /\.(zh|zho|chi|chs|sc|cht|tc)(\.|$)/i.test(subtitleStem(b.name)) ? 0 : 1
        return aZh - bZh || a.name.localeCompare(b.name)
      })
    : []
  const [vState, setVState] = useState<'load' | 'ok' | 'err'>('load')
  useEffect(() => { setVState('load'); setRetry(0) }, [playPath])
  const onEnded = () => {
    if (playPath) localStorage.removeItem(progressKey('local', playPath))
    const i = playables.findIndex((v) => v.path === playPath)
    const nx = playables[i + 1]
    if (nx) { setPlayPath(nx.path); setVState('load') }
  }
  const imgs = shown.filter((f) => f.kind === 'image')
  const imgIdx = img ? imgs.findIndex((f) => f.path === img.path) : -1
  const stepImg = (d: number) => {
    if (imgs.length) setImg(imgs[(imgIdx + d + imgs.length) % imgs.length])
  }
  useEffect(() => {
    if (!img) return
    const h = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setImg(null)
      else if (e.key === 'ArrowLeft') stepImg(-1)
      else if (e.key === 'ArrowRight') stepImg(1)
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [img, imgIdx, imgs.length])

  const PASS_P = 'aurora:player:pos'
  const PASS_W = 'aurora:player:w'
  const [fp, setFp] = useState<{ x: number; y: number } | null>(() => {
    try { const v = JSON.parse(localStorage.getItem(PASS_P) || ''); return v && typeof v.x === 'number' ? v : null } catch { return null }
  })
  const vref = useRef<HTMLVideoElement | null>(null)
  const startDrag = (e: RPE<HTMLDivElement>) => {
    const win = (e.currentTarget as HTMLElement).closest('[data-drag-win]') as HTMLElement | null
    if (!win) return
    const r = win.getBoundingClientRect()
    const ox = e.clientX - r.left, oy = e.clientY - r.top
    const move = (ev: PointerEvent) => {
      const p = { x: Math.max(0, ev.clientX - ox), y: Math.max(0, ev.clientY - oy) }
      latest = p; setFp(p); localStorage.setItem(PASS_P, JSON.stringify(p))
    }
    let latest: { x: number; y: number } = { x: 0, y: 0 }
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up)
      const w = win.offsetWidth, h = win.offsetHeight
      let x = latest.x, y = latest.y
      if (x < 80) x = 0; else if (x + w > window.innerWidth - 24) x = window.innerWidth - w
      if (y < 40) y = 0; else if (y + h > window.innerHeight - 24) y = window.innerHeight - h
      const p = { x, y }; setFp(p); localStorage.setItem(PASS_P, JSON.stringify(p))
    }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  const full = () => { if (vref.current) vref.current.requestFullscreen?.() }
  const pip = async () => {
    const video = vref.current
    if (!video) return
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture()
      else if (document.pictureInPictureEnabled && video.requestPictureInPicture) await video.requestPictureInPicture()
    } catch { toast('当前浏览器不支持画中画', 'warn') }
  }
  const jump = (delta: number) => {
    const i = playables.findIndex((f) => f.path === playPath)
    const next = playables[i + delta]
    if (next) setPlayPath(next.path)
  }
  const setMedia = (node: HTMLMediaElement | null) => {
    mediaRef.current = node
    if (node) node.playbackRate = rate
  }
  const [fz, setFz] = useState<{ w: number } | null>(() => {
    try { const v = JSON.parse(localStorage.getItem(PASS_W) || ''); return v && typeof v.w === 'number' ? v : null } catch { return null }
  })
  const startResize = (e: RPE<HTMLDivElement>) => {
    const win = (e.currentTarget as HTMLElement).closest('[data-drag-win]') as HTMLElement | null
    if (!win) return
    e.preventDefault()
    const startW = win.offsetWidth
    const sx = e.clientX
    const move = (ev: PointerEvent) => {
      const w = Math.max(300, Math.min(window.innerWidth - 24, startW + (ev.clientX - sx)))
      setFz({ w }); localStorage.setItem(PASS_W, JSON.stringify({ w }))
    }
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up)
  }
  useEffect(() => {
    if (!playPath || !vref.current) return
    // 续播记忆：仅当浮窗首帧加载时在 onLoadedMetadata 恢复，这里不做
  }, [playPath])
  useEffect(() => {
    if (mediaRef.current) mediaRef.current.playbackRate = rate
  }, [rate, playPath, jfPlay])

  const del = async (f: MediaFile) => {
    if (f.seeding) {
      if (!window.confirm(`「${f.name}」正在 qBittorrent 做种中！移入回收站后种子将报 missingFiles，做种会中断。\n\n仍要继续吗？`)) return
    } else if (!window.confirm(`将「${f.name}」移入回收站？`)) return
    const ok = await deleteMedia(f.path)
    toast(ok ? '已移入回收站' : '操作失败', ok ? 'ok' : 'bad')
    if (ok) { if (playPath === f.path) setPlayPath(null); load() }
  }
  const rn = async (f: MediaFile) => {
    const n = window.prompt('新文件名：', f.name)
    if (!n || n === f.name) return
    if (f.seeding && !window.confirm(`「${f.name}」正在 qBittorrent 做种中！重命名后种子指向的旧路径失效（missingFiles），做种与分享率作废。\n\n仍要重命名吗？`)) return
    const ok = await renameMedia(f.path, n)
    toast(ok ? '已重命名' : '重命名失败', ok ? 'ok' : 'bad')
    if (ok) load()
  }
  const copy = async (p: string) => { try { await navigator.clipboard.writeText(p); toast('路径已复制') } catch { toast('复制失败', 'bad') } }
  const rnDir = async (d: string) => {
    const cur = d.split('/').pop() || ''
    const n = window.prompt('重命名目录为：', cur)
    if (!n || !n.trim() || n.trim() === cur) return
    const ok = await renameMedia(d, n.trim())
    toast(ok ? '目录已重命名' : '重命名失败', ok ? 'ok' : 'bad')
    if (ok) { if (dir === d) setDir(''); load() }
  }
  const delDir = async (d: string) => {
    if (!window.confirm(`删除空目录「${d}」？非空目录将失败。`)) return
    const ok = await rmdirMedia(d)
    toast(ok ? '目录已删除' : '删除失败（目录可能非空）', ok ? 'ok' : 'bad')
    if (ok) { if (dir === d) setDir(''); load() }
  }
  const newDir = async () => {
    const prefix = dir ? (dir.replace(/\/+$/, '') + '/') : ''
    const hint = prefix
      ? `在「${dir}」下新建子目录（已带前缀，直接输入名字即可）：`
      : '新建目录（相对下载盘，如 Movies/科幻）：'
    const p = window.prompt(hint, prefix)
    if (p == null) return
    const cleaned = p.trim().replace(/^\/+/, '')
    if (!cleaned) return
    const ok = await mkdirMedia(cleaned)
    toast(ok ? '目录已创建' : '创建失败（可能已存在或路径非法）', ok ? 'ok' : 'bad')
    if (ok) { setDir(cleaned); load() }
  }
  const toggleSel = (p: string) => setSel((s) => {
    const n = new Set(s)
    if (n.has(p)) n.delete(p); else n.add(p)
    return n
  })
  const batchDel = async () => {
    if (!sel.size) return
    const seedingSel = files.filter((f) => sel.has(f.path) && f.seeding).length
    if (seedingSel) {
      if (!window.confirm(`选中的 ${seedingSel} 个文件正在做种！删除后对应 qBittorrent 种子将报 missingFiles，做种作废。\n\n仍要删除吗？`)) return
    } else if (!window.confirm(`删除选中的 ${sel.size} 个文件？`)) return
    let ok = 0
    for (const p of sel) { if (await deleteMedia(p)) ok++ }
    toast(`已删除 ${ok}/${sel.size}`, ok === sel.size ? 'ok' : 'warn')
    setSel(new Set()); if (playPath && sel.has(playPath)) setPlayPath(null); load()
  }
  const batchMove = () => { if (sel.size) setMoveOpen(true) }
  const closeMove = () => { setMoveOpen(false); setMoveDest(''); setMoveQuery('') }
  const confirmMove = async () => {
    const to = moveDest.trim()
    if (!to || !sel.size) return
    // 完整选中的种子 -> qbit 联动移动（setLocation 搬文件并更新路径，做种不中断）
    const seedGroups = new Map<string, MediaFile[]>()
    for (const f of files) {
      if (sel.has(f.path) && f.seedHash) {
        const arr = seedGroups.get(f.seedHash) ?? []
        arr.push(f); seedGroups.set(f.seedHash, arr)
      }
    }
    const fullSeeds: string[] = []
    for (const [hash] of seedGroups) {
      const allOfSeed = files.filter((f) => f.seedHash === hash)
      if (allOfSeed.length > 0 && allOfSeed.every((f) => sel.has(f.path))) fullSeeds.push(hash)
    }
    const seedFiles = new Set<string>()
    for (const h of fullSeeds) for (const f of files) if (f.seedHash === h) seedFiles.add(f.path)
    // 部分选中的种子文件（种子没选全）走手动移动会毁种 -> 强警告
    const partialSeeding = files.filter((f) => sel.has(f.path) && f.seedHash && !seedFiles.has(f.path)).length
    if (partialSeeding && !window.confirm(`选中了 ${partialSeeding} 个做种中文件，但未包含其完整种子。手动移动会破坏这些种子（missingFiles，做种作废）。\n\n仍要移动吗？`)) return
    let ok = 0, total = 0
    for (const h of fullSeeds) { total++; if (await moveSeedMedia(h, to)) ok++ }
    for (const p of sel) {
      if (seedFiles.has(p)) continue
      total++; if (await moveMedia(p, to)) ok++
    }
    toast(`已移动 ${ok}/${total} 到 ${to}`, ok === total ? 'ok' : 'warn')
    setSel(new Set()); setMoveOpen(false); setMoveDest(''); setMoveQuery(''); load()
  }
  useEffect(() => {
    if (!slide || !img) return
    const id = setInterval(() => stepImg(1), 3000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slide, img, imgIdx, imgs.length])

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col md:h-screen">
      <header className="flex min-w-0 flex-col gap-3 border-b border-line px-4 py-4 sm:flex-row sm:items-center sm:justify-between md:px-10 md:py-5">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm text-dim">
          <FolderOpen size={16} className="text-aurora-2" />
          <span className="text-fg">媒资库</span>
          <div className="ml-1 flex items-center gap-1 rounded-lg border border-line bg-white/4 p-0.5">
            <button onClick={() => setMode('files')}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors ${mode === 'files' ? 'grad-bar text-ink' : 'text-dim hover:text-fg'}`}>
              <ListIcon size={12} /> 文件浏览
            </button>
            <button onClick={() => setMode('wall')}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors ${mode === 'wall' ? 'grad-bar text-ink' : 'text-dim hover:text-fg'}`}>
              <MonitorPlay size={12} /> 海报墙
            </button>
            <button onClick={() => setMode('trash')}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors ${mode === 'trash' ? 'grad-bar text-ink' : 'text-dim hover:text-fg'}`}>
              <Trash2 size={12} /> 回收站
            </button>
          </div>
          {mode === 'files' ? (
            <>
              <span>/</span>
              <span className="grad-txt font-medium">{base || 'local'}</span>
              <span className="num text-[11px] text-dim">{files.length} 文件</span>
            </>
          ) : jf ? (
            jf.online ? <span className="num text-[11px] text-teal-300">Jellyfin · {jf.items.length} 条目</span> : <span className="text-[11px] text-dim">Jellyfin 未接入</span>
          ) : null}
        </div>
        {mode === 'files' && (
        <div className="relative w-full sm:w-auto">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            className="w-full rounded-lg border border-line bg-white/4 py-2 pl-9 pr-3 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none sm:w-52"
            placeholder="搜索文件…" />
        </div>
        )}
      </header>

      {mode === 'files' && (
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-60 shrink-0 border-r border-line px-3 py-4 md:block">
          <div className="flex items-center justify-between px-2 pb-3">
            <span className="text-[11px] uppercase tracking-[0.2em] text-dim">目录</span>
            <button onClick={newDir} title="新建目录"
              className="grid h-6 w-6 place-items-center rounded-md border border-line bg-white/4 text-dim transition-colors hover:border-aurora-2/40 hover:text-aurora-1"><FolderPlus size={12} /></button>
          </div>
          <button onClick={() => setDir('')}
            className={`mb-1 flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-sm ${!dir ? 'bg-white/8 text-fg' : 'text-dim hover:bg-white/5 hover:text-fg'}`}>
            <FolderOpen size={15} className="text-aurora-1/70" /> 全部
          </button>
          {dirsList.map((d) => {
            const seg = d.path.split('/')
            const depth = seg.length - 1
            // 父目录折叠时隐藏子目录
            const hidden = seg.slice(0, -1).some((_, i) => collapsed.has(seg.slice(0, i + 1).join('/')))
            if (hidden) return null
            const hasChildren = dirsList.some((x) => x.path !== d.path && x.path.startsWith(d.path + '/'))
            const isCollapsed = collapsed.has(d.path)
            return (
              <div key={d.path} className="group mb-1 flex items-center gap-0.5">
                <button onClick={() => setDir(d.path)}
                  style={{ paddingLeft: 6 + depth * 14 }}
                  className={`flex min-w-0 flex-1 items-center gap-1.5 rounded-md py-1.5 pr-2 text-sm ${dir === d.path ? 'bg-white/8 text-fg' : 'text-dim hover:bg-white/5 hover:text-fg'}`}>
                  {hasChildren ? (
                    <span onClick={(e) => { e.stopPropagation(); toggleFold(d.path) }} title={isCollapsed ? '展开' : '折叠'}
                      className="grid h-4 w-4 shrink-0 place-items-center text-dim/70 hover:text-fg">
                      <ChevronRight size={12} className={`transition-transform ${isCollapsed ? '' : 'rotate-90'}`} />
                    </span>
                  ) : <span className="w-4 shrink-0" />}
                  <FolderOpen size={15} className="shrink-0 text-aurora-1/70" />
                  <span className="truncate font-medium">{seg[seg.length - 1]}</span>
                </button>
                <div className="flex shrink-0 items-center opacity-0 transition-opacity group-hover:opacity-100">
                  <button title="重命名目录" onClick={() => rnDir(d.path)} className="p-1 text-dim hover:text-fg"><Pencil size={12} /></button>
                  <button title="删除空目录" onClick={() => delDir(d.path)} className="p-1 text-dim hover:text-rose-300"><Trash2 size={12} /></button>
                </div>
              </div>
            )
          })}
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 md:px-8">
          <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2">
            {FILTERS.map((f) => (
              <button key={f} onClick={() => setKind(f)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${kind === f ? 'grad-bar border-transparent text-ink' : 'border-line bg-white/4 text-dim hover:text-fg'}`}>
                {f === 'all' ? '全部' : KIND_LABEL[f]}
              </button>
            ))}
            <span className="flex w-full items-center justify-end gap-1.5 sm:ml-auto sm:w-auto">
              <span className="num text-[11px] text-dim">{shown.length} 项</span>
              <button onClick={() => setSel(sel.size === shown.length ? new Set() : new Set(shown.map((f) => f.path)))} title="全选"
                className="grid h-7 w-7 place-items-center rounded-md border border-line bg-white/4 text-dim hover:text-fg"><Check size={13} /></button>
              <button onClick={() => setView('list')} title="列表"
                className={`grid h-7 w-7 place-items-center rounded-md border ${view === 'list' ? 'border-aurora-2/50 text-aurora-1' : 'border-line bg-white/4 text-dim hover:text-fg'}`}><ListIcon size={13} /></button>
              <button onClick={() => setView('grid')} title="网格"
                className={`grid h-7 w-7 place-items-center rounded-md border ${view === 'grid' ? 'border-aurora-2/50 text-aurora-1' : 'border-line bg-white/4 text-dim hover:text-fg'}`}><LayoutGrid size={13} /></button>
            </span>
          </div>

          {sel.size > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-aurora-2/30 bg-aurora-2/5 px-3 py-2">
              <span className="text-sm text-fg">已选 <span className="num text-aurora-1">{sel.size}</span> 项</span>
              <div className="flex flex-wrap items-center gap-2">
                <button onClick={batchMove} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-3 py-1.5 text-xs text-fg hover:bg-white/8"><FolderInput size={12} />移动到…</button>
                <button onClick={batchDel} className="inline-flex items-center gap-1 rounded-md border border-rose-400/30 bg-rose-400/10 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-400/20"><Trash2 size={12} />删除</button>
                <button onClick={() => setSel(new Set())} className="px-1 text-xs text-dim hover:text-fg">取消</button>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex flex-col gap-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} h="3rem" />)}</div>
          ) : shown.length === 0 ? (
            <EmptyState icon={<File size={20} />} title={loading ? '' : '该目录暂无文件'} hint="在下载盘放入文件后自动显示" />
          ) : view === 'grid' ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
              {shown.map((f) => {
                const Icon = KIND_ICON[f.kind] ?? File
                const hasThumb = f.kind === 'video' || f.kind === 'image'
                return (
                  <div key={f.path}
                    onClick={() => { if (f.kind === 'video' || f.kind === 'audio') setPlayPath(playPath === f.path ? null : f.path); else if (f.kind === 'image') setImg(f) }}
                    className={`card-in group relative cursor-pointer overflow-hidden rounded-xl border ${sel.has(f.path) ? 'border-aurora-2/60 ring-2 ring-aurora-2/30' : 'border-line hover:border-aurora-2/40'}`}>
                    <div className="relative h-28 w-full overflow-hidden bg-black/40">
                      {hasThumb ? <img src={mediaThumbUrl(f.path)} alt={f.name} loading="lazy" className="h-full w-full object-cover" /> : (
                        <div className="grid h-full w-full place-items-center text-aurora-2/70"><Icon size={22} /></div>
                      )}
                      {f.kind === 'video' && <span className="absolute bottom-1 right-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white/90"><Play size={9} className="inline" /></span>}
                    </div>
                    <div className="px-2 py-1.5">
                      <div className="flex items-center gap-1.5">
                        <div className="truncate text-xs font-medium text-fg">{f.name}</div>
                        {f.seeding && <span className="shrink-0 rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">做种中</span>}
                      </div>
                      <div className="num text-[10px] text-dim">{fmtSize(f.size)}</div>
                    </div>
                    <span onClick={(e) => { e.stopPropagation(); toggleSel(f.path) }}
                      className={`absolute left-1.5 top-1.5 grid h-6 w-6 cursor-pointer place-items-center rounded-md ${sel.has(f.path) ? 'bg-aurora-2/70 text-ink' : 'bg-black/40 text-white/70'}`}>
                      {sel.has(f.path) ? <Check size={12} /> : null}
                    </span>
                  </div>
                )
              })}
            </div>
          ) : shown.map((f) => {
            const Icon = KIND_ICON[f.kind] ?? File
            const pl = f.kind === 'video' || f.kind === 'audio'
            return (
              <div key={f.path} className={`card-in mb-2 grid min-w-0 grid-cols-[auto_auto_minmax(0,1fr)] items-center gap-3 rounded-lg border px-3 py-2.5 sm:flex ${sel.has(f.path) ? 'border-aurora-2/50 bg-aurora-2/5' : 'border-line bg-white/3'}`}>
                <input type="checkbox" checked={sel.has(f.path)} onChange={() => toggleSel(f.path)} className="accent-[#a78bfa]" />
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white/5 text-aurora-2/70"><Icon size={17} /></div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <div className="truncate text-sm font-medium text-fg">{f.name}</div>
                    {f.seeding && <span className="shrink-0 rounded-full border border-amber-400/30 bg-amber-400/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-300">做种中</span>}
                  </div>
                  <div className="truncate text-[11px] text-dim">{KIND_LABEL[f.kind] ?? f.ext} · {f.dir || base} · <span className="num">{fmtSize(f.size)}</span></div>
                </div>
                <div className="col-span-3 flex shrink-0 items-center justify-end gap-1 sm:col-span-1">
                  {pl && (
                    <button onClick={() => setPlayPath(playPath === f.path ? null : f.path)}
                      className="grid h-8 w-8 place-items-center rounded-md border border-line bg-white/4 text-dim hover:border-aurora-2/40 hover:text-aurora-1">
                      {playPath === f.path ? <X size={14} /> : <Play size={14} />}
                    </button>
                  )}
                  {f.kind === 'image' && (
                    <button onClick={() => setImg(f)} title="查看"
                      className="grid h-8 w-8 place-items-center rounded-md border border-line bg-white/4 text-dim hover:border-aurora-2/40 hover:text-aurora-1"><ImageIcon size={14} /></button>
                  )}
                  <button onClick={() => rn(f)} title="重命名" className="grid h-8 w-8 place-items-center rounded-md border border-line bg-white/4 text-dim hover:border-aurora-2/40 hover:text-aurora-1"><Pencil size={14} /></button>
                  <button onClick={() => copy(f.path)} title="复制路径" className="grid h-8 w-8 place-items-center rounded-md border border-line bg-white/4 text-dim hover:border-aurora-2/40 hover:text-aurora-1"><Copy size={14} /></button>
                  <button onClick={() => del(f)} title="删除" className="grid h-8 w-8 place-items-center rounded-md border border-line bg-white/4 text-dim hover:border-rose-400/40 hover:text-rose-300"><Trash2 size={14} /></button>
                </div>
              </div>
            )
          })}
        </main>
      </div>
      )}

      {/* 海报墙（Jellyfin） */}
      {mode === 'wall' && (
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 md:px-8">
          {!jf ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} h="17rem" />)}
            </div>
          ) : !jf.online ? (
            <EmptyState icon={<WifiOff size={20} />} title="Jellyfin 未接入" hint="启动 Jellyfin 后这里显示海报墙" />
          ) : jf.items.length === 0 ? (
            <EmptyState icon={<MonitorPlay size={20} />} title="媒体库暂无条目" hint="在下载盘放入电影后 Jellyfin 自动扫描" />
          ) : (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
              {jf.items.map((it) => (
                <button key={it.id} onClick={() => setJfPlay(it)}
                  className="card-in group relative overflow-hidden rounded-xl border border-line bg-white/3 text-left transition-colors hover:border-aurora-2/50">
                  <div className="relative aspect-[2/3] w-full overflow-hidden bg-black/40">
                    {it.hasImage ? (
                      <img src={jellyfinImageUrl(it.id, it.primaryTag)} alt={it.name} loading="lazy"
                        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
                    ) : (
                      <div className="grid h-full w-full place-items-center text-aurora-2/60"><Film size={26} /></div>
                    )}
                    <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
                    <div className="pointer-events-none absolute bottom-2 right-2 grid h-8 w-8 place-items-center rounded-full bg-aurora-2/80 text-ink opacity-0 transition-opacity group-hover:opacity-100">
                      <Play size={14} />
                    </div>
                  </div>
                  <div className="px-3 py-2.5">
                    <div className="truncate text-sm text-fg">{it.name}</div>
                    <div className="mt-0.5 text-[11px] text-dim">{it.type === 'Series' ? '剧集' : '电影'}{it.year ? ` · ${it.year}` : ''}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </main>
      )}

      {mode === 'trash' && (
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 md:px-8">
          {trash.length === 0 ? <EmptyState icon={<Trash2 size={20} />} title="回收站为空" /> : trash.map((it) => (
            <div key={it.id} className="mb-2 flex min-w-0 items-center gap-3 rounded-lg border border-line bg-white/3 px-3 py-3">
              <Trash2 size={16} className="shrink-0 text-dim" />
              <div className="min-w-0 flex-1"><div className="truncate text-sm text-fg">{it.name}</div><div className="num truncate text-[10px] text-dim">{it.path} · {fmtSize(it.size)} · {new Date(it.deleted * 1000).toLocaleString('zh-CN')}</div></div>
              <button title="恢复" onClick={async () => { const r = await restoreTrash(it.id); toast(r.ok ? '文件已恢复' : r.detail, r.ok ? 'ok' : 'bad'); if (r.ok) setTrash((x) => x.filter((v) => v.id !== it.id)) }} className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-line text-dim hover:text-teal-300"><RotateCcw size={14} /></button>
              <button title="彻底删除" onClick={async () => { if (!window.confirm(`彻底删除「${it.name}」？此操作无法恢复。`)) return; const r = await purgeTrash(it.id); toast(r.ok ? '已彻底删除' : r.detail, r.ok ? 'ok' : 'bad'); if (r.ok) setTrash((x) => x.filter((v) => v.id !== it.id)) }} className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-rose-400/30 text-rose-300"><Trash2 size={14} /></button>
            </div>
          ))}
        </main>
      )}

      {playPath && (
        <div data-drag-win style={{ ...(fp ? { left: fp.x, top: fp.y } : {}), ...(fz ? { width: fz.w } : {}) }}
          className={`z-[70] max-w-[calc(100vw-1.5rem)] w-[min(640px,94vw)] overflow-hidden rounded-xl border border-line bg-ink-2/95 shadow-xl backdrop-blur ${fp ? 'fixed' : 'fixed bottom-3 right-3 sm:bottom-5 sm:right-5'}`}>
          <div onPointerDown={startDrag} style={{ cursor: 'grab', touchAction: 'none' }}
            className="flex items-center gap-2 border-b border-line bg-white/4 px-3 py-2">
            <GripHorizontal size={14} className="shrink-0 text-dim" />
            <span className="min-w-0 flex-1 truncate text-xs text-fg">{playFile ? playFile.name : playPath}</span>
            {playables.length > 1 && <>
              <button onClick={() => jump(-1)} disabled={!playables[playables.findIndex((f) => f.path === playPath) - 1]} title="上一项" aria-label="上一项"
                className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:bg-white/8 hover:text-fg disabled:opacity-30"><SkipBack size={13} /></button>
              <button onClick={() => jump(1)} disabled={!playables[playables.findIndex((f) => f.path === playPath) + 1]} title="下一项" aria-label="下一项"
                className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:bg-white/8 hover:text-fg disabled:opacity-30"><SkipForward size={13} /></button>
            </>}
            <select value={rate} onChange={(e) => setRate(Number(e.target.value))} title="播放速度" aria-label="播放速度"
              className="h-7 rounded-md border border-line bg-ink-2 px-1.5 text-[11px] text-fg focus:outline-none">
              {[0.5, 0.75, 1, 1.25, 1.5, 2].map((v) => <option key={v} value={v}>{v}x</option>)}
            </select>
            {isVideo && <button onClick={pip} title="画中画" aria-label="画中画"
              className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:bg-white/8 hover:text-fg"><PictureInPicture size={14} /></button>}
            {isVideo && <button onClick={full} title="全屏" aria-label="全屏" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:bg-white/8 hover:text-fg"><Maximize size={14} /></button>}
            <button onClick={() => setPlayPath(null)} title="关闭播放器" aria-label="关闭播放器" className="grid h-7 w-7 shrink-0 place-items-center text-dim hover:text-fg"><X size={14} /></button>
          </div>
          <div className="p-2">
            {isVideo ? (
              <div className="relative">
                <video key={`${playPath}:${retry}`} ref={(node) => { vref.current = node; setMedia(node) }} controls autoPlay src={mediaStreamUrl(playPath)}
                  onLoadedMetadata={(e) => {
                    restoreProgress(e.currentTarget, progressKey('local', playPath))
                  }}
                  onLoadedData={() => { setVState('ok'); vref.current?.play().catch(() => {}) }}
                  onError={() => setVState('err')}
                  onEnded={onEnded}
                  onTimeUpdate={(e) => localStorage.setItem(progressKey('local', playPath), String(e.currentTarget.currentTime))}
                  className="max-h-[58vh] w-full rounded bg-black">
                  {subtitleFiles.map((sub, i) => <track key={sub.path} kind="subtitles" src={mediaSubtitleUrl(sub.path)} srcLang={/\.(en|eng)(\.|$)/i.test(subtitleStem(sub.name)) ? 'en' : 'zh'} label={subtitleLabel(sub.name)} default={i === 0} />)}
                </video>
                {vState !== 'ok' && (
                  <div className="absolute inset-0 grid place-items-center rounded bg-black/50 text-white/80">
                    {vState === 'err' ? (
                      <div className="flex flex-col items-center gap-3 px-4 text-center text-xs">
                        <span>无法播放该文件，可能是浏览器不支持此编码</span>
                        <button onClick={() => { setVState('load'); setRetry((n) => n + 1) }} className="inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/10 px-2.5 py-1.5 text-white hover:bg-white/20"><RotateCcw size={12} />重试</button>
                      </div>
                    ) : <Loader size={22} className="animate-spin" />}
                  </div>
                )}
                <button onClick={full} title="全屏" aria-label="全屏" className="absolute right-2 top-2 grid h-8 w-8 place-items-center rounded-lg bg-black/50 text-white hover:bg-black/80"><Maximize size={15} /></button>
              </div>
            ) : (
              <div className="relative">
                <audio key={`${playPath}:${retry}`} ref={setMedia} controls autoPlay src={mediaStreamUrl(playPath)}
                  onLoadedMetadata={(e) => restoreProgress(e.currentTarget, progressKey('local', playPath))}
                  onLoadedData={() => setVState('ok')}
                  onError={() => setVState('err')}
                  onEnded={onEnded}
                  onTimeUpdate={(e) => localStorage.setItem(progressKey('local', playPath), String(e.currentTarget.currentTime))}
                  className="w-full" />
                {vState !== 'ok' && <div className="absolute inset-0 grid place-items-center rounded bg-ink-2/90 text-white/80">
                  {vState === 'err' ? <button onClick={() => { setVState('load'); setRetry((n) => n + 1) }} className="inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/10 px-2.5 py-1.5 text-xs text-white hover:bg-white/20"><RotateCcw size={12} />重试</button> : <Loader size={18} className="animate-spin" />}
                </div>}
              </div>
            )}
          </div>
          <div onPointerDown={startResize} title="拖拽调整大小"
            style={{ cursor: 'nwse-resize', touchAction: 'none' }}
            className="absolute bottom-1.5 right-1.5 z-10 h-5 w-5 cursor-nwse-resize rounded-sm border-b-2 border-r-2 border-white/40" />
          <div onPointerDown={(e) => { e.stopPropagation(); startResize(e) }} title="拖拽调整大小（对角）"
            style={{ cursor: 'nwse-resize', touchAction: 'none' }}
            className="absolute left-1.5 top-10 z-10 h-5 w-5 cursor-nwse-resize rounded-sm border-t-2 border-l-2 border-white/40" />
        </div>
      )}

      {img && (
        <div className="overlay-in fixed inset-0 z-[90] grid place-items-center bg-black/85 p-6" onClick={() => setImg(null)}>
          <div className="relative max-h-full max-w-full" onClick={(e) => e.stopPropagation()}>
            <img src={mediaStreamUrl(img.path)} alt={img.name} className="max-h-[85vh] max-w-[90vw] rounded-lg object-contain" />
            <div className="absolute inset-x-0 bottom-2 text-center text-xs text-white/70">{img.name}</div>
            <div className="absolute right-2 top-2 flex gap-1">
              <button onClick={() => setSlide((s) => !s)} title={slide ? '暂停幻灯片' : '自动轮播'}
                className="grid h-9 w-9 place-items-center rounded-lg bg-black/60 text-white hover:bg-black/80">{slide ? <Pause size={16} /> : <Play size={16} />}</button>
              <button onClick={() => setImg(null)} className="grid h-9 w-9 place-items-center rounded-lg bg-black/60 text-white hover:bg-black/80"><X size={18} /></button>
            </div>
            {imgs.length > 1 && (
              <>
                <button onClick={() => stepImg(-1)} className="absolute left-2 top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-black/50 text-white hover:bg-black/75"><ChevronLeft size={20} /></button>
                <button onClick={() => stepImg(1)} className="absolute right-2 top-1/2 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full bg-black/50 text-white hover:bg-black/75"><ChevronRight size={20} /></button>
              </>
            )}
            <div className="absolute bottom-1 right-2 num text-[11px] text-white/60">{imgIdx + 1}/{imgs.length}</div>
          </div>
        </div>
      )}
      {jfPlay && (
        <div className="overlay-in fixed inset-0 z-[85] grid place-items-center bg-black/85 p-4" onClick={() => setJfPlay(null)}>
          <div className="w-full max-w-4xl overflow-hidden rounded-xl border border-line bg-ink-2/95 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-3 border-b border-line bg-white/4 px-4 py-2.5">
              <span className="min-w-0 flex-1 truncate text-sm text-fg">{jfPlay.name}</span>
              <div className="flex shrink-0 items-center gap-1.5">
                <select value={rate} onChange={(e) => setRate(Number(e.target.value))} title="播放速度" aria-label="播放速度"
                  className="h-7 rounded-md border border-line bg-ink-2 px-1.5 text-[11px] text-fg focus:outline-none">
                  {[0.5, 0.75, 1, 1.25, 1.5, 2].map((v) => <option key={v} value={v}>{v}x</option>)}
                </select>
                <button onClick={async () => {
                  const video = vref.current
                  if (!video) return
                  try {
                    if (document.pictureInPictureElement) await document.exitPictureInPicture()
                    else if (document.pictureInPictureEnabled && video.requestPictureInPicture) await video.requestPictureInPicture()
                  } catch { toast('当前浏览器不支持画中画', 'warn') }
                }} title="画中画" aria-label="画中画" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:bg-white/8 hover:text-fg"><PictureInPicture size={14} /></button>
                <button onClick={full} title="全屏" aria-label="全屏" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:bg-white/8 hover:text-fg"><Maximize size={14} /></button>
                {jfPlay.localPath && (
                  <button onClick={() => { setPlayPath(jfPlay.localPath); setJfPlay(null) }}
                    className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2.5 py-1 text-xs text-dim hover:text-fg">
                    <FolderOpen size={12} /> 本地播放
                  </button>
                )}
                <button onClick={() => setJfPlay(null)} className="text-dim hover:text-fg"><X size={16} /></button>
              </div>
            </div>
            <div className="relative bg-black">
              <video key={`${jfPlay.id}:${retry}`} ref={(node) => { vref.current = node; setMedia(node) }} controls autoPlay src={jellyfinStreamUrl(jfPlay.id)}
                onLoadedMetadata={(e) => restoreProgress(e.currentTarget, progressKey('jellyfin', jfPlay.id))}
                onLoadedData={() => setJfState('ok')}
                onError={() => setJfState('err')}
                onTimeUpdate={(e) => localStorage.setItem(progressKey('jellyfin', jfPlay.id), String(e.currentTarget.currentTime))}
                onEnded={() => localStorage.removeItem(progressKey('jellyfin', jfPlay.id))}
                className="max-h-[70vh] w-full" />
              {jfState !== 'ok' && (
                <div className="absolute inset-0 grid place-items-center bg-black/60 text-white/80">
                  {jfState === 'err' ? (
                    <div className="px-6 text-center">
                      <div className="text-sm">Jellyfin 播放失败</div>
                      <button onClick={() => { setJfState('load'); setRetry((n) => n + 1) }} className="mt-3 inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/10 px-2.5 py-1.5 text-xs hover:bg-white/20"><RotateCcw size={12} />重试</button>
                      {jfPlay.localPath && (
                        <button onClick={() => { setPlayPath(jfPlay.localPath); setJfPlay(null) }}
                          className="ml-2 mt-3 rounded-lg border border-line bg-white/10 px-3 py-1.5 text-xs hover:bg-white/20">
                          改用本地播放
                        </button>
                      )}
                    </div>
                  ) : <Loader size={24} className="animate-spin" />}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {moveOpen && (
        <div className="overlay-in fixed inset-0 z-[80] grid place-items-center bg-black/50 px-4 backdrop-blur-sm" onClick={closeMove}>
          <div className="panel w-[min(440px,100%)] overflow-hidden" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <span className="text-sm font-medium text-fg">移动到目录</span>
              <span className="num text-[11px] text-dim">已选 {sel.size} 项</span>
            </div>
            <div className="p-4">
              <div className="relative">
                <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dim" />
                <input value={moveQuery}
                  onChange={(e) => { setMoveQuery(e.target.value); setMoveDest(e.target.value) }} autoFocus
                  placeholder="输入新目录，或点选下方已有目录…"
                  className="w-full rounded-lg border border-line bg-white/4 py-2 pl-8 pr-3 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none" />
              </div>
              <div className="mt-3 max-h-60 overflow-y-auto">
                {dirsList.length === 0 ? (
                  <div className="py-8 text-center text-sm text-dim">暂无可用目录，可输入新路径创建</div>
                ) : dirsList.filter((d) => !moveQuery || d.path.toLowerCase().includes(moveQuery.toLowerCase())).map((d) => (
                  <button key={d.path} onClick={() => { setMoveDest(d.path) }}
                    className={`mb-0.5 flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-sm ${moveDest === d.path ? 'bg-aurora-2/10 text-fg' : 'text-dim hover:bg-white/5 hover:text-fg'}`}>
                    <FolderOpen size={14} className="shrink-0 text-aurora-1/70" />
                    <span className="truncate">{d.path}</span>
                    {moveDest === d.path && <Check size={13} className="ml-auto shrink-0 text-aurora-1" />}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-3">
              <div className="min-w-0 flex-1">
                {moveDest && <div className="max-w-full truncate text-[11px] text-dim">目标：<span className="num text-fg">{moveDest}</span></div>}
              </div>
              <button onClick={closeMove} className="rounded-md border border-line bg-white/4 px-3 py-1.5 text-xs text-dim hover:text-fg">取消</button>
              <button onClick={confirmMove} disabled={!moveDest.trim() || !sel.size}
                className="rounded-md grad-bar px-3 py-1.5 text-xs font-medium text-ink disabled:opacity-40">移动</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
