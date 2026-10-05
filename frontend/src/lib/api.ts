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
  state: 'downloading' | 'stalled' | 'seeding' | 'queued' | 'error' | 'done' | 'paused' | 'unknown'
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
  eta?: number
  savePath?: string
  category?: string
  tags?: string[]
  seedingTime?: number
  destination?: {
    id: string
    remote: string
    path: string
    status: 'waiting' | 'uploading' | 'done' | 'error' | 'orphaned'
    detail: string
  }
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

export interface MediaDir {
  path: string
  name: string
}

async function getJson<T>(url: string, timeoutMs = 2500): Promise<T | null> {
  try {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), timeoutMs)
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
      // metrics 是仪表盘命脉：比默认 2.5s 放宽到 6s，链路稍慢时不至于闪回空态
      const live = await getJson<Metrics>('/api/metrics', 6000)
      if (!on) return
      if (live && live.mounts) {
        setData(live)
        setSource('live')
        const s = (live as Metrics & { sources?: Record<string, string> }).sources
        if (s) setSources(s)
      } else {
        // API 不可用时保持诚实空态，不注入假读数；
        // 同时清空数据源徽标，否则界面继续显示"qBittorrent 真实"等陈旧来源，
        // 让用户误以为任务丢了而不是后端断了
        setData(mockMetrics())
        setSource('mock')
        setSources({})
      }
    }
    tick()
    const id = setInterval(tick, 2000)
    return () => { on = false; clearInterval(id) }
  }, [])
  return createElement(MetricsCtx.Provider, { value: { data, source, sources } }, children)
}

export function useMetrics() {
  return useContext(MetricsCtx)
}

async function responseDetail(r: Response, fallback: string) {
  const d = await r.json().catch(() => null)
  return { data: d, detail: (d && typeof d.detail === 'string' && d.detail) || fallback }
}

export async function fetchMediaDirs(): Promise<MediaDir[]> {
  const d = await getJson<{ dirs?: MediaDir[] }>('/api/media/dirs')
  return d?.dirs || []
}

export async function createMediaDir(path: string) {
  try {
    const r = await fetch('/api/media/mkdir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ path }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { data: d, detail } = await responseDetail(r, '新建目录失败')
    return { ok: r.ok && !!d?.ok, detail, path: (d && d.new) || path }
  } catch {
    return { ok: false, detail: '网络请求失败', path }
  }
}

export async function addTorrent(magnet: string, savePath = '', destinationRemote = '', destinationPath = '', category = '', tags: string[] = [], destinationMode: 'local' | 'remote' | 'default' = 'default') {
  try {
    const r = await fetch('/api/torrents/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({
        magnet, save_path: savePath,
        destination_remote: destinationRemote, destination_path: destinationPath,
        destination_mode: destinationMode,
        category, tags,
      }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, mode: '' } }
    const { data: d, detail } = await responseDetail(r, '提交失败')
    return { ok: r.ok, mode: (d && d.mode) || '', detail }
  } catch {
    return { ok: false, mode: '', detail: '网络请求失败' }
  }
}

export async function addTorrentFile(file: File, savePath = '', destinationRemote = '', destinationPath = '', category = '', tags: string[] = [], destinationMode: 'local' | 'remote' | 'default' = 'default') {
  try {
    const body = new FormData()
    body.append('file', file, file.name)
    body.append('save_path', savePath)
    body.append('destination_remote', destinationRemote)
    body.append('destination_path', destinationPath)
    body.append('destination_mode', destinationMode)
    body.append('category', category)
    body.append('tags', tags.join(','))
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

export interface UpdateStatus {
  ok?: boolean
  repo?: string
  current?: string
  latest?: string
  update_available?: boolean
  tag?: string
  url?: string
  published_at?: string
  checked_at?: number
  cached?: boolean
  detail?: string
}

export async function checkUpdate(force = false): Promise<UpdateStatus> {
  try {
    const r = await fetch(`/api/update/check${force ? '?force=1' : ''}`, { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '登录已过期' } }
    if (!r.ok) return { ok: false, detail: `检查失败（HTTP ${r.status}）` }
    return await r.json()
  } catch {
    return { ok: false, detail: '网络请求失败' }
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
  try {
    const r = await fetch('/api/auth/sessions', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
    if (!r.ok) return []
    return (await r.json()).sessions || []
  } catch {
    return []
  }
}

export async function revokeSession(id: string) {
  try {
    const r = await fetch('/api/auth/sessions/revoke', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ id }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '登录已过期' } }
    const { detail } = await responseDetail(r, '撤销失败')
    return { ok: r.ok, detail }
  } catch {
    return { ok: false, detail: '网络请求失败' }
  }
}

export async function changePassword(current: string, next: string) {
  try {
    const r = await fetch('/api/auth/password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ current, new: next }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '登录已过期' } }
    const { detail } = await responseDetail(r, '修改失败')
    return { ok: r.ok, detail }
  } catch {
    return { ok: false, detail: '网络请求失败' }
  }
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

export interface TorrentFileDetail {
  index: number
  name: string
  size: number
  progress: number
  priority: number
  availability: number
  is_seed: boolean
}

export interface TorrentTrackerDetail {
  url: string
  status: number
  tier: number
  num_peers: number
  num_seeds: number
  num_leeches: number
  msg: string
}

export interface TorrentDetail {
  hash: string
  name: string
  state: string
  progress: number
  size: number
  downloaded: number
  uploaded: number
  dlspeed: number
  upspeed: number
  eta: number
  ratio: number
  seeders: number
  leechers: number
  connections: number
  save_path: string
  content_path: string
  category: string
  tags: string[]
  comment: string
  tracker: string
  error_string: string
  added_on: number
  completion_on: number
  last_activity: number
  seeding_time: number
  inactive_seeding_time: number
  queue_position: number
  download_limit: number
  upload_limit: number
  ratio_limit: number
  seeding_time_limit: number
  inactive_seeding_time_limit: number
  files: TorrentFileDetail[]
  trackers: TorrentTrackerDetail[]
  peers?: Peer[]
}

export interface QbitCategory { name: string; save_path: string }
export interface QbitLabels { online: boolean; categories: QbitCategory[]; tags: string[]; detail?: string }
export interface TorrentCategoryMapping {
  category: string
  local_path: string
  destination_remote: string
  destination_path: string
}

export async function fetchTorrentDetail(hash: string): Promise<TorrentDetail | null> {
  try {
    const r = await fetch(`/api/torrents/detail?hash=${encodeURIComponent(hash)}`, { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    const d = await r.json()
    return d.torrent as TorrentDetail
  } catch { return null }
}

export async function torrentAdvancedAction(id: string, action: string, options: { limitKib?: number; location?: string; deleteFiles?: boolean; fileIds?: number[]; allFileIds?: number[]; priority?: number } = {}) {
  try {
    const r = await fetch('/api/torrents/advanced', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ id, action, limit_kib: options.limitKib, location: options.location || '', delete_files: !!options.deleteFiles, file_ids: options.fileIds || [], all_file_ids: options.allFileIds || [], priority: options.priority }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { detail } = await responseDetail(r, '高级操作失败')
    return { ok: r.ok, detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function fetchTorrentLabels(): Promise<QbitLabels | null> {
  try {
    const r = await fetch('/api/torrents/labels', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    const d = await r.json().catch(() => null)
    if (!r.ok) return { online: false, categories: [], tags: [], detail: d?.detail || '读取分类标签失败' }
    return d as QbitLabels
  } catch { return null }
}

export async function updateTorrentLabels(id: string, category?: string, tags?: string[]) {
  try {
    const r = await fetch('/api/torrents/labels', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ id, ...(category !== undefined ? { category } : {}), ...(tags !== undefined ? { tags } : {}) }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { data: d, detail } = await responseDetail(r, '保存分类标签失败')
    return { ok: r.ok && !!d?.ok, detail, torrent: d?.torrent as TorrentDetail | undefined }
  } catch { return { ok: false, detail: '网络请求失败', torrent: undefined } }
}

export async function createTorrentCategory(name: string, savePath: string) {
  try {
    const r = await fetch('/api/torrents/category', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ name, save_path: savePath }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { detail } = await responseDetail(r, '创建分类失败')
    return { ok: r.ok, detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function editTorrentCategory(name: string, savePath: string) {
  try {
    const r = await fetch('/api/torrents/category/edit', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ name, save_path: savePath }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { detail } = await responseDetail(r, '修改分类失败')
    return { ok: r.ok, detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function deleteTorrentCategory(name: string) {
  try {
    const r = await fetch('/api/torrents/category/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ name }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { detail } = await responseDetail(r, '删除分类失败')
    return { ok: r.ok, detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function createTorrentTags(tags: string[]) {
  try {
    const r = await fetch('/api/torrents/tag', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ tags }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { detail } = await responseDetail(r, '创建标签失败')
    return { ok: r.ok, detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function deleteTorrentTags(tags: string[]) {
  try {
    const r = await fetch('/api/torrents/tag/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ tags }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { detail } = await responseDetail(r, '删除标签失败')
    return { ok: r.ok, detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export async function fetchTorrentMappings(): Promise<TorrentCategoryMapping[]> {
  const d = await getJson<{ mappings?: TorrentCategoryMapping[] }>('/api/torrents/mappings')
  return d?.mappings || []
}

export async function saveTorrentMappings(mappings: TorrentCategoryMapping[]) {
  try {
    const r = await fetch('/api/torrents/mappings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ mappings }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '', mappings: [] as TorrentCategoryMapping[] } }
    const { data: d, detail } = await responseDetail(r, '保存分类映射失败')
    return { ok: r.ok && !!d?.ok, detail, mappings: (d?.mappings || []) as TorrentCategoryMapping[] }
  } catch { return { ok: false, detail: '网络请求失败', mappings: [] as TorrentCategoryMapping[] } }
}

export interface TorrentPolicy {
  id: string
  name: string
  category: string
  hash?: string
  enabled: boolean
  action: 'pause' | 'notify' | 'remove' | 'transfer'
  min_seed_minutes: number
  max_seed_minutes: number
  max_inactive_minutes: number
  max_ratio: number
  allow_delete: boolean
  delete_files: boolean
  destination_remote?: string
  destination_path?: string
}

export interface TorrentPolicies {
  enabled: boolean
  interval: number
  rules: TorrentPolicy[]
}

export interface TorrentPolicyPreview {
  hash: string
  name: string
  category: string
  tags: string[]
  ratio: number
  seeding_minutes: number
  rule_id: string
  rule_name: string
  action: TorrentPolicy['action']
  reason: string
  protected: boolean
  protection: string
  delete_files: boolean
  status?: string
  detail?: string
  recoverable?: boolean
}

export async function fetchTorrentPolicies(): Promise<TorrentPolicies | null> {
  try {
    const r = await fetch('/api/torrents/policies', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json() as TorrentPolicies
  } catch { return null }
}

export async function saveTorrentPolicies(policies: TorrentPolicies) {
  try {
    const r = await fetch('/api/torrents/policies', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(policies),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { data: d, detail } = await responseDetail(r, '保存做种策略失败')
    return { ok: r.ok && !!d?.ok, detail, policies: d?.policies as TorrentPolicies | undefined }
  } catch { return { ok: false, detail: '网络请求失败', policies: undefined } }
}

export async function previewTorrentPolicies(): Promise<{ enabled: boolean; interval: number; items: TorrentPolicyPreview[] } | null> {
  try {
    const r = await fetch('/api/torrents/policies/preview', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

export async function applyTorrentPolicies(dryRun = false) {
  try {
    const r = await fetch('/api/torrents/policies/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ confirm: true, dry_run: dryRun }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '', dryRun: false } }
    const { data: d, detail } = await responseDetail(r, '应用做种策略失败')
    return {
      ok: r.ok && !!d?.ok,
      detail,
      dryRun: !!d?.dry_run,
      applied: d?.applied as number | undefined,
      items: d?.items as TorrentPolicyPreview[] | undefined,
    }
  } catch { return { ok: false, detail: '网络请求失败', dryRun: false, applied: undefined, items: undefined } }
}

export interface PolicyUndoItem {
  id: string
  time: number
  action: string
  name: string
  rule_name: string
  reason: string
  category: string
  trash_id: string
  recoverable: boolean
}

export async function fetchPolicyUndo(): Promise<PolicyUndoItem[]> {
  try {
    const r = await fetch('/api/torrents/policies/undo', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
    if (!r.ok) return []
    return (await r.json()).items || []
  } catch { return [] }
}

export async function undoPolicy(id: string) {
  try {
    const r = await fetch('/api/torrents/policies/undo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ id }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '登录已过期' } }
    const { data: d, detail } = await responseDetail(r, '撤销失败')
    return { ok: r.ok && !!d?.ok, detail: d?.detail || detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export interface MediaOrgRule {
  id: string
  name: string
  category: string
  enabled: boolean
  target_dir: string
  mode: 'hardlink' | 'copy' | 'move'
  use_subfolder: boolean
  min_size_mb: number
}

export interface MediaOrgHistoryItem {
  time: number
  hash: string
  name: string
  rule_name: string
  mode: string
  target: string
  status: string
  detail: string
}

export interface MediaOrgSettings {
  enabled: boolean
  interval: number
  jellyfin_refresh: boolean
  rules: MediaOrgRule[]
  history: MediaOrgHistoryItem[]
}

export async function fetchMediaOrganize(): Promise<MediaOrgSettings | null> {
  try {
    const r = await fetch('/api/media/organize', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json() as MediaOrgSettings
  } catch { return null }
}

export async function saveMediaOrganize(value: { enabled: boolean; interval: number; jellyfin_refresh: boolean; rules: MediaOrgRule[] }) {
  try {
    const r = await fetch('/api/media/organize', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(value),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return (await r.json()) as MediaOrgSettings & { ok: boolean }
  } catch { return null }
}

export interface MediaOrgPreviewItem {
  hash: string
  name: string
  category: string
  size: number
  rule_id: string
  rule_name: string
  mode: string
  target: string
  exists: boolean
  processed: boolean
  skipped_reason: string
  status?: string
  detail?: string
}

export async function previewMediaOrganize(): Promise<{ items: MediaOrgPreviewItem[] } | null> {
  try {
    const r = await fetch('/api/media/organize/preview', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

export async function applyMediaOrganize(dryRun = false) {
  try {
    const r = await fetch('/api/media/organize/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ confirm: true, dry_run: dryRun }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '', organized: 0 } }
    const { data: d, detail } = await responseDetail(r, '执行整理失败')
    return {
      ok: r.ok && !!d?.ok,
      detail,
      organized: d?.organized as number | undefined,
      jellyfinRefreshed: d?.jellyfin_refreshed as boolean | undefined,
      items: d?.items as MediaOrgPreviewItem[] | undefined,
    }
  } catch { return { ok: false, detail: '网络请求失败', organized: 0 } }
}

export interface RssFeed {
  path: string
  name: string
  url: string
  has_error: boolean
  loading: boolean
}

export interface RssRule {
  name: string
  enabled: boolean
  use_regex: boolean
  must_contain: string
  must_not_contain: string
  episode_filter: string
  affected_feeds: string[]
  save_path: string
  category: string
  tags: string[]
  last_match: number
  /** 当前绑定的网盘转存目标（经 aurora-remote-* 标记反查），编辑时回显用 */
  destination_remote?: string
  destination_path?: string
}

export interface RssArticle {
  title: string
  feed: string
  url: string
}

export async function fetchRssOverview(): Promise<{ feeds: RssFeed[]; rules: RssRule[] } | null> {
  try {
    const r = await fetch('/api/rss/overview', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json()
  } catch { return null }
}

async function rssPost(body: Record<string, unknown>, path: string, failMsg: string): Promise<{ ok: boolean; detail: string }> {
  try {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify(body),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '登录已过期' } }
    if (!r.ok) {
      let detail = failMsg
      try { detail = (await r.json()).detail || detail } catch { /* 保留默认文案 */ }
      return { ok: false, detail }
    }
    return { ok: true, detail: '' }
  } catch { return { ok: false, detail: '网络请求失败' } }
}

export const rssAddFeed = (p: { path: string; url: string }) => rssPost(p, '/api/rss/feeds/add', '添加订阅失败')
export const rssRemoveFeed = (path: string) => rssPost({ path }, '/api/rss/feeds/remove', '删除订阅失败')
export const rssRenameFeed = (path: string, new_path: string) => rssPost({ path, new_path }, '/api/rss/feeds/rename', '重命名失败')
export const rssSetFeedUrl = (path: string, url: string) => rssPost({ path, url }, '/api/rss/feeds/url', '更新地址失败')
export const rssRefreshFeed = (path: string) => rssPost({ path }, '/api/rss/feeds/refresh', '刷新失败')
export const rssRemoveRule = (name: string) => rssPost({ name }, '/api/rss/rules/remove', '删除规则失败')

export async function saveRssRule(rule: RssRule & { destination_remote?: string; destination_path?: string }) {
  return rssPost(rule as unknown as Record<string, unknown>, '/api/rss/rules/save', '保存规则失败')
}

export async function previewRssMatches(name: string): Promise<RssArticle[] | null> {
  try {
    const r = await fetch('/api/rss/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ name }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return (await r.json()).items || []
  } catch { return null }
}

export async function batchAction(ids: string[], action: string, options: { category?: string; limitKib?: number; location?: string; destinationRemote?: string; destinationPath?: string } = {}) {
  try {
    const r = await fetch('/api/torrents/batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({
        action, ids, category: options.category || '', limit_kib: options.limitKib,
        location: options.location || '', destination_remote: options.destinationRemote || '',
        destination_path: options.destinationPath || '',
      }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { done: 0, failed: 0 } }
    const d = await r.json().catch(() => null)
    return { ok: r.ok, done: (d && d.done) || 0, failed: (d && d.failed) || 0, detail: (d && d.detail) || '', errors: (d && d.errors) || [] }
  } catch {
    return { ok: false, done: 0, failed: 0, detail: '网络请求失败', errors: [] }
  }
}

export async function retryTorrentDestination(id: string) {
  try {
    const r = await fetch('/api/torrents/destination/retry', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ id }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { detail } = await responseDetail(r, '重试失败')
    return { ok: r.ok, detail }
  } catch { return { ok: false, detail: '网络请求失败' } }
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

export interface QbitQueueSettings {
  queueing_enabled: boolean
  max_active_torrents: number
  max_active_downloads: number
  max_active_uploads: number
  max_active_checking_torrents: number
  add_to_top_of_queue: boolean
}

export async function fetchQbitQueueSettings(): Promise<{ online: boolean; settings: QbitQueueSettings | null; detail?: string } | null> {
  try {
    const r = await fetch('/api/qbittorrent/queue', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    const d = await r.json().catch(() => null)
    if (!r.ok) return { online: false, settings: null, detail: d?.detail || '读取 qBittorrent 设置失败' }
    return d as { online: boolean; settings: QbitQueueSettings | null; detail?: string }
  } catch { return null }
}

export async function saveQbitQueueSettings(settings: QbitQueueSettings) {
  try {
    const r = await fetch('/api/qbittorrent/queue', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify(settings),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { data: d, detail } = await responseDetail(r, '保存 qBittorrent 队列设置失败')
    return { ok: r.ok && !!d?.ok, detail, settings: d?.settings as QbitQueueSettings | undefined }
  } catch { return { ok: false, detail: '网络请求失败', settings: undefined } }
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

export interface RcloneRemote { name: string; type: string; bucket?: string }

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

export interface RcloneConfig {
  name: string
  type: string
  params: Record<string, string>
  secretFields: string[]
}

export async function fetchRcloneRemote(name: string): Promise<RcloneConfig | null> {
  try {
    const r = await fetch(`/api/rclone/remotes/config?name=${encodeURIComponent(name)}`, { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    if (!r.ok) return null
    return await r.json() as RcloneConfig
  } catch { return null }
}

export async function updateRcloneRemote(name: string, params: Record<string, string>, newName = name) {
  try {
    const r = await fetch('/api/rclone/remotes/update', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify({ name, new_name: newName, params }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const d = await r.json().catch(() => null)
    return { ok: r.ok && !!d?.ok, detail: (d && d.detail) || (r.ok ? '' : '更新失败') }
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

export interface RcloneEntry {
  name: string
  path: string
  isDir: boolean
  size: number
  modTime: string
  mimeType: string
}

export interface RcloneTransfer {
  id: string
  action: 'upload' | 'download' | 'copy' | 'move' | 'delete'
  label: string
  status: 'running' | 'done' | 'error' | 'canceled'
  progress: number | null
  bytes: number
  total: number | null
  speed: number
  detail: string
  created: number
  finished: number
  retryable?: boolean
  phase?: 'staging' | 'transferring'
}

async function rcloneMutation(url: string, body: unknown) {
  try {
    const r = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
      body: JSON.stringify(body),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '' } }
    const { data, detail } = await responseDetail(r, '操作失败')
    return { ok: r.ok && !!data?.ok, detail, job: data?.job as RcloneTransfer | null }
  } catch { return { ok: false, detail: '网络请求失败', job: null } }
}

export async function fetchRcloneFiles(name: string, path = ''): Promise<{ name: string; path: string; items: RcloneEntry[]; detail?: string } | null> {
  try {
    const r = await fetch(`/api/rclone/files?name=${encodeURIComponent(name)}&path=${encodeURIComponent(path)}`, { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return null }
    const data = await r.json().catch(() => null) as { name?: string; path?: string; items?: RcloneEntry[]; detail?: string } | null
    if (!r.ok) return { name, path, items: [], detail: data?.detail || '目录读取失败' }
    return { name: data?.name || name, path: data?.path || path, items: data?.items || [], detail: data?.detail }
  } catch { return null }
}

export async function mkdirRclone(name: string, path: string) {
  return rcloneMutation('/api/rclone/files/mkdir', { name, path })
}

export async function deleteRcloneFile(name: string, path: string, isDir: boolean) {
  return rcloneMutation('/api/rclone/files/delete', { name, path, is_dir: isDir })
}

export async function renameRcloneFile(name: string, path: string, newName: string, isDir: boolean) {
  return rcloneMutation('/api/rclone/files/rename', { name, path, new_name: newName, is_dir: isDir })
}

export async function copyRcloneFile(sourceName: string, sourcePath: string, destinationName: string, destinationPath: string, isDir: boolean, action: 'copy' | 'move') {
  return rcloneMutation('/api/rclone/transfers/copy', {
    source_name: sourceName, source_path: sourcePath, destination_name: destinationName,
    destination_path: destinationPath, is_dir: isDir, action,
  })
}

export async function downloadRcloneFile(name: string, path: string, destination: string, isDir: boolean) {
  return rcloneMutation('/api/rclone/transfers/download', { name, path, destination, is_dir: isDir })
}

export async function uploadRcloneFile(
  name: string,
  path: string,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
) {
  return new Promise<{ ok: boolean; detail: string; job: RcloneTransfer | null }>((resolve) => {
    const body = new FormData()
    body.append('name', name)
    body.append('path', path)
    body.append('file', file, file.name)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/rclone/transfers/upload')
    xhr.withCredentials = true
    xhr.upload.onprogress = (event) => {
      const total = file.size
      if (!total) return
      const loaded = event.lengthComputable && event.total > 0
        ? Math.round(total * Math.min(1, event.loaded / event.total))
        : Math.min(total, event.loaded)
      onProgress?.(loaded, total)
    }
    xhr.onload = () => {
      type UploadResponse = { ok?: boolean; detail?: string; job?: RcloneTransfer }
      let data: UploadResponse | null = null
      try { data = JSON.parse(xhr.responseText) as UploadResponse } catch { /* handled by fallback below */ }
      if (xhr.status === 401) {
        window.dispatchEvent(new Event('aurora:unauth'))
        resolve({ ok: false, detail: '', job: null })
        return
      }
      resolve({
        ok: xhr.status >= 200 && xhr.status < 300 && !!data?.ok,
        detail: data?.detail || (xhr.status >= 200 && xhr.status < 300 ? '上传失败' : `上传失败（HTTP ${xhr.status}）`),
        job: data?.job || null,
      })
    }
    xhr.onerror = () => resolve({ ok: false, detail: '网络请求失败', job: null })
    xhr.onabort = () => resolve({ ok: false, detail: '上传已取消', job: null })
    try {
      xhr.send(body)
    } catch {
      resolve({ ok: false, detail: '网络请求失败', job: null })
    }
  })
}

export async function fetchRcloneTransfers(): Promise<RcloneTransfer[]> {
  try {
    const r = await fetch('/api/rclone/transfers', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
    if (!r.ok) return []
    return (await r.json()).jobs || []
  } catch { return [] }
}

export async function cancelRcloneTransfer(id: string) {
  return rcloneMutation('/api/rclone/transfers/cancel', { id })
}

export async function retryRcloneTransfer(id: string) {
  return rcloneMutation('/api/rclone/transfers/retry', { id })
}

export async function clearRcloneTransfers() {
  return rcloneMutation('/api/rclone/transfers/clear', {})
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
export const mediaSubtitleUrl = (p: string) => `/api/media/subtitle?path=${encodeURIComponent(p)}`

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
  try {
    const r = await fetch('/api/media/trash', { credentials: 'include' })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return [] }
    if (!r.ok) return []
    return (await r.json()).items || []
  } catch {
    return []
  }
}

async function trashAction(action: 'restore' | 'purge', id: string) {
  try {
    const r = await fetch(`/api/media/trash/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ path: id }),
    })
    if (r.status === 401) { window.dispatchEvent(new Event('aurora:unauth')); return { ok: false, detail: '登录已过期' } }
    const { detail } = await responseDetail(r, action === 'restore' ? '恢复失败' : '删除失败')
    return { ok: r.ok, detail }
  } catch {
    return { ok: false, detail: '网络请求失败' }
  }
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
