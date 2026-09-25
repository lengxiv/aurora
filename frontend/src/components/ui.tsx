import type { ReactNode } from 'react'

// ---- formatting helpers ----
// 非有限数值（0/0、缺字段等）一律显示占位符，而不是 "NaN%" / "NaN GiB"
function finite(n: number): boolean {
  return Number.isFinite(n)
}
export function fmtGb(n: number): string {
  if (!finite(n)) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(2)} TiB` : `${n.toFixed(n < 10 ? 1 : 0)} GiB`
}
export function fmtBytes(n: number): string {
  if (!finite(n)) return '—'
  if (n >= 1024 * 1024 * 1024) return `${(n / 1e9).toFixed(1)} GB`
  if (n >= 1024 * 1024) return `${(n / 1e6).toFixed(1)} MB`
  return `${n.toFixed(0)} B`
}
export function fmtRate(mbps: number): string {
  if (!finite(mbps)) return '—'
  if (mbps >= 1000) return `${(mbps / 1000).toFixed(1)} Gb/s`
  return `${mbps.toFixed(1)} Mb/s`
}
export function pct(n: number): string {
  if (!finite(n)) return '—'
  return `${(n * 100).toFixed(n >= 1 ? 0 : 1)}%`
}

// ---- primitives ----
export function StatCard({ label, value, sub, accent }: { label: string; value: string; sub?: ReactNode; accent?: boolean }) {
  return (
    <div className="panel panel-hover px-5 py-4">
      <div className="text-[11px] uppercase tracking-[0.18em] text-dim">{label}</div>
      <div className={`num mt-2 text-2xl font-medium ${accent ? 'grad-txt' : ''}`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-dim">{sub}</div>}
    </div>
  )
}

export function Bar({ p, className = 'grad-bar' }: { p: number; className?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
      <div className={`h-full rounded-full ${className}`} style={{ width: `${Math.min(100, p * 100)}%` }} />
    </div>
  )
}

export function Tag({ children, tone }: { children: ReactNode; tone: 'ok' | 'warn' | 'bad' | 'muted' }) {
  const map = {
    ok: 'text-teal-300 border-teal-400/30 bg-teal-400/10',
    warn: 'text-amber-300 border-amber-400/30 bg-amber-400/10',
    bad: 'text-rose-300 border-rose-400/30 bg-rose-400/10',
    muted: 'text-dim border-white/10 bg-white/5',
  }
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] ${map[tone]}`}>{children}</span>
  )
}

export function Fi({ children, label }: { children: ReactNode; label: string }) {
  return (
    <span role="img" aria-label={label} className="inline-flex">{children}</span>
  )
}

const REAL = new Set(['system', 'rclone', 'qbittorrent', 'jellyfin', 'local'])

// 挂载行「读数」：local=真实读速率 B/s；rclone=活跃传输数（均不伪装成 0/固定值）
export function fmtMountReads(m: { driver?: string; reads?: number }): string {
  const r = m.reads ?? 0
  if (m.driver === 'local') return r > 0 ? `${fmtBytes(r)}/s` : '—'
  if (m.driver === 'rclone') return `${r} 传输`
  return '—'
}

export function MountLatency({ m, className = 'text-[11px] text-dim' }: { m: { latencyMs?: number | null }; className?: string }) {
  return <div className={className}>{m.latencyMs == null ? '—' : `${m.latencyMs} ms`}</div>
}

export const SC_KEY: Record<string, string> = { mounts: '挂载', torrents: '磁力', streams: '播放', disk: '磁盘', bandwidth: '带宽' }
export const SC_VAL: Record<string, string> = {
  none: '未接入', system: '系统真实', rclone: 'rclone 真实', qbittorrent: 'qBittorrent 真实', jellyfin: 'Jellyfin 真实', local: '本地真实',
}

export function SourceBadge({ sources }: { sources: Record<string, string> }) {
  const items = Object.entries(sources)
  if (items.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map(([k, v]) => (
        <Tag key={k} tone={REAL.has(v) ? 'ok' : 'warn'}>{SC_KEY[k] ?? k} · {SC_VAL[v] ?? v}</Tag>
      ))}
    </div>
  )
}

export function Skeleton({ h = '2.5rem', w = '100%' }: { h?: string; w?: string }) {
  return <div className="skeleton rounded-lg" style={{ height: h, width: w }} />
}

export function EmptyState({ icon, title, hint }: { icon: ReactNode; title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 text-center">
      <div className="grid h-11 w-11 place-items-center rounded-xl border border-line bg-white/3 text-aurora-2/70">{icon}</div>
      <div className="mt-3 text-sm text-dim">{title}</div>
      {hint && <div className="mt-1 text-xs text-dim/60">{hint}</div>}
    </div>
  )
}

export const STATE_ZH: Record<string, string> = {
  downloading: '下载中', stalled: '下载停滞', seeding: '做种中', queued: '排队中', paused: '已暂停', error: '出错', done: '已完成', unknown: '未知',
  metaDL: '获取元数据', forcedDL: '强制下载', stalledUP: '做种待连', stalledDL: '下载待连', uploading: '上传中', checkingDL: '校验中', checkingUP: '校验中',
}
export const STATUS_ZH: Record<string, string> = { online: '在线', degraded: '降级', offline: '离线' }
export const CLIENT_ZH: Record<string, string> = { web: '网页', ios: 'iOS', android: '安卓', tv: '电视', desktop: '桌面', mobile: '移动' }
export const STREAM_ZH: Record<string, string> = { live: '播放中', buffering: '缓冲中' }

export function Switch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${checked ? 'grad-bar' : 'bg-white/10'}`}>
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${checked ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  )
}

export function Spark({ data, w = 120, h = 34, className = '' }: { data: number[]; w?: number; h?: number; className?: string }) {
  const max = Math.max(...data, 1)
  const step = data.length > 1 ? w / (data.length - 1) : 0
  const pts = data.map((v, i) => [i * step, h - (v / max) * (h - 4) - 2])
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0]},${p[1]}`).join(' ')
  const fill = `M0,${h} L${pts.map((p) => `${p[0]},${p[1]}`).join(' L')} L${w},${h} Z`
  return (
    <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} className={className} aria-hidden="true" preserveAspectRatio="none">
      <defs>
        <linearGradient id="spk" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#a78bfa" stopOpacity="0.35" />
          <stop offset="1" stopColor="#a78bfa" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={fill} fill="url(#spk)" />
      <path d={line} fill="none" stroke="#a78bfa" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

// ---- 国家/地区：国旗（ISO 两位码 → 区域指示符）----

const _iso2flag = (iso: string) =>
  [...iso.toUpperCase()].map((c) => String.fromCodePoint(0x1f1e6 + c.charCodeAt(0) - 65)).join('')

// qBittorrent 的 country 是英文全名（GeoLite2），映射回 ISO 两位码
const NAME_ISO: Record<string, string> = {
  'china': 'CN', 'hong kong': 'HK', 'taiwan': 'TW', 'macau': 'MO', 'macao': 'MO',
  'singapore': 'SG', 'japan': 'JP', 'korea, republic of': 'KR', 'south korea': 'KR',
  'north korea': 'KP', 'united states': 'US', 'usa': 'US', 'united kingdom': 'GB', 'uk': 'GB',
  'germany': 'DE', 'france': 'FR', 'italy': 'IT', 'spain': 'ES', 'portugal': 'PT',
  'netherlands': 'NL', 'belgium': 'BE', 'switzerland': 'CH', 'austria': 'AT',
  'russian federation': 'RU', 'russia': 'RU', 'ukraine': 'UA', 'poland': 'PL',
  'czechia': 'CZ', 'czech republic': 'CZ', 'slovakia': 'SK', 'hungary': 'HU',
  'romania': 'RO', 'bulgaria': 'BG', 'greece': 'GR', 'denmark': 'DK', 'sweden': 'SE',
  'norway': 'NO', 'finland': 'FI', 'ireland': 'IE', 'iceland': 'IS', 'turkey': 'TR',
  'india': 'IN', 'pakistan': 'PK', 'bangladesh': 'BD', 'sri lanka': 'LK',
  'indonesia': 'ID', 'malaysia': 'MY', 'thailand': 'TH', 'vietnam': 'VN',
  'philippines': 'PH', 'myanmar': 'MM', 'cambodia': 'KH', 'laos': 'LA',
  'lao people\'s democratic republic': 'LA', 'mongolia': 'MN', 'nepal': 'NP',
  'afghanistan': 'AF', 'bhutan': 'BT', 'maldives': 'MV', 'brunei darussalam': 'BN',
  'timor-leste': 'TL', 'australia': 'AU', 'new zealand': 'NZ',
  'canada': 'CA', 'brazil': 'BR', 'argentina': 'AR', 'mexico': 'MX', 'chile': 'CL',
  'peru': 'PE', 'colombia': 'CO', 'venezuela, bolivarian republic of': 'VE',
  'venezuela': 'VE', 'ecuador': 'EC', 'uruguay': 'UY', 'paraguay': 'PY',
  'bolivia, plurinational state of': 'BO', 'bolivia': 'BO', 'guyana': 'GY', 'suriname': 'SR',
  'south africa': 'ZA', 'egypt': 'EG', 'nigeria': 'NG', 'kenya': 'KE',
  'ethiopia': 'ET', 'morocco': 'MA', 'algeria': 'DZ', 'tunisia': 'TN', 'ghana': 'GH',
  'cameroon': 'CM', 'senegal': 'SN', 'angola': 'AO', 'zimbabwe': 'ZW', 'zambia': 'ZM',
  'mozambique': 'MZ', 'tanzania, united republic of': 'TZ', 'tanzania': 'TZ',
  'uganda': 'UG', 'sudan': 'SD', 'libya': 'LY', 'mauritius': 'MU', 'madagascar': 'MG',
  'israel': 'IL', 'iran, islamic republic of': 'IR', 'iran': 'IR', 'iraq': 'IQ',
  'saudi arabia': 'SA', 'united arab emirates': 'AE', 'qatar': 'QA', 'kuwait': 'KW',
  'bahrain': 'BH', 'oman': 'OM', 'jordan': 'JO', 'lebanon': 'LB',
  'syrian arab republic': 'SY', 'syria': 'SY', 'yemen': 'YE', 'palestine, state of': 'PS',
  'kazakhstan': 'KZ', 'uzbekistan': 'UZ', 'turkmenistan': 'TM', 'kyrgyzstan': 'KG',
  'tajikistan': 'TJ', 'georgia': 'GE', 'armenia': 'AM', 'azerbaijan': 'AZ',
  'belarus': 'BY', 'moldova, republic of': 'MD', 'moldova': 'MD',
  'lithuania': 'LT', 'latvia': 'LV', 'estonia': 'EE', 'croatia': 'HR',
  'serbia': 'RS', 'slovenia': 'SI', 'bosnia and herzegovina': 'BA',
  'macedonia, the former yugoslav republic of': 'MK', 'north macedonia': 'MK',
  'albania': 'AL', 'kosovo': 'XK', 'montenegro': 'ME', 'luxembourg': 'LU',
  'malta': 'MT', 'cyprus': 'CY', 'panama': 'PA', 'costa rica': 'CR', 'cuba': 'CU',
  'dominican republic': 'DO', 'puerto rico': 'PR', 'jamaica': 'JM',
  'trinidad and tobago': 'TT', 'honduras': 'HN', 'guatemala': 'GT',
  'el salvador': 'SV', 'nicaragua': 'NI', 'belize': 'BZ', 'fiji': 'FJ',
  'papua new guinea': 'PG',
}

function _peerIso(p?: { country?: string; country_code?: string }): string {
  if (!p) return ''
  const c = (p.country_code || '').trim()
  if (/^[A-Za-z]{2}$/.test(c)) return c.toUpperCase()
  const n = (p.country || '').trim().toLowerCase()
  return (NAME_ISO[n] || '').toUpperCase()
}

const _regionNames = typeof Intl !== 'undefined' && 'DisplayNames' in Intl
  ? new Intl.DisplayNames(['zh-CN'], { type: 'region' })
  : null

export function flagFor(p?: { country?: string; country_code?: string }): string {
  const iso = _peerIso(p)
  return iso ? _iso2flag(iso) : ''
}

export function peerIsoCode(p?: { country?: string; country_code?: string }): string {
  return _peerIso(p)
}

export function countryZh(p?: { country?: string; country_code?: string }): string {
  const iso = _peerIso(p)
  if (iso) {
    try {
      const n = _regionNames?.of(iso)
      if (n) return n
    } catch { /* fall through */ }
    return iso
  }
  return p?.country?.trim() || ''
}
