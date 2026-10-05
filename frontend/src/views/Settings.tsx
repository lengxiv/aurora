import { useEffect, useState } from 'react'
import { Server, Database, ShieldCheck, LogOut, RefreshCw, Bell, KeyRound, Smartphone, X, ListFilter, Tags, Plus, Trash2, Eye, Play, Save, FolderCog, Undo2, FolderOutput } from 'lucide-react'
import { fetchInfo, fetchSettings, saveSettings, testTelegram, fetchSessions, revokeSession, changePassword, fetchQbitQueueSettings, saveQbitQueueSettings, fetchTorrentLabels, createTorrentCategory, deleteTorrentCategory, createTorrentTags, deleteTorrentTags, fetchTorrentMappings, saveTorrentMappings, fetchMediaDirs, fetchTorrentPolicies, saveTorrentPolicies, previewTorrentPolicies, applyTorrentPolicies, fetchPolicyUndo, undoPolicy, fetchMediaOrganize, saveMediaOrganize, previewMediaOrganize, applyMediaOrganize, fetchRcloneRemotes, checkUpdate, type AuthSession, type QbitQueueSettings, type QbitLabels, type TorrentCategoryMapping, type TorrentPolicies, type TorrentPolicy, type TorrentPolicyPreview, type PolicyUndoItem, type RcloneRemote, type MediaDir, type UpdateStatus, type MediaOrgSettings, type MediaOrgRule, type MediaOrgPreviewItem } from '../lib/api'
import { SC_KEY, SC_VAL, Switch } from '../components/ui'
import { useToast } from '../toast'

interface Info {
  name?: string
  version?: string
  providers?: Record<string, { url?: string; online?: boolean; auth?: boolean }>
  sources?: Record<string, string>
  auth?: string
}

interface SettingsT {
  alerts?: { torrent?: boolean; disk?: boolean; diskWarn?: number }
  daily?: { enabled?: boolean; time?: string }
  tg?: { enabled?: boolean; tokens?: { name?: string; token?: string; chat_id?: string }[] }
  policies?: TorrentPolicies
}

// 预演（dry run）状态 → 中文文案
const POLICY_DRY_STATUS: Record<string, string> = {
  would_pause: '将暂停', would_notify: '将通知', would_transfer: '将转存',
  would_remove: '将移除', would_error: '配置有误', already_notified: '今日已通知',
  protected: '受保护', skipped: '未允许自动删除',
}

// 整理执行/预览状态 → 中文文案
const ORG_STATUS: Record<string, string> = {
  would_organize: '将整理', already_done: '已整理', conflict: '目标已存在',
  skipped: '跳过', done: '已入库', error: '失败',
}
const ORG_MODE_ZH: Record<string, string> = { hardlink: '硬链接', copy: '拷贝', move: '移动' }

export default function Settings({ onLogout }: { onLogout: () => void }) {
  const toast = useToast()
  const [info, setInfo] = useState<Info | null>(null)
  const [st, setSt] = useState<SettingsT | null>(null)
  const [load, trigger] = useState(0)
  const [sessions, setSessions] = useState<AuthSession[]>([])
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' })
  const [qbitQueue, setQbitQueue] = useState<{ online: boolean; settings: QbitQueueSettings | null; detail?: string } | null>(null)
  const [queueTried, setQueueTried] = useState(false)
  const [qbitBusy, setQbitBusy] = useState(false)
  const [qbitLabels, setQbitLabels] = useState<QbitLabels | null>(null)
  const [labelsBusy, setLabelsBusy] = useState(false)
  const [categoryMappings, setCategoryMappings] = useState<Record<string, TorrentCategoryMapping>>({})
  const [mappingDirs, setMappingDirs] = useState<MediaDir[]>([])
  const [mappingBusy, setMappingBusy] = useState(false)
  const [newCategory, setNewCategory] = useState('')
  const [newCategoryPath, setNewCategoryPath] = useState('')
  const [newTag, setNewTag] = useState('')
  const [policies, setPolicies] = useState<TorrentPolicies | null>(null)
  const [policiesTried, setPoliciesTried] = useState(false)   // 区分"加载中"与"加载失败"，避免永久卡在加载文案
  const [policyBusy, setPolicyBusy] = useState(false)
  const [policyPreview, setPolicyPreview] = useState<TorrentPolicyPreview[] | null>(null)
  const [policyDryRun, setPolicyDryRun] = useState<TorrentPolicyPreview[] | null>(null)
  const [policyUndo, setPolicyUndo] = useState<PolicyUndoItem[]>([])
  const [org, setOrg] = useState<MediaOrgSettings | null>(null)
  const [orgTried, setOrgTried] = useState(false)
  const [orgBusy, setOrgBusy] = useState(false)
  const [orgPreview, setOrgPreview] = useState<MediaOrgPreviewItem[] | null>(null)
  const [orgDry, setOrgDry] = useState(false)
  const [policyRemotes, setPolicyRemotes] = useState<RcloneRemote[]>([])
  const [update, setUpdate] = useState<UpdateStatus | null>(null)
  const [updateBusy, setUpdateBusy] = useState(false)

  const refreshUpdate = async (force: boolean) => {
    setUpdateBusy(true)
    setUpdate(await checkUpdate(force))
    setUpdateBusy(false)
  }

  useEffect(() => {
    fetchInfo().then(setInfo)
    fetchSettings().then(setSt)
    fetchSessions().then(setSessions)
    fetchQbitQueueSettings().then((v) => { setQbitQueue(v); setQueueTried(true) })
    fetchTorrentLabels().then(setQbitLabels)
    fetchTorrentMappings().then((items) => setCategoryMappings(Object.fromEntries(items.map((item) => [item.category, item]))))
    fetchMediaDirs().then(setMappingDirs)
    fetchTorrentPolicies().then((v) => { setPolicies(v); setPoliciesTried(true) })
    fetchRcloneRemotes().then((result) => setPolicyRemotes(result?.remotes || []))
    fetchPolicyUndo().then(setPolicyUndo)
    fetchMediaOrganize().then((v) => { setOrg(v); setOrgTried(true) })
    // 后端对 GitHub 结果缓存 30 分钟，打开设置页的自动检查不会打爆 API 限额
    void refreshUpdate(false)
  }, [load])

  const p = info?.providers || {}
  const updateHint = !update
    ? '对比 GitHub Releases 获取最新版本'
    : !update.ok
      ? (update.detail || '检查失败')
      : update.update_available
        ? `当前 ${update.current || '—'}，最新 ${update.latest || '—'}`
        : `已是最新（${update.latest || update.current || '—'}）`
  const save = async () => {
    // 设置未加载成功时禁止保存：提交 {} 会被后端按默认值重建，
    // 等于把 Telegram token、告警开关、日报时间全部重置
    if (!st) { toast('设置尚未加载，无法保存', 'bad'); return }
    const d = await saveSettings(st as Record<string, unknown>)
    if (d) { setSt(d); toast('设置已保存') } else { toast('保存失败', 'bad') }
  }
  const patchQbitQueue = (patch: Partial<QbitQueueSettings>) => setQbitQueue((current) => current?.settings
    ? { ...current, settings: { ...current.settings, ...patch } }
    : current)
  const saveQueue = async () => {
    if (!qbitQueue?.settings) return
    setQbitBusy(true)
    const result = await saveQbitQueueSettings(qbitQueue.settings)
    setQbitBusy(false)
    if (result.ok && result.settings) {
      setQbitQueue((current) => current ? { ...current, online: true, settings: result.settings ?? null } : current)
      toast('qBittorrent 队列设置已保存', 'ok')
    } else toast(result.detail || '保存失败', 'bad')
  }
  const reloadLabels = async () => {
    setLabelsBusy(true)
    setQbitLabels(await fetchTorrentLabels())
    setLabelsBusy(false)
  }
  const patchCategoryMapping = (category: string, patch: Partial<TorrentCategoryMapping>) => setCategoryMappings((current) => ({
    ...current,
    [category]: {
      category,
      local_path: patch.local_path ?? current[category]?.local_path ?? '',
      destination_remote: patch.destination_remote ?? current[category]?.destination_remote ?? '',
      destination_path: patch.destination_path ?? current[category]?.destination_path ?? '',
    },
  }))
  const saveMappings = async () => {
    setMappingBusy(true)
    const result = await saveTorrentMappings(Object.values(categoryMappings))
    setMappingBusy(false)
    toast(result.ok ? '分类默认映射已保存' : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) setCategoryMappings(Object.fromEntries(result.mappings.map((item) => [item.category, item])))
  }
  const addCategory = async () => {
    if (!newCategory.trim()) return
    setLabelsBusy(true)
    const result = await createTorrentCategory(newCategory.trim(), newCategoryPath.trim())
    setLabelsBusy(false)
    toast(result.ok ? '分类已创建' : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) { setNewCategory(''); setNewCategoryPath(''); await reloadLabels() }
  }
  const removeCategory = async (name: string) => {
    if (!window.confirm(`删除分类「${name}」？已有任务不会删除。`)) return
    setLabelsBusy(true)
    const result = await deleteTorrentCategory(name)
    setLabelsBusy(false)
    toast(result.ok ? '分类已删除' : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) await reloadLabels()
  }
  const addTags = async () => {
    const values = newTag.split(',').map((item) => item.trim()).filter(Boolean)
    if (!values.length) return
    setLabelsBusy(true)
    const result = await createTorrentTags(values)
    setLabelsBusy(false)
    toast(result.ok ? '标签已创建' : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) { setNewTag(''); await reloadLabels() }
  }
  const removeTag = async (tag: string) => {
    if (!window.confirm(`删除标签「${tag}」？已有任务上的标签不会自动移除。`)) return
    setLabelsBusy(true)
    const result = await deleteTorrentTags([tag])
    setLabelsBusy(false)
    toast(result.ok ? '标签已删除' : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) await reloadLabels()
  }
  const patchPolicy = (id: string, patch: Partial<TorrentPolicy>) => setPolicies((current) => current ? { ...current, rules: current.rules.map((rule) => rule.id === id ? { ...rule, ...patch } : rule) } : current)
  const addPolicy = () => setPolicies((current) => current ? { ...current, rules: [...current.rules, { id: `local-${Date.now()}`, name: `新策略 ${current.rules.length + 1}`, category: '', hash: '', enabled: true, action: 'pause', min_seed_minutes: 0, max_seed_minutes: -1, max_inactive_minutes: -1, max_ratio: -1, allow_delete: false, delete_files: false, destination_remote: '', destination_path: '' }] } : current)
  const savePolicies = async () => {
    if (!policies) return
    setPolicyBusy(true)
    const result = await saveTorrentPolicies(policies)
    setPolicyBusy(false)
    if (result.ok && result.policies) { setPolicies(result.policies); toast('做种策略已保存', 'ok') } else toast(result.detail, 'bad')
  }
  const previewPolicies = async () => {
    setPolicyBusy(true)
    const result = await previewTorrentPolicies()
    setPolicyBusy(false)
    setPolicyPreview(result?.items || [])
    toast(result ? `预览完成：${result.items.length} 个任务命中` : '预览失败', result ? 'ok' : 'bad')
  }
  const dryRunPolicies = async () => {
    if (!policies) return
    setPolicyBusy(true)
    const result = await applyTorrentPolicies(true)
    setPolicyBusy(false)
    if (result.ok && result.items) { setPolicyDryRun(result.items); toast(`预演完成：${result.items.length} 项待执行，未产生任何变更`, 'ok') } else { setPolicyDryRun(null); toast(result.detail, 'bad') }
  }
  const applyPolicies = async () => {
    if (!window.confirm('将按当前策略暂停或移除命中任务，删除文件还需策略显式允许。确定继续吗？')) return
    setPolicyBusy(true)
    const result = await applyTorrentPolicies()
    setPolicyBusy(false)
    toast(result.ok ? `策略已应用，执行 ${result.applied || 0} 项` : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) { setPolicyPreview(result.items || []); setPolicyDryRun(null); fetchPolicyUndo().then(setPolicyUndo) }
  }
  const runUndo = async (id: string) => {
    if (!window.confirm('撤销会把文件恢复到原位并重新添加任务（原路径被占用时会中止）。继续吗？')) return
    const result = await undoPolicy(id)
    toast(result.ok ? (result.detail || '已撤销') : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) fetchPolicyUndo().then(setPolicyUndo)
  }
  const patchOrgRule = (id: string, patch: Partial<MediaOrgRule>) => setOrg((current) => current
    ? { ...current, rules: current.rules.map((rule) => rule.id === id ? { ...rule, ...patch } : rule) }
    : current)
  const addOrgRule = () => setOrg((current) => current ? {
    ...current,
    rules: [...current.rules, { id: `local-${Date.now()}`, name: `新规则 ${current.rules.length + 1}`, category: '', enabled: true, target_dir: 'library', mode: 'hardlink', use_subfolder: true, min_size_mb: 0 }],
  } : current)
  const saveOrg = async () => {
    if (!org) return
    setOrgBusy(true)
    const saved = await saveMediaOrganize({ enabled: org.enabled, interval: org.interval, jellyfin_refresh: org.jellyfin_refresh, rules: org.rules })
    setOrgBusy(false)
    if (saved?.ok) { setOrg(saved); toast('整理规则已保存', 'ok') } else toast('保存失败', 'bad')
  }
  const previewOrg = async () => {
    setOrgBusy(true)
    const result = await previewMediaOrganize()
    setOrgBusy(false)
    setOrgDry(false)
    if (result) { setOrgPreview(result.items); toast(`预览完成：${result.items.length} 个候选`, result.items.length ? 'ok' : 'bad') } else toast('预览失败：后端未连接或 qBittorrent 未接入', 'bad')
  }
  const runOrg = async (dryRun: boolean) => {
    if (!dryRun && !window.confirm('将按规则把已完成的任务整理到媒体库目录（硬链接/拷贝/移动取决于规则设置）。继续吗？')) return
    setOrgBusy(true)
    const result = await applyMediaOrganize(dryRun)
    setOrgBusy(false)
    if (result.ok) {
      setOrgPreview(result.items || [])
      setOrgDry(dryRun)
      const tail = result.jellyfinRefreshed ? '，已刷新 Jellyfin 库' : ''
      toast(dryRun ? `预演完成：${result.items?.length || 0} 项待整理，未产生变更` : `整理完成：${result.organized || 0} 项${tail}`, 'ok')
    } else toast(result.detail, 'bad')
  }

  return (
    <div className="min-w-0 px-4 py-6 md:px-10 md:py-8">
      <div className="text-[11px] uppercase tracking-[0.3em] text-dim">系统</div>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight">设置</h1>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="panel px-6 py-5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Server size={16} className="text-aurora-1" /> 服务
          </div>
          <dl className="mt-4 space-y-3 text-sm">
            <div className="flex justify-between"><dt className="text-dim">名称</dt><dd className="num">{info?.name ?? '—'}</dd></div>
            <div className="flex justify-between"><dt className="text-dim">后端版本</dt><dd className="num">{info?.version ?? '—'}</dd></div>
            <div className="flex justify-between"><dt className="text-dim">前端构建</dt><dd className="num">{__APP_VERSION__}</dd></div>
            <div className="flex justify-between"><dt className="text-dim">鉴权</dt><dd className="num">{info?.auth ?? '—'}</dd></div>
          </dl>
          <div className="mt-4 border-t border-line pt-4">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm text-fg">检查更新</div>
                <div className="num mt-0.5 truncate text-[11px] text-dim" title={updateHint}>{updateHint}</div>
              </div>
              <button type="button" onClick={() => void refreshUpdate(true)} disabled={updateBusy} title="对比 GitHub Releases 检查新版本" aria-label="检查更新" className="grid h-8 w-8 shrink-0 place-items-center rounded-md grad-bar text-ink disabled:opacity-40">
                <RefreshCw size={13} className={updateBusy ? 'animate-spin' : ''} />
              </button>
            </div>
            {update?.ok && update.update_available && update.url && (
              <a href={update.url} target="_blank" rel="noreferrer" className="num mt-2 inline-block text-[11px] text-teal-300 underline-offset-2 hover:underline">
                发现新版本 {update.latest || update.tag} → 查看发布说明
              </a>
            )}
          </div>
        </section>

        <section className="panel px-6 py-5">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Database size={16} className="text-aurora-2" /> 数据源
          </div>
          <div className="mt-4 flex flex-col gap-2.5">
            {Object.entries(p).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between rounded-lg border border-line bg-white/3 px-3 py-2">
                <div>
                  <div className="text-sm text-fg">{k}</div>
                  <div className="num text-[11px] text-dim">{v?.url ?? ''}</div>
                </div>
                <span className={`num text-[11px] ${v?.online ? 'text-teal-300' : 'text-dim'}`}>
                  {v?.online ? '在线' : (v?.url ? '离线' : '未配置')}{v?.auth ? ' · 已鉴权' : ''}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium"><FolderCog size={16} className="text-aurora-1" /> 分类默认目标</div>
              <div className="mt-1 text-[11px] text-dim">添加任务时未手动填写保存目录或网盘目标，自动使用这里的映射。</div>
            </div>
            <button type="button" onClick={() => void saveMappings()} disabled={!qbitLabels?.online || mappingBusy} title="保存分类默认映射" aria-label="保存分类默认映射" className="grid h-8 w-8 place-items-center rounded-md grad-bar text-ink disabled:opacity-40"><Save size={13} /></button>
          </div>
          {!qbitLabels?.online ? <div className="mt-4 text-sm text-dim">qBittorrent 未接入，无法读取分类。</div> : qbitLabels.categories.length === 0 ? <div className="mt-4 text-sm text-dim">暂无分类。先在上方创建分类，再配置默认目标。</div> : <div className="mt-4 flex flex-col gap-3">
            {qbitLabels.categories.map((item) => {
              const mapping = categoryMappings[item.name] || { category: item.name, local_path: '', destination_remote: '', destination_path: '' }
              return <div key={item.name} className="rounded-lg border border-line bg-white/3 p-4">
                <div className="flex items-center justify-between gap-2"><div className="text-sm text-fg">{item.name}</div><span className="text-[10px] text-dim">未填写项沿用 qBittorrent/本地默认</span></div>
                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                  <label className="text-[11px] text-dim">本地保存目录
                    <select value={mapping.local_path} onChange={(e) => patchCategoryMapping(item.name, { local_path: e.target.value })} disabled={mappingBusy} className="aurora-select mt-1 w-full rounded-md border border-line px-2 py-1.5 text-xs text-fg focus:outline-none disabled:opacity-60">
                      <option value="">使用 qBittorrent 分类目录</option>
                      {mappingDirs.map((dir) => <option key={dir.path} value={dir.path}>{dir.path}</option>)}
                    </select>
                  </label>
                  <label className="text-[11px] text-dim">完成后转存网盘
                    <select value={mapping.destination_remote} onChange={(e) => patchCategoryMapping(item.name, { destination_remote: e.target.value, destination_path: e.target.value ? mapping.destination_path : '' })} disabled={mappingBusy} className="aurora-select mt-1 w-full rounded-md border border-line px-2 py-1.5 text-xs text-fg focus:outline-none disabled:opacity-60">
                      <option value="">不自动转存</option>
                      {policyRemotes.map((remote) => <option key={remote.name} value={remote.name}>{remote.name} · {remote.type || 'remote'}</option>)}
                    </select>
                  </label>
                  <label className="text-[11px] text-dim">网盘目标目录
                    <input value={mapping.destination_path} onChange={(e) => patchCategoryMapping(item.name, { destination_path: e.target.value })} disabled={mappingBusy || !mapping.destination_remote} placeholder="例如：电影/2026" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none disabled:opacity-50" />
                  </label>
                </div>
              </div>
            })}
          </div>}
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium"><Tags size={16} className="text-aurora-1" /> qBittorrent 分类与标签</div>
            <button type="button" onClick={() => void reloadLabels()} disabled={labelsBusy} title="刷新分类标签" aria-label="刷新分类标签" className="grid h-8 w-8 place-items-center rounded-md border border-line bg-white/4 text-dim hover:text-fg disabled:opacity-40"><RefreshCw size={14} className={labelsBusy ? 'animate-spin' : ''} /></button>
          </div>
          {!qbitLabels || !qbitLabels.online ? <div className="mt-4 text-sm text-dim">qBittorrent 未接入，无法管理分类和标签。</div> : <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded-lg border border-line bg-white/3 p-4">
              <div className="flex items-center gap-2 text-xs text-dim"><FolderCog size={13} />分类</div>
              <div className="mt-3 flex gap-2">
                <input value={newCategory} onChange={(e) => setNewCategory(e.target.value)} placeholder="分类名称" className="min-w-0 flex-1 rounded-md border border-line bg-white/4 px-2.5 py-2 text-xs text-fg placeholder:text-dim/60 focus:outline-none" />
                <input value={newCategoryPath} onChange={(e) => setNewCategoryPath(e.target.value)} placeholder="保存目录（可选）" className="min-w-0 flex-1 rounded-md border border-line bg-white/4 px-2.5 py-2 text-xs text-fg placeholder:text-dim/60 focus:outline-none" />
                <button type="button" onClick={() => void addCategory()} disabled={labelsBusy || !newCategory.trim()} title="创建分类" aria-label="创建分类" className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-line bg-white/4 text-dim hover:text-fg disabled:opacity-40"><Plus size={14} /></button>
              </div>
              <div className="mt-3 flex flex-col gap-1.5">
                {qbitLabels.categories.length === 0 ? <div className="text-xs text-dim/70">暂无分类，任务会使用默认下载目录。</div> : qbitLabels.categories.map((item) => <div key={item.name} className="flex items-center gap-2 rounded-md border border-line/70 bg-white/2 px-2.5 py-2"><div className="min-w-0 flex-1"><div className="truncate text-xs text-fg">{item.name}</div><div className="truncate text-[10px] text-dim">{item.save_path || '/downloads'}</div></div><button type="button" onClick={() => void removeCategory(item.name)} disabled={labelsBusy} title="删除分类" aria-label="删除分类" className="grid h-6 w-6 place-items-center rounded text-dim hover:text-rose-300 disabled:opacity-40"><Trash2 size={12} /></button></div>)}
              </div>
            </div>
            <div className="rounded-lg border border-line bg-white/3 p-4">
              <div className="flex items-center gap-2 text-xs text-dim"><Tags size={13} />用户标签</div>
              <div className="mt-3 flex gap-2">
                <input value={newTag} onChange={(e) => setNewTag(e.target.value)} placeholder="标签，可用逗号分隔" className="min-w-0 flex-1 rounded-md border border-line bg-white/4 px-2.5 py-2 text-xs text-fg placeholder:text-dim/60 focus:outline-none" />
                <button type="button" onClick={() => void addTags()} disabled={labelsBusy || !newTag.trim()} title="创建标签" aria-label="创建标签" className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-line bg-white/4 text-dim hover:text-fg disabled:opacity-40"><Plus size={14} /></button>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {qbitLabels.tags.length === 0 ? <div className="text-xs text-dim/70">暂无用户标签。</div> : qbitLabels.tags.filter((item) => !item.startsWith('aurora-')).map((tag) => <span key={tag} className="inline-flex items-center gap-1 rounded-md border border-line bg-white/4 px-2 py-1 text-xs text-fg">{tag}<button type="button" onClick={() => void removeTag(tag)} disabled={labelsBusy} title={`删除标签 ${tag}`} aria-label={`删除标签 ${tag}`} className="text-dim hover:text-rose-300 disabled:opacity-40"><X size={11} /></button></span>)}
              </div>
              <div className="mt-3 text-[10px] leading-relaxed text-dim/70">`aurora-` 开头的系统标签由 Aurora 管理，不能删除或覆盖。</div>
            </div>
          </div>}
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium"><ListFilter size={16} className="text-aurora-1" /> 做种策略与生命周期</div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={addPolicy} disabled={!policies || policyBusy} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40"><Plus size={12} />新增规则</button>
              <button type="button" onClick={() => void previewPolicies()} disabled={!policies || policyBusy} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40"><Eye size={12} />预览</button>
              <button type="button" onClick={() => void dryRunPolicies()} disabled={!policies || policyBusy} title="完整走一遍判定但不执行任何操作" className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40"><Play size={12} />预演</button>
              <button type="button" onClick={() => void savePolicies()} disabled={!policies || policyBusy} title="保存做种策略" aria-label="保存做种策略" className="grid h-8 w-8 place-items-center rounded-md grad-bar text-ink disabled:opacity-40"><Save size={13} /></button>
            </div>
          </div>
          {!policies ? <div className="mt-4 text-sm text-dim">{policiesTried ? '读取失败：后端未连接或 qBittorrent 未接入' : '正在读取做种策略…'}</div> : <>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-400/20 bg-amber-400/5 px-4 py-3">
              <div><div className="text-sm text-fg">启用后台自动检查</div><div className="text-[11px] text-dim">默认关闭；开启后按间隔检查命中规则。自动删除仍需规则允许。</div></div>
              <div className="flex items-center gap-3"><label className="flex items-center gap-1.5 text-xs text-dim">间隔 <input type="number" min={60} max={86400} value={policies.interval} onChange={(e) => setPolicies({ ...policies, interval: Math.max(60, Number(e.target.value) || 60) })} className="num w-20 rounded-md border border-line bg-white/4 px-2 py-1 text-right text-xs text-fg focus:outline-none" /> 秒</label><Switch checked={policies.enabled} onChange={(v) => setPolicies({ ...policies, enabled: v })} /></div>
            </div>
            {policies.rules.length === 0 ? <div className="py-8 text-center text-sm text-dim">暂无策略。先新增一条规则，再保存或预览。</div> : <div className="mt-3 flex flex-col gap-3">
              {policies.rules.map((rule) => <div key={rule.id} className="rounded-lg border border-line bg-white/3 p-4">
                <div className="flex flex-wrap items-center gap-2"><input value={rule.name} onChange={(e) => patchPolicy(rule.id, { name: e.target.value })} className="min-w-[150px] flex-1 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-sm text-fg focus:outline-none" /><select value={rule.action} onChange={(e) => patchPolicy(rule.id, { action: e.target.value as TorrentPolicy['action'] })} className="aurora-select rounded-md border border-line px-2 py-1.5 text-xs text-fg focus:outline-none"><option value="pause">达到条件后暂停</option><option value="notify">达到条件后通知</option><option value="transfer">达到条件后转存网盘</option><option value="remove">达到条件后移除任务</option></select><Switch checked={rule.enabled} onChange={(v) => patchPolicy(rule.id, { enabled: v })} /><button type="button" onClick={() => setPolicies({ ...policies, rules: policies.rules.filter((item) => item.id !== rule.id) })} title="删除策略" aria-label="删除策略" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:text-rose-300"><Trash2 size={13} /></button></div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-6"><label className="text-[11px] text-dim">匹配分类<input value={rule.category} onChange={(e) => patchPolicy(rule.id, { category: e.target.value })} placeholder="留空=全部" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label><label className="text-[11px] text-dim">指定 Hash<input value={rule.hash || ''} onChange={(e) => patchPolicy(rule.id, { hash: e.target.value.trim().toLowerCase() })} placeholder="可选 40 位" className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-[10px] text-fg placeholder:text-dim/60 focus:outline-none" /></label><label className="text-[11px] text-dim">最少做种分钟<input type="number" min={0} value={rule.min_seed_minutes} onChange={(e) => patchPolicy(rule.id, { min_seed_minutes: Math.max(0, Number(e.target.value) || 0) })} className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg focus:outline-none" /></label><label className="text-[11px] text-dim">最大做种分钟<input type="number" min={-1} value={rule.max_seed_minutes} onChange={(e) => { const value = Number(e.target.value); patchPolicy(rule.id, { max_seed_minutes: Number.isFinite(value) ? value : -1 }) }} className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg focus:outline-none" /></label><label className="text-[11px] text-dim">最大空闲分钟<input type="number" min={-1} value={rule.max_inactive_minutes} onChange={(e) => { const value = Number(e.target.value); patchPolicy(rule.id, { max_inactive_minutes: Number.isFinite(value) ? value : -1 }) }} className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg focus:outline-none" /></label><label className="text-[11px] text-dim">最大分享率<input type="number" min={-1} step="0.01" value={rule.max_ratio} onChange={(e) => { const value = Number(e.target.value); patchPolicy(rule.id, { max_ratio: Number.isFinite(value) ? value : -1 }) }} className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg focus:outline-none" /></label></div>
                {rule.action === 'transfer' && <div className="mt-3 grid gap-2 sm:grid-cols-2"><label className="text-[11px] text-dim">目标网盘<select value={rule.destination_remote || ''} onChange={(e) => patchPolicy(rule.id, { destination_remote: e.target.value })} className="aurora-select mt-1 w-full rounded-md border border-line px-2 py-1.5 text-xs text-fg focus:outline-none"><option value="">选择网盘</option>{policyRemotes.map((remote) => <option key={remote.name} value={remote.name}>{remote.name} · {remote.type || 'remote'}</option>)}</select></label><label className="text-[11px] text-dim">目标目录<input value={rule.destination_path || ''} onChange={(e) => patchPolicy(rule.id, { destination_path: e.target.value })} placeholder="留空=根目录" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg focus:outline-none" /></label></div>}
                {rule.action === 'remove' && <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-dim"><label className="flex items-center gap-1.5"><input type="checkbox" checked={rule.allow_delete} onChange={(e) => patchPolicy(rule.id, { allow_delete: e.target.checked })} className="accent-rose-400" />允许自动移除任务</label><label className="flex items-center gap-1.5"><input type="checkbox" checked={rule.delete_files} onChange={(e) => patchPolicy(rule.id, { delete_files: e.target.checked })} className="accent-rose-400" />同时删除本地文件</label><span className="text-[10px] text-amber-300">文件会先移入回收站（媒资库可恢复），也可在下方「最近清理」一键撤销；内容不在媒体目录时仍由 qBittorrent 直接删除。</span></div>}
              </div>)}
            </div>}
            {policyPreview && <div className="mt-4 rounded-lg border border-line bg-white/3 p-4"><div className="flex items-center justify-between gap-2"><div className="text-xs text-dim">预览命中 <span className="num text-fg">{policyPreview.length}</span> 项</div><button type="button" onClick={() => void applyPolicies()} disabled={policyBusy || !policyPreview.length} className="inline-flex items-center gap-1.5 rounded-md border border-amber-400/30 bg-amber-400/10 px-2.5 py-1.5 text-xs text-amber-200 hover:bg-amber-400/20 disabled:opacity-40"><Play size={12} />应用策略</button></div><div className="mt-2 max-h-44 overflow-y-auto">{policyPreview.map((item) => <div key={`${item.hash}-${item.rule_id}`} className="flex items-center gap-2 border-t border-line/60 py-2 text-xs"><div className="min-w-0 flex-1 truncate text-fg">{item.name}</div><span className="shrink-0 text-dim">{item.rule_name}</span><span className={item.protected ? 'shrink-0 text-amber-300' : 'shrink-0 text-dim'}>{item.protected ? `受保护：${item.protection}` : item.reason}</span></div>)}</div></div>}
            {policyDryRun && <div className="mt-4 rounded-lg border border-sky-400/30 bg-sky-400/5 p-4"><div className="flex items-center justify-between gap-2"><div className="text-xs text-sky-200">预演结果（未执行任何操作）· <span className="num">{policyDryRun.length}</span> 项待执行</div><button type="button" onClick={() => void applyPolicies()} disabled={policyBusy || !policyDryRun.length} className="inline-flex items-center gap-1.5 rounded-md border border-amber-400/30 bg-amber-400/10 px-2.5 py-1.5 text-xs text-amber-200 hover:bg-amber-400/20 disabled:opacity-40"><Play size={12} />确认执行</button></div><div className="mt-2 max-h-44 overflow-y-auto">{policyDryRun.map((item) => <div key={`${item.hash}-${item.rule_id}`} className="flex items-center gap-2 border-t border-sky-400/10 py-2 text-xs"><div className="min-w-0 flex-1 truncate text-fg">{item.name}</div><span className="shrink-0 text-dim">{item.rule_name}</span><span className="shrink-0 text-dim">{item.reason}</span><span className={`shrink-0 ${item.status === 'protected' || item.status === 'skipped' || item.status === 'already_notified' ? 'text-amber-300' : item.status === 'would_error' ? 'text-rose-300' : 'text-teal-300'}`}>{POLICY_DRY_STATUS[item.status || ''] || item.status}</span>{item.status === 'would_remove' && <span className={`shrink-0 text-[10px] ${item.recoverable ? 'text-teal-300/70' : 'text-rose-300/80'}`}>{item.recoverable ? '可撤销' : '不可恢复'}</span>}</div>)}</div></div>}
            <div className="mt-4 rounded-lg border border-line bg-white/3 p-4">
              <div className="text-xs text-dim">最近清理 · 可撤销（保留 7 天，上限 50 条）</div>
              {policyUndo.length === 0 ? <div className="mt-2 text-[11px] text-dim/70">暂无可撤销的清理记录</div> : (
                <div className="mt-2 max-h-44 overflow-y-auto">
                  {policyUndo.map((item) => (
                    <div key={item.id} className="flex items-center gap-2 border-t border-line/60 py-2 text-xs">
                      <div className="min-w-0 flex-1 truncate text-fg">{item.name}</div>
                      <span className="shrink-0 text-dim">{item.rule_name || (item.action === 'pause' ? '暂停' : '')}</span>
                      <span className="num shrink-0 text-dim/70">{new Date((item.time || 0) * 1000).toLocaleString()}</span>
                      {item.recoverable ? <button type="button" onClick={() => void runUndo(item.id)} className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line bg-white/4 px-2 py-1 text-[11px] text-dim hover:text-fg"><Undo2 size={11} />撤销</button> : <span className="shrink-0 text-[10px] text-rose-300/80">不可恢复</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>}
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium"><FolderOutput size={16} className="text-aurora-2" /> 下载完成自动整理</div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={addOrgRule} disabled={!org || orgBusy} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40"><Plus size={12} />新增规则</button>
              <button type="button" onClick={() => void previewOrg()} disabled={!org || orgBusy} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40"><Eye size={12} />预览</button>
              <button type="button" onClick={() => void runOrg(true)} disabled={!org || orgBusy} title="完整走一遍整理判定但不落盘" className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40"><Play size={12} />预演</button>
              <button type="button" onClick={() => void runOrg(false)} disabled={!org || orgBusy} className="inline-flex items-center gap-1.5 rounded-md border border-amber-400/30 bg-amber-400/10 px-2.5 py-1.5 text-xs text-amber-200 hover:bg-amber-400/20 disabled:opacity-40"><FolderOutput size={12} />立即整理</button>
              <button type="button" onClick={() => void saveOrg()} disabled={!org || orgBusy} title="保存整理规则" aria-label="保存整理规则" className="grid h-8 w-8 place-items-center rounded-md grad-bar text-ink disabled:opacity-40"><Save size={13} /></button>
            </div>
          </div>
          {!org ? <div className="mt-4 text-sm text-dim">{orgTried ? '读取失败：后端未连接' : '正在读取整理设置…'}</div> : <>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-teal-400/20 bg-teal-400/5 px-4 py-3">
              <div><div className="text-sm text-fg">启用后台自动整理</div><div className="text-[11px] text-dim">完成的任务按分类规则入库，默认硬链接（同盘瞬时、不打断做种）；目标已存在时跳过不覆盖。</div></div>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1.5 text-xs text-dim">间隔 <input type="number" min={60} max={86400} value={org.interval} onChange={(e) => setOrg({ ...org, interval: Math.max(60, Number(e.target.value) || 60) })} className="num w-20 rounded-md border border-line bg-white/4 px-2 py-1 text-right text-xs text-fg focus:outline-none" /> 秒</label>
                <label className="flex items-center gap-1.5 text-xs text-dim">整理后刷 Jellyfin 库<input type="checkbox" checked={org.jellyfin_refresh} onChange={(e) => setOrg({ ...org, jellyfin_refresh: e.target.checked })} className="accent-teal-400" /></label>
                <Switch checked={org.enabled} onChange={(v) => setOrg({ ...org, enabled: v })} />
              </div>
            </div>
            {org.rules.length === 0 ? <div className="py-8 text-center text-sm text-dim">暂无规则。新增一条「分类 → 媒体库目录」规则并保存。</div> : <div className="mt-3 flex flex-col gap-3">
              {org.rules.map((rule) => <div key={rule.id} className="rounded-lg border border-line bg-white/3 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <input value={rule.name} onChange={(e) => patchOrgRule(rule.id, { name: e.target.value })} className="min-w-[140px] flex-1 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-sm text-fg focus:outline-none" />
                  <select value={rule.mode} onChange={(e) => patchOrgRule(rule.id, { mode: e.target.value as MediaOrgRule['mode'] })} className="aurora-select rounded-md border border-line px-2 py-1.5 text-xs text-fg focus:outline-none"><option value="hardlink">硬链接（推荐）</option><option value="copy">拷贝</option><option value="move">移动（跨盘不可用）</option></select>
                  <Switch checked={rule.enabled} onChange={(v) => patchOrgRule(rule.id, { enabled: v })} />
                  <button type="button" onClick={() => setOrg({ ...org, rules: org.rules.filter((item) => item.id !== rule.id) })} title="删除规则" aria-label="删除规则" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:text-rose-300"><Trash2 size={13} /></button>
                </div>
                <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                  <label className="text-[11px] text-dim">匹配分类<input value={rule.category} onChange={(e) => patchOrgRule(rule.id, { category: e.target.value })} placeholder="留空=全部" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                  <label className="text-[11px] text-dim">媒体库目录（相对媒体根）<input value={rule.target_dir} onChange={(e) => patchOrgRule(rule.id, { target_dir: e.target.value })} placeholder="library/movies" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                  <label className="text-[11px] text-dim">最小体积（MB，0=不限）<input type="number" min={0} value={rule.min_size_mb} onChange={(e) => patchOrgRule(rule.id, { min_size_mb: Math.max(0, Number(e.target.value) || 0) })} className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg focus:outline-none" /></label>
                  <label className="flex items-end gap-1.5 pb-1.5 text-[11px] text-dim"><input type="checkbox" checked={rule.use_subfolder} onChange={(e) => patchOrgRule(rule.id, { use_subfolder: e.target.checked })} className="accent-teal-400" />按任务名建子目录</label>
                </div>
              </div>)}
            </div>}
            {orgPreview && <div className="mt-4 rounded-lg border border-line bg-white/3 p-4">
              <div className="flex items-center justify-between gap-2"><div className={`text-xs ${orgDry ? 'text-sky-200' : 'text-dim'}`}>{orgDry ? '预演结果（未落盘）· ' : '整理候选 · '}<span className="num text-fg">{orgPreview.length}</span> 项</div></div>
              {orgPreview.length === 0 ? <div className="mt-2 text-[11px] text-dim/70">没有命中的已完成任务。</div> : (
                <div className="mt-2 max-h-44 overflow-y-auto">
                  {orgPreview.map((item) => <div key={`${item.hash}-${item.rule_id}`} className="flex items-center gap-2 border-t border-line/60 py-2 text-xs">
                    <div className="min-w-0 flex-1 truncate text-fg">{item.name}</div>
                    <span className="num shrink-0 text-dim/70">{(item.size / (1024 * 1024 * 1024)).toFixed(1)} GB</span>
                    <span className="shrink-0 text-dim">{ORG_MODE_ZH[item.mode] || item.mode}</span>
                    <span className="num min-w-0 shrink truncate text-[10px] text-dim/70">{item.target}</span>
                    <span className={`shrink-0 ${item.status === 'error' || item.status === 'conflict' ? 'text-rose-300' : item.status === 'already_done' || item.status === 'done' ? 'text-teal-300' : item.status === 'skipped' ? 'text-amber-300' : 'text-dim'}`}>{ORG_STATUS[item.status || ''] || (item.skipped_reason ? item.skipped_reason : '待整理')}</span>
                  </div>)}
                </div>
              )}
            </div>}
            {(org.history?.length || 0) > 0 && <div className="mt-4 rounded-lg border border-line bg-white/3 p-4">
              <div className="text-xs text-dim">最近整理记录</div>
              <div className="mt-2 max-h-40 overflow-y-auto">
                {[...org.history].reverse().slice(0, 20).map((h, i) => <div key={`${h.time}-${i}`} className="flex items-center gap-2 border-t border-line/60 py-2 text-xs">
                  <div className="min-w-0 flex-1 truncate text-fg">{h.name}</div>
                  <span className="num shrink-0 text-dim/70">{new Date((h.time || 0) * 1000).toLocaleString()}</span>
                  <span className="num min-w-0 shrink truncate text-[10px] text-dim/70">{h.target}</span>
                  <span className={`shrink-0 ${h.status === 'done' ? 'text-teal-300' : 'text-rose-300'}`}>{h.status === 'done' ? '已入库' : '失败'}</span>
                </div>)}
              </div>
            </div>}
          </>}
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium"><ListFilter size={16} className="text-aurora-1" /> qBittorrent 队列</div>
            {qbitQueue?.online && <span className="text-[11px] text-teal-300">已连接 · 实时配置</span>}
          </div>
          {!qbitQueue ? (
            <div className="mt-4 text-sm text-dim">{queueTried ? '读取失败：后端未连接' : '正在读取 qBittorrent 设置…'}</div>
          ) : !qbitQueue.online || !qbitQueue.settings ? (
            <div className="mt-4 rounded-lg border border-line bg-white/3 px-4 py-3 text-sm text-dim">
              <div>qBittorrent 未接入</div>
              <div className="mt-1 text-[11px] text-dim/70">启动并完成鉴权后，这里可以管理活动任务、做种和队列顺序。</div>
            </div>
          ) : (() => {
            const q = qbitQueue.settings
            return (
              <div className="mt-4">
                <div className="flex items-center justify-between rounded-lg border border-line bg-white/3 px-4 py-3">
                  <div>
                    <div className="text-sm text-fg">启用队列调度</div>
                    <div className="text-[11px] text-dim">关闭后 qBittorrent 不再按活动任务上限排队</div>
                  </div>
                  <Switch checked={q.queueing_enabled} onChange={(v) => patchQbitQueue({ queueing_enabled: v })} />
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <label className="rounded-lg border border-line bg-white/3 px-3 py-2.5">
                    <span className="block text-[11px] text-dim">最大活动任务</span>
                    <input type="number" min={1} max={9999} value={q.max_active_torrents} onChange={(e) => patchQbitQueue({ max_active_torrents: Number(e.target.value) })}
                      className="num mt-1 w-full bg-transparent text-lg text-fg focus:outline-none" />
                    <span className="text-[10px] text-dim/60">9999 代表不限</span>
                  </label>
                  <label className="rounded-lg border border-line bg-white/3 px-3 py-2.5">
                    <span className="block text-[11px] text-dim">最大下载任务</span>
                    <input type="number" min={1} max={9999} value={q.max_active_downloads} onChange={(e) => patchQbitQueue({ max_active_downloads: Number(e.target.value) })}
                      className="num mt-1 w-full bg-transparent text-lg text-fg focus:outline-none" />
                    <span className="text-[10px] text-dim/60">只限制下载并发</span>
                  </label>
                  <label className="rounded-lg border border-line bg-white/3 px-3 py-2.5">
                    <span className="block text-[11px] text-dim">最大做种任务</span>
                    <input type="number" min={1} max={9999} value={q.max_active_uploads} onChange={(e) => patchQbitQueue({ max_active_uploads: Number(e.target.value) })}
                      className="num mt-1 w-full bg-transparent text-lg text-fg focus:outline-none" />
                    <span className="text-[10px] text-dim/60">9999 代表不限</span>
                  </label>
                  <label className="rounded-lg border border-line bg-white/3 px-3 py-2.5">
                    <span className="block text-[11px] text-dim">最大校验任务</span>
                    <input type="number" min={1} max={64} value={q.max_active_checking_torrents} onChange={(e) => patchQbitQueue({ max_active_checking_torrents: Number(e.target.value) })}
                      className="num mt-1 w-full bg-transparent text-lg text-fg focus:outline-none" />
                    <span className="text-[10px] text-dim/60">避免校验抢占磁盘</span>
                  </label>
                </div>
                <div className="mt-3 flex items-center justify-between rounded-lg border border-line bg-white/3 px-4 py-3">
                  <div>
                    <div className="text-sm text-fg">新任务置于队列顶部</div>
                    <div className="text-[11px] text-dim">新添加的磁力或种子优先获得活动槽位</div>
                  </div>
                  <Switch checked={q.add_to_top_of_queue} onChange={(v) => patchQbitQueue({ add_to_top_of_queue: v })} />
                </div>
                <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                  <button onClick={() => patchQbitQueue({ max_active_torrents: 9999, max_active_uploads: 9999 })}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-white/4 px-3 py-2 text-xs text-dim hover:text-fg">
                    <RefreshCw size={13} />不限做种
                  </button>
                  <button onClick={saveQueue} disabled={qbitBusy}
                    className="inline-flex items-center gap-1.5 rounded-lg grad-bar px-4 py-2 text-sm font-medium text-ink disabled:opacity-40">
                    <RefreshCw size={14} className={qbitBusy ? 'animate-spin' : ''} />保存队列设置
                  </button>
                </div>
              </div>
            )
          })()}
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ShieldCheck size={16} className="text-teal-300" /> 当前数据来源
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {Object.entries(info?.sources || {}).map(([k, v]) => (
              <span key={k} className="num rounded-full border border-line bg-white/4 px-3 py-1 text-xs text-fg">{SC_KEY[k] ?? k} · {SC_VAL[v] ?? v}</span>
            ))}
          </div>
          <div className="mt-6 flex gap-3">
            <button onClick={() => trigger((x) => x + 1)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-white/4 px-4 py-2 text-sm text-fg hover:bg-white/8">
              <RefreshCw size={14} /> 刷新
            </button>
            <button onClick={onLogout}
              className="inline-flex items-center gap-1.5 rounded-lg border border-rose-400/30 bg-rose-400/10 px-4 py-2 text-sm text-rose-300 hover:bg-rose-400/20">
              <LogOut size={14} /> 退出登录
            </button>
          </div>
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex items-center gap-2 text-sm font-medium"><ShieldCheck size={16} className="text-teal-300" /> 安全中心</div>
          <div className="mt-4 grid gap-5 lg:grid-cols-2">
            <div>
              <div className="flex items-center gap-2 text-xs text-dim"><KeyRound size={13} /> 修改密码</div>
              <div className="mt-3 grid gap-2">
                <input type="password" autoComplete="current-password" placeholder="当前密码" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} className="rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-fg focus:border-aurora-2/50 focus:outline-none" />
                <input type="password" autoComplete="new-password" placeholder="新密码（至少 12 位）" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} className="rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-fg focus:border-aurora-2/50 focus:outline-none" />
                <input type="password" autoComplete="new-password" placeholder="确认新密码" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} className="rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-fg focus:border-aurora-2/50 focus:outline-none" />
                <button disabled={!pw.current || pw.next.length < 12 || pw.next !== pw.confirm} onClick={async () => {
                  const r = await changePassword(pw.current, pw.next)
                  toast(r.ok ? '密码已修改，其他会话已下线' : r.detail, r.ok ? 'ok' : 'bad')
                  if (r.ok) { setPw({ current: '', next: '', confirm: '' }); fetchSessions().then(setSessions) }
                }} className="justify-self-start rounded-lg grad-bar px-4 py-2 text-sm font-medium text-ink disabled:opacity-40">更新密码</button>
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2 text-xs text-dim"><Smartphone size={13} /> 活跃会话</div>
              <div className="mt-3 flex flex-col gap-2">
                {sessions.map((s) => (
                  <div key={s.id} className="flex min-w-0 items-center gap-3 rounded-lg border border-line bg-white/3 px-3 py-2">
                    <Smartphone size={14} className="shrink-0 text-aurora-2" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs text-fg">{s.current ? '当前设备' : (s.device || '未知设备')}</div>
                      <div className="num truncate text-[10px] text-dim">{s.ip} · 到期 {new Date(s.expires * 1000).toLocaleString('zh-CN')}</div>
                    </div>
                    {!s.current && <button title="撤销会话" onClick={async () => { const r = await revokeSession(s.id); toast(r.ok ? '会话已撤销' : r.detail, r.ok ? 'ok' : 'bad'); if (r.ok) setSessions((x) => x.filter((v) => v.id !== s.id)) }} className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-line text-dim hover:text-rose-300"><X size={13} /></button>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="panel px-6 py-5 lg:col-span-2">
          <div className="flex items-center gap-2 text-sm font-medium"><Bell size={16} className="text-aurora-1" /> 通知与告警</div>
          <div className="mt-4 flex flex-col gap-3">
            <div className="flex items-center justify-between rounded-lg border border-line bg-white/3 px-4 py-3">
              <div>
                <div className="text-sm text-fg">下载完成 / 失败通知</div>
                <div className="text-[11px] text-dim">磁力任务状态变化时弹窗提示</div>
              </div>
              <Switch checked={!!st?.alerts?.torrent} onChange={(v) => setSt((s) => ({ ...s, alerts: { ...(s?.alerts || {}), torrent: v } }))} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-line bg-white/3 px-4 py-3">
              <div>
                <div className="text-sm text-fg">磁盘近满告警</div>
                <div className="text-[11px] text-dim">使用率超阈值时横幅 + 通知</div>
              </div>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <input type="number" min={10} max={99} value={st?.alerts?.diskWarn ?? 90}
                    onChange={(e) => setSt((s) => ({ ...s, alerts: { ...(s?.alerts || {}), diskWarn: Number(e.target.value) } }))}
                    className="num w-16 rounded-md border border-line bg-white/4 px-2 py-1 text-right text-sm text-fg focus:border-aurora-2/50 focus:outline-none" />
                  <span className="text-xs text-dim">%</span>
                </div>
                <Switch checked={!!st?.alerts?.disk} onChange={(v) => setSt((s) => ({ ...s, alerts: { ...(s?.alerts || {}), disk: v } }))} />
              </div>
            </div>
            <div className="rounded-lg border border-line bg-white/3 px-4 py-3">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm text-fg">Telegram 推送（主 / 备两个 Bot）</div>
                  <div className="text-[11px] text-dim">开启后下载完成/失败、磁盘告警推送到 Telegram</div>
                </div>
                <Switch checked={!!st?.tg?.enabled} onChange={(v) => setSt((s) => ({ ...s, tg: { enabled: v, tokens: s?.tg?.tokens ?? [] } }))} />
              </div>
              <div className="mt-3 flex flex-col gap-2">
                {[0, 1].map((i) => {
                  const t = st?.tg?.tokens?.[i] ?? { name: i === 0 ? '主' : '备', token: '', chat_id: '' }
                  const setT = (patch: Partial<{ name: string; token: string; chat_id: string }>) => setSt((s) => {
                    const tokens = [...(s?.tg?.tokens ?? [])]
                    while (tokens.length < 2) tokens.push({ name: tokens.length === 0 ? '主' : '备', token: '', chat_id: '' })
                    tokens[i] = { ...tokens[i], ...patch }
                    return { ...s, tg: { enabled: !!s?.tg?.enabled, tokens } }
                  })
                  return (
                    <div key={i} className="flex flex-wrap items-center gap-2">
                      <span className="w-6 shrink-0 text-xs text-dim">{t.name}</span>
                      <input type="password" placeholder="Bot Token" value={t.token ?? ''} onChange={(e) => setT({ token: e.target.value })}
                        className="min-w-[160px] flex-1 basis-40 rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none" />
                      <input placeholder="Chat ID" value={t.chat_id ?? ''} onChange={(e) => setT({ chat_id: e.target.value })}
                        className="w-24 shrink-0 rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none" />
                      <button disabled={!t.token || !t.chat_id}
                        onClick={async () => {
                          const r = await testTelegram(t.token ?? '', t.chat_id ?? '')
                          toast(r.ok ? `测试成功（${t.name}）` : `测试失败：${r.detail}`, r.ok ? 'ok' : 'bad')
                        }}
                        className="rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40">测试</button>
                    </div>
                  )
                })}
              </div>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-line bg-white/3 px-4 py-3">
              <div>
                <div className="text-sm text-fg">每日做种日报</div>
                <div className="text-[11px] text-dim">每天定时推送做种数 / 今日上传 / 分享率 / 磁盘到 Telegram</div>
              </div>
              <div className="flex items-center gap-3">
                <input type="time" value={st?.daily?.time ?? '21:00'}
                  onChange={(e) => setSt((s) => ({ ...s, daily: { enabled: !!s?.daily?.enabled, time: e.target.value } }))}
                  className="rounded-md border border-line bg-white/4 px-2 py-1 text-xs text-fg focus:border-aurora-2/50 focus:outline-none" />
                <Switch checked={!!st?.daily?.enabled} onChange={(v) => setSt((s) => ({ ...s, daily: { time: s?.daily?.time ?? '21:00', enabled: v } }))} />
              </div>
            </div>
            <div className="flex justify-end">
              <button onClick={save} className="rounded-lg grad-bar px-5 py-2 text-sm font-medium text-ink hover:opacity-90">保存设置</button>
            </div>
          </div>
        </section>
      </div>
      <div className="mt-6 text-center text-[11px] text-dim/60">Aurora Media Hub · by 苏念</div>
    </div>
  )
}
