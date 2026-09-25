import { useEffect, useRef, useState } from 'react'
import { Cloud, Plus, Trash2, ExternalLink, X, RefreshCw, Eye, EyeOff, Pencil } from 'lucide-react'
import { fetchRcloneRemotes, testRcloneRemote, fetchRcloneRemote, updateRcloneRemote, createRcloneRemote, deleteRcloneRemote, type RcloneRemote } from '../lib/api'
import { EmptyState } from '../components/ui'
import { useToast } from '../toast'
import RcloneBrowser from './netdisk/RcloneBrowser'

const FIELD_LABELS: Record<string, string> = {
  url: '地址',
  vendor: '服务商',
  user: '用户名',
  pass: '密码',
  provider: '云服务商',
  endpoint: '端点地址',
  bucket: 'Bucket',
  access_key_id: 'Access Key ID',
  secret_access_key: 'Secret Key',
  region: '区域',
  client_id: 'Client ID',
  client_secret: 'Client Secret',
}

const rcTypes: Record<string, { label: string; desc: string; fields: { k: string; ph: string; pw?: boolean }[]; hint?: string }> = {
  webdav: { label: 'WebDAV 通用', desc: 'Nextcloud / Synology / 任意 WebDAV 服务', fields: [
    { k: 'url', ph: 'https://dav.example.com' }, { k: 'vendor', ph: 'nextcloud / 留空' },
    { k: 'user', ph: '登录用户名' }, { k: 'pass', ph: '登录密码', pw: true },
  ] },
  s3: { label: 'S3 / R2 / COS / OSS', desc: 'Cloudflare R2 / AWS S3 / 腾讯 COS / 阿里 OSS', fields: [
    { k: 'provider', ph: 'Cloudflare / AWS / TencentCOS / Alibaba' },
    { k: 'endpoint', ph: 'https://<acct>.r2.cloudflarestorage.com' },
    { k: 'bucket', ph: 'Bucket 名称，如 lengxi' },
    { k: 'access_key_id', ph: 'Access Key ID' }, { k: 'secret_access_key', ph: 'Secret Key', pw: true },
    { k: 'region', ph: 'auto / us-east-1 / ap-northeast-1' },
  ], hint: 'R2 endpoint 不要带 bucket 路径；填写 bucket 后，应用会直接进入该 bucket，不要求账号具备 bucket 列表权限' },
  aliyundrive_open: { label: '阿里云盘', desc: '阿里云盘开放平台（需申请应用）', fields: [
    { k: 'client_id', ph: '开放平台 Client ID' }, { k: 'client_secret', ph: 'Client Secret', pw: true },
  ], hint: '需先到阿里云盘开放平台申请应用；保存后可能仍需在高级配置里补 OAuth 授权' },
  drive: { label: 'Google Drive', desc: 'Google Drive（OAuth 授权）', fields: [
    { k: 'client_id', ph: 'Google Cloud Client ID' }, { k: 'client_secret', ph: 'Client Secret', pw: true },
  ], hint: 'OAuth 授权需浏览器回调，建议走高级配置完成' },
  onedrive: { label: 'OneDrive', desc: 'Microsoft OneDrive（OAuth 授权）', fields: [
    { k: 'client_id', ph: '可留空' }, { k: 'client_secret', ph: '可留空', pw: true },
  ], hint: 'OAuth 授权需浏览器回调，建议走高级配置完成' },
}

export default function NetdiskView() {
  const toast = useToast()
  const [rc, setRc] = useState<{ online: boolean; remotes: RcloneRemote[] } | null>(null)
  const [rcTried, setRcTried] = useState(false)   // 区分"加载中"与"读取失败"，避免失败后永久骨架屏
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [editName, setEditName] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [type, setType] = useState('webdav')
  const [params, setParams] = useState<Record<string, string>>({})
  const [showPw, setShowPw] = useState<Set<string>>(new Set())
  const [secretFields, setSecretFields] = useState<Set<string>>(new Set())
  const originalParams = useRef<Record<string, string>>({})
  const [testing, setTesting] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)

  const reload = () => { setBusy(true); fetchRcloneRemotes().then((d) => { setRc(d); setRcTried(true); setBusy(false) }) }
  useEffect(() => { reload() }, [])

  // Auto-focus name input when dialog opens
  useEffect(() => {
    if (open) setTimeout(() => nameRef.current?.focus(), 80)
  }, [open])

  const togglePw = (k: string) => {
    setShowPw((s) => {
      const n = new Set(s)
      if (n.has(k)) n.delete(k)
      else n.add(k)
      return n
    })
  }

  const openCreate = () => {
    setEditName(null)
    setName('')
    setType('webdav')
    setParams({})
    setShowPw(new Set())
    setSecretFields(new Set())
    originalParams.current = {}
    setOpen(true)
  }

  const openEdit = async (n: string) => {
    setBusy(true)
    const config = await fetchRcloneRemote(n)
    setBusy(false)
    if (!config) { toast(`读取 ${n} 配置失败`, 'bad'); return }
    if (!rcTypes[config.type]) {
      toast(`类型 ${config.type} 请在高级配置中修改`, 'bad')
      return
    }
    setEditName(n)
    setName(n)
    setType(config.type)
    setParams(config.params)
    setShowPw(new Set())
    setSecretFields(new Set(config.secretFields))
    originalParams.current = config.params
    setOpen(true)
  }

  const closeForm = () => {
    setOpen(false)
    setEditName(null)
    setName('')
    setParams({})
    setShowPw(new Set())
    setSecretFields(new Set())
    originalParams.current = {}
  }

  const submit = async () => {
    const n = name.trim()
    if (!n) { toast('请输入网盘名称', 'bad'); return }
    if (!rcTypes[type]) { toast('未知类型', 'bad'); return }
    if (editName) {
      const keys = new Set([...Object.keys(originalParams.current), ...Object.keys(params)])
      const changed = [...keys].some((key) => {
        if (secretFields.has(key) && !(params[key] || '').trim()) return false
        return (params[key] ?? '') !== (originalParams.current[key] ?? '')
      })
      const renamed = n !== editName
      if (!changed && !renamed) {
        toast('配置未修改', 'ok')
        closeForm()
        return
      }
      const r = await updateRcloneRemote(editName, params, n)
      toast(r.ok ? `已保存 ${n}` : `保存失败：${r.detail}`, r.ok ? 'ok' : 'bad')
      if (r.ok) { closeForm(); reload() }
      return
    }
    const r = await createRcloneRemote(n, type, params)
    toast(r.ok ? `已创建 ${n}` : `创建失败：${r.detail}`, r.ok ? 'ok' : 'bad')
    if (r.ok) { closeForm(); reload() }
  }
  const test = async (n: string) => {
    setTesting(n)
    const r = await testRcloneRemote(n)
    const suffix = r.ok && r.latencyMs !== undefined ? ` · ${r.latencyMs} ms` : ''
    toast(r.ok ? `连接成功${suffix}` : `连接失败：${r.detail}`, r.ok ? 'ok' : 'bad')
    setTesting(null)
  }
  const del = async (n: string) => {
    if (!window.confirm(`删除网盘「${n}」？`)) return
    const r = await deleteRcloneRemote(n)
    toast(r.ok ? `已删除 ${n}` : `删除失败：${r.detail}`, r.ok ? 'ok' : 'bad')
    if (r.ok) reload()
  }

  // Keyboard: Enter to submit
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col md:h-screen">
      <header className="flex min-w-0 flex-col gap-3 border-b border-line px-4 py-4 sm:flex-row sm:items-center sm:justify-between md:px-10 md:py-5">
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm text-dim">
          <span className="text-fg">网盘对接</span>
          <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${rc?.online ? 'border-teal-400/30 bg-teal-400/10 text-teal-300' : 'border-rose-400/30 bg-rose-400/10 text-rose-300'}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${rc?.online ? 'bg-teal-400' : 'bg-rose-400'}`} />
            {rc?.online ? 'rclone 在线' : 'rclone 未接入'}
          </span>
          {rc?.online && rc.remotes.length > 0 && (
            <span className="num text-[11px] text-teal-300">{rc.remotes.length} 个网盘</span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button onClick={reload} title="刷新" aria-label="刷新"
            className="grid h-8 w-8 place-items-center rounded-lg border border-line bg-white/4 text-dim hover:text-fg">
            <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
          </button>
          <a href="/rclone/" target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-white/4 px-3 py-2 text-xs text-dim hover:text-fg">
            <ExternalLink size={13} /> 高级配置 · OAuth 授权
          </a>
          <button onClick={openCreate} disabled={!rc?.online}
            className="inline-flex items-center gap-1.5 rounded-lg grad-bar px-4 py-2 text-sm font-medium text-ink disabled:opacity-40">
            <Plus size={15} /> 添加网盘
          </button>
        </div>
      </header>

      <main className="min-w-0 flex-1 md:overflow-y-auto px-4 py-5 md:px-10 md:py-8">
        {!rc ? (
          rcTried
            ? <EmptyState icon={<Cloud size={20} />} title="读取失败" hint="无法连接后端接口，请检查 Aurora 服务后重试" />
            : <div className="flex flex-col gap-2"><div className="skeleton h-16 rounded-xl" /><div className="skeleton h-16 rounded-xl" /></div>
        ) : !rc.online ? (
          <EmptyState icon={<Cloud size={20} />} title="rclone 服务未运行" hint="systemctl start rclone-rcd 后自动显示" />
        ) : rc.remotes.length === 0 ? (
          <EmptyState icon={<Cloud size={20} />} title="尚未配置网盘" hint="点右上角「添加网盘」选择类型创建；OAuth 类网盘走高级配置" />
        ) : (
          <div className="grid max-w-3xl grid-cols-1 gap-3 sm:grid-cols-2">
            {rc.remotes.map((r) => (
              <div key={r.name} className="card-in flex items-center gap-3 rounded-xl border border-line bg-white/3 px-4 py-3">
                <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg grad-bar text-ink"><Cloud size={18} /></div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-fg">{r.name}</div>
                  <div className="num text-[11px] text-dim">{r.type || '未知类型'}</div>
                  <div className="mt-0.5 text-[11px] text-teal-300">已连接 · 自动挂载</div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button title="测试连接" aria-label="测试连接" onClick={() => test(r.name)} disabled={testing !== null || busy}
                    className="grid h-8 w-8 place-items-center rounded-md border border-line text-dim transition-colors hover:border-aurora-2/40 hover:text-aurora-1 disabled:opacity-40">
                    <RefreshCw size={14} className={testing === r.name ? 'animate-spin' : ''} />
                  </button>
                  <button title="修改配置" aria-label="修改配置" onClick={() => openEdit(r.name)} disabled={testing !== null || busy}
                    className="grid h-8 w-8 place-items-center rounded-md border border-line text-dim transition-colors hover:border-aurora-2/40 hover:text-aurora-1 disabled:opacity-40">
                    <Pencil size={14} />
                  </button>
                  <button title="删除" aria-label="删除" onClick={() => del(r.name)} disabled={testing !== null || busy}
                    className="grid h-8 w-8 place-items-center rounded-md border border-line text-dim transition-colors hover:border-rose-400/40 hover:text-rose-300 disabled:opacity-40">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        {rc && rc.online && rc.remotes.length > 0 && <RcloneBrowser remotes={rc.remotes} />}
        <div className="mt-6 max-w-3xl text-[11px] leading-relaxed text-dim/70">
          网盘配置由 rclone 服务账户保存；保存后 ≤2 秒自动生效，管理台与监控大屏的挂载来源变为「rclone 真实」。WebDAV / S3 / 阿里云盘可表单直配；Google Drive / OneDrive 需浏览器 OAuth 回调，请在高级配置（rclone WebGUI）中完成。
        </div>
      </main>

      {open && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black/60 backdrop-blur-sm" onClick={closeForm}>
          <div className="flex min-h-full items-center justify-center p-4">
            <div className="panel w-full max-w-md px-6 py-5" onClick={(e) => e.stopPropagation()} onKeyDown={onKey}>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-medium"><Cloud size={16} className="text-aurora-1" /> {editName ? '修改网盘' : '添加网盘'}</span>
                <button onClick={closeForm} aria-label="关闭" title="关闭" className="text-dim hover:text-fg"><X size={16} /></button>
              </div>

            <div className="mt-4 grid gap-4">
              {/* 名称 */}
              <div>
                <label className="block text-[11px] uppercase tracking-[0.2em] text-dim">名称</label>
                <input ref={nameRef} value={name} onChange={(e) => setName(e.target.value)} placeholder="如 aliyun / gd / r2"
                  className="mt-1.5 w-full rounded-lg border border-line bg-white/4 px-3 py-2.5 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none" />
              </div>

              {/* 类型 */}
              <div>
                <label className="block text-[11px] uppercase tracking-[0.2em] text-dim">类型</label>
                <div className="mt-1.5 grid grid-cols-1 gap-1.5">
                  {Object.entries(rcTypes).map(([k, v]) => (
                    <label key={k}
                      className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                        type === k
                          ? 'border-aurora-2/50 bg-aurora-2/8 text-fg'
                          : 'border-line bg-white/4 text-dim hover:border-line/60 hover:text-fg'
                      }`}>
                      <input type="radio" name="rc-type" value={k} checked={type === k}
                        disabled={!!editName}
                        onChange={() => { setType(k); setParams({}); setShowPw(new Set()); setSecretFields(new Set()) }}
                        className="sr-only" />
                      <span className={`h-3 w-3 shrink-0 rounded-full border-2 ${
                        type === k ? 'border-aurora-2 bg-aurora-2' : 'border-dim/40'
                      }`} />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium">{v.label}</div>
                        <div className="text-[11px] text-dim/70">{v.desc}</div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>

              {/* 参数字段 */}
              {rcTypes[type]?.fields.length > 0 && (
                <div className="space-y-3 rounded-lg border border-line bg-white/3 px-3.5 py-3.5">
                  <div className="text-[11px] font-medium uppercase tracking-[0.2em] text-dim/60">连接参数</div>
                  {rcTypes[type].fields.map((f) => {
                    const isPw = f.pw && !showPw.has(f.k)
                    return (
                      <div key={f.k}>
                        <label className="block text-[11px] text-dim/80">{FIELD_LABELS[f.k] || f.k}</label>
                        <div className="relative mt-1">
                          <input type={isPw ? 'password' : 'text'}
                            value={params[f.k] ?? ''}
                            onChange={(e) => setParams((m) => ({ ...m, [f.k]: e.target.value }))}
                            placeholder={editName && secretFields.has(f.k) ? '已保存，留空保持不变' : f.ph}
                            className="w-full rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none disabled:opacity-60"
                            style={f.pw ? { paddingRight: '2.25rem' } : undefined} />
                          {f.pw && (
                            <button type="button" tabIndex={-1} onClick={() => togglePw(f.k)}
                              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-dim hover:text-fg">
                              {showPw.has(f.k) ? <EyeOff size={14} /> : <Eye size={14} />}
                            </button>
                          )}
                        </div>
                      </div>
                    )
                  })}
                  {rcTypes[type]?.hint && (
                    <div className="text-[11px] leading-relaxed text-dim/70 border-t border-line/50 pt-2.5 mt-2.5">
                      {rcTypes[type].hint}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="mt-5 flex justify-end gap-3">
              <button onClick={closeForm}
                className="rounded-lg border border-line bg-white/4 px-4 py-2.5 text-sm text-dim hover:text-fg transition-colors">取消</button>
              <button onClick={submit} disabled={!name.trim()}
                className="rounded-lg grad-bar px-4 py-2.5 text-sm font-medium text-ink disabled:opacity-40 transition-opacity">{editName ? '保存修改' : '创建'}</button>
            </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
