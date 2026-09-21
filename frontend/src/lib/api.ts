import { createContext, createElement, useContext, useEffect, useState, type ReactNode } from 'react'

// Lightweight API client. Every accessor hits the FastAPI backend and falls
// back to deterministic mock data so the UI is fully explorable offline.

export type MountStatus = 'online' | 'degraded' | 'offline'

export interface Mount {
  id: string
  name: string
  driver: 'rclone' | 'openlist' | 'webdav' | 'local'
  provider: string
  usedGb: number
  capGb: number
  status: MountStatus
  /** local: 真实读速率 B/s；rclone: 活跃传输数 */
  reads: number
  latencyMs: number | null
}

export interface Torrent {
  id: string
  name: string
  state: 'downloading' | 'seeding' | 'queued' | 'error' | 'done' | 'paused'
  progress: number
  speed: number
  sizeGb: number
  leechers: number
  seeders: number
  upspeed: number
  ratio: number
  upGb: number
  downGb: number
  conns: number
  hash: string
}

export interface Peer {
  ip: string
  client: string
  country: string
  country_code: string
  progress: number
  up_speed: number
  down_speed: number
  uploaded: number
  downloaded: number
}

export interface TorrentPeers {
  peers: Peer[]
  connected: number
  seeds: number
  leechers: number
}

export interface StreamEvent {
  id: string
  title: string
  source: string
  client: 'web' | 'ios' | 'android' | 'tv'
  bitrate: number
  devices: number
  status: 'live' | 'buffering'
}

export interface Disk {
  usedGb: number
  capGb: number
  rw: number
}

export interface Bandwidth {
  in: number
  out: number
}

export interface Metrics {
  mounts: Mount[]
  torrents: Torrent[]
  streams: StreamEvent[]
  disk: Disk
  bandwidth: Bandwidth
}

async function getJson<T>(url: string): Promise<T | null> {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 2500)
    const res = await fetch(url, { signal: ctrl.signal, credentials: 'include' })
    clearTimeout(t)
    if (res.status === 401) {
      window.dispatchEvent(new Event('aurora:unauth'))
      return null
    }
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

// ---- deterministic mock generator (seeded) ----
export function mockMetrics(): Metrics {
  return { mounts: [], torrents: [], streams: [], disk: { usedGb: 0, capGb: 0, rw: 0 }, bandwidth: { in: 0, out: 0 } }
}

interface MetricsState {
  data: Metrics
  source: 'live' | 'mock'
  sources: Record<string, string>
}

const MetricsCtx = createContext<MetricsState>({ data: mockMetrics(), source: 'mock', sources: {} })

export function MetricsProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Metrics>(mockMetrics)
  const [source, setSource] = useState<'live' | 'mock'>('mock')
  const [sources, setSources] = useState<Record<string, string>>({})
  useEffect(() => {
    let on = true
    const tick = async () => {
      const live = await getJson<Metrics>('/api/metrics')
      if (!on) return
      if (live && live.mounts) {
        setData(live)
        setSource('live')
        const s = (live as Metrics & { sources?: Record<string, string> }).sources
        if (s) setSources(s)
      } else {
        // API 不可用时保持诚实空态，不注入假读数
        setData(mockMetrics())
        setSource('mock')
      }
    }
    tick()
    const id = setInterval(tick, 2000)
    return () => { on = false; clearInterval(id) }
  }, [])
  return createElement(MetricsCtx.Provider, { value: { data, source, sources } }, children)
}

export function useMetrics(_pollMs = 4000) {
  return useContext(MetricsCtx)
}

async function responseDetail(r: Response, fallback: string) {
  const d = await r.json().catch(() => null)
  return { data: d, detail: (d && typeof d.detail === 'string' && d.detail) || fallback }
}

export async function addTorrent(magnet: string) {
  try {
    const r = await fetch('/api/torrents/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ magnet }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, mode: '' } }
    const { data: d, detail } = await responseDetail(r, '提交失败')
    return { ok: r.ok, mode: (d && d.mode) || '', detail }
  } catch {
    return { ok: false, mode: '', detail: '网络请求失败' }
  }
}

export async function addTorrentFile(file: File) {
  try {
    const body = new FormData()
    body.append('file', file, file.name)
    const r = await fetch('/api/torrents/upload', {
      method: 'POST', credentials: 'include', body,
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, mode: '', detail: '' } }
    const { data: d, detail } = await responseDetail(r, '上传失败')
    return { ok: r.ok && !!d?.ok, mode: (d && d.mode) || '', detail }
  } catch {
    return { ok: false, mode: '', detail: '网络请求失败' }
  }
}

export async function fetchInfo() {
  try {
    const r = await fetch('/api/info', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  }
}

export interface AuthSession {
  id: string
  created: number
  expires: number
  ip: string
  device: string
  current: boolean
}

export async function fetchSessions(): Promise<AuthSession[]> {
  const r = await fetch('/api/auth/sessions', { credentials: 'include' })
  if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
  if (!r.ok) return []
  return (await r.json()).sessions || []
}

export async function revokeSession(id: string) {
  const r = await fetch('/api/auth/sessions/revoke', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ id }),
  })
  const { detail } = await responseDetail(r, '撤销失败')
  return { ok: r.ok, detail }
}

export async function changePassword(current: string, next: string) {
  const r = await fetch('/api/auth/password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ current, new: next }),
  })
  const { detail } = await responseDetail(r, '修改失败')
  return { ok: r.ok, detail }
}

export async function torrentAction(id: string, action: string) {
  try {
    const r = await fetch(`/api/torrents/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ id }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, mode: '' } }
    const { data: d, detail } = await responseDetail(r, '操作失败')
    return { ok: r.ok && !!d?.ok, mode: (d && d.mode) || '', detail }
  } catch {
    return { ok: false, mode: '', detail: '网络请求失败' }
  }
}

export async function batchAction(ids: string[], action: string) {
  try {
    const r = await fetch('/api/torrents/batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ action, ids }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { done: 0, failed: 0 } }
    const d = await r.json().catch(() => null)
    return { ok: r.ok, done: (d && d.done) || 0, failed: (d && d.failed) || 0 }
  } catch {
    return { done: 0, failed: 0 }
  }
}

export async function fetchTorrentPeers(hash: string): Promise<TorrentPeers | null> {
  try {
    const r = await fetch(`/api/torrents/peers?hash=${encodeURIComponent(hash)}`, { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json() as TorrentPeers
  } catch {
    return null
  }
}

export async function fetchStats() {
  try {
    const r = await fetch('/api/stats', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
    if (!r.ok) return []
    const d = await r.json()
    return d.days || []
  } catch {
    return []
  }
}

export async function fetchSettings() {
  try {
    const r = await fetch('/api/settings', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

export async function saveSettings(settings: Record<string, unknown>) {
  try {
    const r = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ settings }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

export async function testTelegram(token: string, chatId: string) {
  try {
    const r = await fetch('/api/tg/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ token, chat_id: chatId }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const d = await r.json().catch(() => null)
    return { ok: !!d?.ok, detail: (d && d.detail) || '' }
  } catch { return { ok: false, detail: '请求失败' } }
}

export interface RcloneRemote { name: string; type: string }

export async function fetchRcloneRemotes(): Promise<{ online: boolean; remotes: RcloneRemote[] } | null> {
  try {
    const r = await fetch('/api/rclone/remotes', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

export async function testRcloneRemote(name: string): Promise<{ ok: boolean; detail: string; latencyMs?: number }> {
  try {
    const r = await fetch('/api/rclone/remotes/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ name }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const d = await r.json().catch(() => null)
    return { ok: r.ok && !!d?.ok, detail: (d && d.detail) || (r.ok ? '测试失败' : '测试请求失败'), latencyMs: d?.latencyMs }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function createRcloneRemote(name: string, type: string, params: Record<string, string>) {
  try {
    const r = await fetch('/api/rclone/remotes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ name, type, params }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const d = await r.json().catch(() => null)
    return { ok: r.ok, detail: (d && d.detail) || (r.ok ? '' : '创建失败') }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function deleteRcloneRemote(name: string) {
  try {
    const r = await fetch('/api/rclone/remotes/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ name }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const d = await r.json().catch(() => null)
    return { ok: r.ok, detail: (d && d.detail) || (r.ok ? '' : '删除失败') }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function fetchLogs() {
  try {
    const r = await fetch('/api/logs', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
    if (!r.ok) return []
    const d = await r.json()
    return d.logs || []
  } catch {
    return []
  }
}

export interface MediaFile {
  path: string
  name: string
  dir: string
  size: number
  ext: string
  kind: string
  seeding?: boolean
  seedHash?: string
}

export async function fetchMedia() {
  try {
    const r = await fetch('/api/media', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  }
}

export const mediaStreamUrl = (p: string) => `/api/media/stream?path=${encodeURIComponent(p)}`
export const mediaThumbUrl = (p: string) => `/api/media/thumb?path=${encodeURIComponent(p)}`

export interface JellyfinItem {
  id: string
  name: string
  type: string
  year?: number
  overview?: string
  hasImage: boolean
  primaryTag: string
  localPath: string
  path: string
}

export async function fetchJellyfinLibrary(): Promise<{ online: boolean; items: JellyfinItem[] } | null> {
  try {
    const r = await fetch('/api/jellyfin/library', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json() as { online: boolean; items: JellyfinItem[] }
  } catch { return null }
}

export const jellyfinImageUrl = (itemId: string, tag: string) => `/api/jellyfin/image?item_id=${encodeURIComponent(itemId)}&tag=${encodeURIComponent(tag)}`
export const jellyfinStreamUrl = (itemId: string) => `/api/jellyfin/stream?item_id=${encodeURIComponent(itemId)}`

export async function moveMedia(path: string, to: string) {
  try {
    const r = await fetch('/api/media/move', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ path, to }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')) }
    return r.ok
  } catch { return false }
}

export async function moveSeedMedia(hash: string, dir: string) {
  try {
    const r = await fetch('/api/media/move_seed', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ hash, dir }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')) }
    return r.ok
  } catch { return false }
}

export async function deleteMedia(path: string) {
  try {
    const r = await fetch('/api/media/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ path }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')) }
    return r.ok
  } catch { return false }
}

export interface TrashItem { id: string; path: string; name: string; size: number; deleted: number }

export async function fetchTrash(): Promise<TrashItem[]> {
  const r = await fetch('/api/media/trash', { credentials: 'include' })
  if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
  if (!r.ok) return []
  return (await r.json()).items || []
}

async function trashAction(action: 'restore' | 'purge', id: string) {
  const r = await fetch(`/api/media/trash/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ path: id }),
  })
  const { detail } = await responseDetail(r, action === 'restore' ? '恢复失败' : '删除失败')
  return { ok: r.ok, detail }
}

export const restoreTrash = (id: string) => trashAction('restore', id)
export const purgeTrash = (id: string) => trashAction('purge', id)

export async function renameMedia(path: string, newName: string) {
  try {
    const r = await fetch('/api/media/rename', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ path, new: newName }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')) }
    return r.ok
  } catch { return false }
}

export interface MediaDir { path: string; name: string }

export async function fetchDirs(): Promise<MediaDir[] | null> {
  try {
    const r = await fetch('/api/media/dirs', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    const d = await r.json() as { dirs: MediaDir[] }
    return d.dirs || []
  } catch { return null }
}

export async function mkdirMedia(path: string) {
  try {
    const r = await fetch('/api/media/mkdir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ path }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')) }
    return r.ok
  } catch { return false }
}

export async function rmdirMedia(path: string) {
  try {
    const r = await fetch('/api/media/rmdir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ path }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')) }
    return r.ok
  } catch { return false }
}
