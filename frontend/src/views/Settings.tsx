import { useEffect, useState } from 'react'
import { Server, Database, ShieldCheck, LogOut, RefreshCw, Bell, KeyRound, Smartphone, X, ListFilter } from 'lucide-react'
import { fetchInfo, fetchSettings, saveSettings, testTelegram, fetchSessions, revokeSession, changePassword, fetchQbitQueueSettings, saveQbitQueueSettings, type AuthSession, type QbitQueueSettings } from '../lib/api'
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
}

export default function Settings({ onLogout }: { onLogout: () => void }) {
  const toast = useToast()
  const [info, setInfo] = useState<Info | null>(null)
  const [st, setSt] = useState<SettingsT | null>(null)
  const [load, trigger] = useState(0)
  const [sessions, setSessions] = useState<AuthSession[]>([])
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' })
  const [qbitQueue, setQbitQueue] = useState<{ online: boolean; settings: QbitQueueSettings | null; detail?: string } | null>(null)
  const [qbitBusy, setQbitBusy] = useState(false)

  useEffect(() => {
    fetchInfo().then(setInfo)
    fetchSettings().then(setSt)
    fetchSessions().then(setSessions)
    fetchQbitQueueSettings().then(setQbitQueue)
  }, [load])

  const p = info?.providers || {}
  const save = async () => {
    const d = await saveSettings((st || {}) as Record<string, unknown>)
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
            <div className="flex justify-between"><dt className="text-dim">版本</dt><dd className="num">{info?.version ?? '—'}</dd></div>
            <div className="flex justify-between"><dt className="text-dim">鉴权</dt><dd className="num">{info?.auth ?? '—'}</dd></div>
          </dl>
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
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium"><ListFilter size={16} className="text-aurora-1" /> qBittorrent 队列</div>
            {qbitQueue?.online && <span className="text-[11px] text-teal-300">已连接 · 实时配置</span>}
          </div>
          {!qbitQueue ? (
            <div className="mt-4 text-sm text-dim">正在读取 qBittorrent 设置…</div>
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
