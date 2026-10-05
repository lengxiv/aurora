import { useEffect, useState } from 'react'
import { Rss, Plus, Trash2, Save, RefreshCw, Eye, Pencil, X, Link2 } from 'lucide-react'
import {
  fetchRssOverview, rssAddFeed, rssRemoveFeed, rssRenameFeed, rssSetFeedUrl, rssRefreshFeed,
  saveRssRule, rssRemoveRule, previewRssMatches,
  type RssFeed, type RssRule, type RssArticle,
} from '../lib/api'
import { Switch } from '../components/ui'
import { useToast } from '../toast'

interface RuleDraft extends RssRule {
  destination_remote: string
  destination_path: string
}

const EMPTY_DRAFT: RuleDraft = {
  name: '', enabled: true, use_regex: false, must_contain: '', must_not_contain: '',
  episode_filter: '', affected_feeds: [], save_path: '', category: '', tags: [],
  last_match: 0, destination_remote: '', destination_path: '',
}

export default function RssView() {
  const toast = useToast()
  const [ov, setOv] = useState<{ feeds: RssFeed[]; rules: RssRule[] } | null>(null)
  const [tried, setTried] = useState(false)
  const [busy, setBusy] = useState(false)
  const [nfName, setNfName] = useState('')
  const [nfUrl, setNfUrl] = useState('')
  const [draft, setDraft] = useState<RuleDraft | null>(null)
  const [draftTags, setDraftTags] = useState('')
  const [matches, setMatches] = useState<RssArticle[] | null>(null)
  const [matchName, setMatchName] = useState('')

  const load = async () => {
    setOv(await fetchRssOverview())
    setTried(true)
  }
  useEffect(() => { void load() }, [])

  const act = async (fn: () => Promise<{ ok: boolean; detail: string }>, okMsg: string) => {
    setBusy(true)
    const result = await fn()
    setBusy(false)
    toast(result.ok ? okMsg : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) await load()
  }

  const addFeed = async () => {
    if (!nfName.trim() || !nfUrl.trim()) { toast('请填写订阅名称和 URL', 'bad'); return }
    await act(() => rssAddFeed({ path: nfName.trim(), url: nfUrl.trim() }), '订阅已添加')
    setNfName(''); setNfUrl('')
  }
  const removeFeed = async (feed: RssFeed) => {
    if (!window.confirm(`删除订阅「${feed.name}」？已下载的任务不受影响。`)) return
    await act(() => rssRemoveFeed(feed.path), '订阅已删除')
  }
  const renameFeed = async (feed: RssFeed) => {
    const name = window.prompt('新的订阅名称（可用 \\ 放入文件夹）', feed.name)
    if (!name || name === feed.name) return
    await act(() => rssRenameFeed(feed.path, name), '订阅已重命名')
  }
  const editFeedUrl = async (feed: RssFeed) => {
    const url = window.prompt('新的订阅 URL', feed.url)
    if (!url || url === feed.url) return
    await act(() => rssSetFeedUrl(feed.path, url.trim()), '订阅地址已更新')
  }
  const refreshFeed = async (feed: RssFeed) => {
    await act(() => rssRefreshFeed(feed.path), `已触发刷新：${feed.name}`)
  }

  const startNewRule = () => { setDraft({ ...EMPTY_DRAFT }); setDraftTags('') }
  const startEditRule = (rule: RssRule) => {
    // 回显现有网盘绑定：否则保存后 aurora-remote-* 标记会被剥掉，
    // 规则的自动转存网盘功能就静默失效了
    setDraft({ ...rule, destination_remote: rule.destination_remote || '', destination_path: rule.destination_path || '' })
    setDraftTags(rule.tags.filter((t) => !t.startsWith('aurora-remote-')).join(','))
  }
  const saveDraft = async () => {
    if (!draft) return
    if (!draft.name.trim()) { toast('请填写规则名称', 'bad'); return }
    if (!draft.must_contain.trim() && !draft.use_regex) { toast('请至少填写包含关键字', 'bad'); return }
    setBusy(true)
    const result = await saveRssRule({
      ...draft, name: draft.name.trim(),
      tags: draftTags.split(',').map((t) => t.trim()).filter(Boolean),
    })
    setBusy(false)
    toast(result.ok ? '规则已保存（启用后 qBittorrent 自动下载命中条目）' : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) { setDraft(null); await load() }
  }
  const removeRule = async (rule: RssRule) => {
    if (!window.confirm(`删除规则「${rule.name}」？`)) return
    if (matches && matchName === rule.name) { setMatches(null); setMatchName('') }
    await act(() => rssRemoveRule(rule.name), '规则已删除')
  }
  const toggleRule = async (rule: RssRule, enabled: boolean) => {
    // 开关直接保存：tags 原样回传（含转存标记），destination_remote 留空
    // 表示不新增转存注册，避免重复登记网盘目标
    setBusy(true)
    const result = await saveRssRule({ ...rule, enabled, destination_remote: '', destination_path: '' })
    setBusy(false)
    toast(result.ok ? (enabled ? '规则已启用' : '规则已停用') : result.detail, result.ok ? 'ok' : 'bad')
    if (result.ok) await load()
  }
  const previewRule = async (name: string) => {
    setBusy(true)
    const items = await previewRssMatches(name)
    setBusy(false)
    setMatchName(name)
    setMatches(items)
    if (items === null) toast('匹配预览失败：请先保存规则', 'bad')
    else toast(`规则「${name}」当前命中 ${items.length} 条`, items.length ? 'ok' : 'bad')
  }

  const feeds = ov?.feeds || []
  const rules = ov?.rules || []

  return (
    <div className="min-w-0 px-4 py-6 md:px-10 md:py-8">
      <div className="text-[11px] uppercase tracking-[0.3em] text-dim">自动化</div>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight">RSS 订阅</h1>
      <p className="mt-2 max-w-3xl text-sm text-dim">
        抓取、匹配与自动下载由 qBittorrent 内置 RSS 引擎完成，Aurora 负责管理与匹配预览。
        命中条目按规则自动分类并落入保存目录；规则上绑定网盘目标后，完成后由既有调度器继续自动转存。
      </p>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="panel px-6 py-5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium"><Rss size={16} className="text-aurora-1" /> 订阅源 <span className="num text-dim">{feeds.length}</span></div>
            <button type="button" onClick={() => void load()} disabled={busy} aria-label="刷新订阅列表" className="grid h-8 w-8 place-items-center rounded-md border border-line bg-white/4 text-dim hover:text-fg disabled:opacity-40"><RefreshCw size={13} /></button>
          </div>
          <div className="mt-4 flex gap-2">
            <input value={nfName} onChange={(e) => setNfName(e.target.value)} placeholder="订阅名称" className="min-w-0 flex-1 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" />
            <input value={nfUrl} onChange={(e) => setNfUrl(e.target.value)} placeholder="https://…（RSS 地址）" className="num min-w-0 flex-[2] rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" />
            <button type="button" onClick={() => void addFeed()} disabled={busy} title="添加订阅" className="grid h-8 w-8 shrink-0 place-items-center rounded-md grad-bar text-ink disabled:opacity-40"><Plus size={14} /></button>
          </div>
          {!ov ? <div className="mt-4 text-sm text-dim">{tried ? '读取失败：qBittorrent 未接入或版本过旧（RSS 需要 qBittorrent 4.1+）' : '正在读取订阅…'}</div>
            : feeds.length === 0 ? <div className="py-8 text-center text-sm text-dim">暂无订阅源。先在上方添加一个 RSS 地址。</div>
              : <div className="mt-3 flex flex-col gap-2">
                {feeds.map((feed) => (
                  <div key={feed.path} className="rounded-lg border border-line bg-white/3 px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 text-sm text-fg"><span className="truncate">{feed.name}</span>
                          {feed.has_error && <span className="shrink-0 rounded-full bg-rose-400/10 px-2 py-0.5 text-[10px] text-rose-300">抓取错误</span>}
                          {feed.loading && <span className="shrink-0 text-[10px] text-dim">刷新中…</span>}
                        </div>
                        <div className="num truncate text-[11px] text-dim" title={feed.url}>{feed.url}</div>
                      </div>
                      <button type="button" onClick={() => void refreshFeed(feed)} disabled={busy} title="立即刷新" aria-label="立即刷新" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:text-fg"><RefreshCw size={13} /></button>
                      <button type="button" onClick={() => void editFeedUrl(feed)} disabled={busy} title="修改地址" aria-label="修改地址" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:text-fg"><Link2 size={13} /></button>
                      <button type="button" onClick={() => void renameFeed(feed)} disabled={busy} title="重命名" aria-label="重命名" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:text-fg"><Pencil size={13} /></button>
                      <button type="button" onClick={() => void removeFeed(feed)} disabled={busy} title="删除" aria-label="删除" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:text-rose-300"><Trash2 size={13} /></button>
                    </div>
                  </div>
                ))}
              </div>}
        </section>

        <section className="panel px-6 py-5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium"><Rss size={16} className="text-aurora-2" /> 订阅规则 <span className="num text-dim">{rules.length}</span></div>
            <button type="button" onClick={startNewRule} disabled={!!draft} className="inline-flex items-center gap-1.5 rounded-md border border-line bg-white/4 px-2.5 py-1.5 text-xs text-dim hover:text-fg disabled:opacity-40"><Plus size={12} />新增规则</button>
          </div>
          {rules.length === 0 && !draft ? <div className="mt-4 py-8 text-center text-sm text-dim">暂无规则。新增规则并绑定订阅源，命中条目将自动下载。</div>
            : <div className="mt-3 flex flex-col gap-2">
              {rules.map((rule) => (
                <div key={rule.name} className="rounded-lg border border-line bg-white/3 px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-sm text-fg">
                        <span className="truncate">{rule.name}</span>
                        {!rule.enabled && <span className="shrink-0 text-[10px] text-dim">已停用</span>}
                        {rule.use_regex && <span className="shrink-0 rounded-full bg-white/6 px-2 py-0.5 text-[10px] text-dim">正则</span>}
                        {rule.destination_remote && <span className="num shrink-0 rounded-full bg-teal-400/10 px-2 py-0.5 text-[10px] text-teal-300" title="完成后自动转存到该网盘目标">→ {rule.destination_remote}{rule.destination_path ? `:${rule.destination_path}` : ''}</span>}
                      </div>
                      <div className="num truncate text-[11px] text-dim">
                        包含：{rule.must_contain || '—'}{rule.category ? ` · 分类 ${rule.category}` : ''}{rule.save_path ? ` · ${rule.save_path}` : ''} · 订阅 {rule.affected_feeds.length} 个
                      </div>
                    </div>
                    <button type="button" onClick={() => void previewRule(rule.name)} disabled={busy} title="匹配预览" aria-label="匹配预览" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:text-fg"><Eye size={13} /></button>
                    <button type="button" onClick={() => startEditRule(rule)} title="编辑" aria-label="编辑" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:text-fg"><Pencil size={13} /></button>
                    <button type="button" onClick={() => void removeRule(rule)} disabled={busy} title="删除" aria-label="删除" className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-dim hover:text-rose-300"><Trash2 size={13} /></button>
                    <Switch checked={rule.enabled} onChange={(v) => void toggleRule(rule, v)} />
                  </div>
                </div>
              ))}
            </div>}
          {draft && (
            <div className="mt-3 rounded-lg border border-aurora-2/30 bg-white/3 p-4">
              <div className="flex items-center justify-between gap-2">
                <div className="text-sm font-medium text-fg">{draft.name ? `编辑规则：${draft.name}` : '新增规则'}</div>
                <button type="button" onClick={() => setDraft(null)} aria-label="取消编辑" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:text-fg"><X size={13} /></button>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="text-[11px] text-dim">规则名称<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="例：每周新番 1080p" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                <label className="text-[11px] text-dim">匹配分类（命中任务自动归类）<input value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} placeholder="留空不设置" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                <label className="text-[11px] text-dim">包含（{draft.use_regex ? '正则' : '关键字，| 分隔多个'}）<input value={draft.must_contain} onChange={(e) => setDraft({ ...draft, must_contain: e.target.value })} placeholder={draft.use_regex ? 'S\d{2}.*1080' : '1080p|BluRay'} className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                <label className="text-[11px] text-dim">排除（{draft.use_regex ? '正则' : '关键字'}）<input value={draft.must_not_contain} onChange={(e) => setDraft({ ...draft, must_not_contain: e.target.value })} placeholder="留空不排除" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                <label className="text-[11px] text-dim">保存目录（qbit 内路径）<input value={draft.save_path} onChange={(e) => setDraft({ ...draft, save_path: e.target.value })} placeholder="/downloads/动画（留空=默认）" className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                <label className="text-[11px] text-dim">集数过滤（可选，如 S01E01-）<input value={draft.episode_filter} onChange={(e) => setDraft({ ...draft, episode_filter: e.target.value })} placeholder="留空不过滤" className="num mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="text-[11px] text-dim">完成后转存网盘（可选）<input value={draft.destination_remote} onChange={(e) => setDraft({ ...draft, destination_remote: e.target.value })} placeholder="网盘名称，留空不转存" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                <label className="text-[11px] text-dim">网盘目标目录<input value={draft.destination_path} onChange={(e) => setDraft({ ...draft, destination_path: e.target.value })} placeholder="留空=网盘根目录" className="mt-1 w-full rounded-md border border-line bg-white/4 px-2 py-1.5 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
              </div>
              {feeds.length > 0 && <div className="mt-3">
                <div className="text-[11px] text-dim">作用订阅源（不选=全部）</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {feeds.map((feed) => {
                    const on = draft.affected_feeds.includes(feed.path)
                    return <button type="button" key={feed.path} onClick={() => setDraft({ ...draft, affected_feeds: on ? draft.affected_feeds.filter((p) => p !== feed.path) : [...draft.affected_feeds, feed.path] })}
                      className={`rounded-full border px-2.5 py-1 text-[11px] ${on ? 'border-aurora-2/50 text-aurora-1' : 'border-line bg-white/4 text-dim'}`}>{feed.name}</button>
                  })}
                </div>
              </div>}
              <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-dim">
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft.use_regex} onChange={(e) => setDraft({ ...draft, use_regex: e.target.checked })} className="accent-teal-400" />使用正则表达式</label>
                <label className="flex items-center gap-1.5">标签<input value={draftTags} onChange={(e) => setDraftTags(e.target.value)} placeholder="逗号分隔" className="num w-40 rounded-md border border-line bg-white/4 px-2 py-1 text-xs text-fg placeholder:text-dim/60 focus:outline-none" /></label>
                <label className="flex items-center gap-1.5">保存后启用<Switch checked={draft.enabled} onChange={(v) => setDraft({ ...draft, enabled: v })} /></label>
              </div>
              <div className="mt-4 flex items-center justify-end gap-2">
                <button type="button" onClick={() => void saveDraft()} disabled={busy} className="inline-flex items-center gap-1.5 rounded-md grad-bar px-3 py-1.5 text-xs font-medium text-ink disabled:opacity-40"><Save size={12} />保存规则</button>
              </div>
              <div className="mt-2 text-[10px] text-dim/70">提示：匹配预览针对「已保存」的规则执行——先保存（可保持停用），再点规则行的预览按钮。</div>
            </div>
          )}
        </section>

        {matches && (
          <section className="panel px-6 py-5 lg:col-span-2">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-medium text-fg">匹配预览 · <span className="num">{matchName}</span> · 命中 <span className="num">{matches.length}</span> 条</div>
              <button type="button" onClick={() => setMatches(null)} aria-label="关闭预览" className="grid h-7 w-7 place-items-center rounded-md text-dim hover:text-fg"><X size={13} /></button>
            </div>
            {matches.length === 0 ? <div className="mt-3 py-4 text-center text-sm text-dim">当前没有命中的条目。</div> : (
              <div className="mt-3 max-h-60 overflow-y-auto">
                {matches.map((item, i) => (
                  <div key={`${item.feed}-${i}`} className="flex items-center gap-3 border-t border-line/60 py-2 text-xs">
                    <div className="min-w-0 flex-1 truncate text-fg">{item.title}</div>
                    <span className="num shrink-0 text-[10px] text-dim">{item.feed}</span>
                  </div>
                ))}
              </div>
            )}
            <div className="mt-3 text-[11px] text-dim/70">预览仅展示命中结果；启用规则后，新出现的命中条目由 qBittorrent 自动添加下载，已下载过的不会重复。</div>
          </section>
        )}
      </div>
    </div>
  )
}
