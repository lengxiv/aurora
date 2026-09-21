import { useState, type FormEvent } from 'react'
import { Lock, MonitorPlay, AlertCircle } from 'lucide-react'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password }),
      })
      if (res.ok) {
        window.location.assign('/')
        return
      }
      setError('账号或密码不正确')
    } catch {
      setError('登录服务不可用')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="aurora-bg grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl grad-bar text-ink">
            <MonitorPlay size={24} strokeWidth={2.2} />
          </div>
          <div>
            <div className="text-lg font-semibold tracking-wide">Aurora</div>
            <div className="text-[11px] uppercase tracking-[0.3em] text-dim">private media hub</div>
          </div>
        </div>

        <form onSubmit={submit} className="panel px-6 py-6">
          <div className="flex items-center gap-2 pb-5">
            <Lock size={15} className="text-aurora-2" />
            <span className="text-sm font-medium">登录</span>
          </div>

          <label className="block text-[11px] uppercase tracking-[0.2em] text-dim">账号</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            className="mt-1.5 w-full rounded-lg border border-line bg-white/4 px-3 py-2.5 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none"
            placeholder="admin"
          />

          <label className="mt-4 block text-[11px] uppercase tracking-[0.2em] text-dim">密码</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            className="mt-1.5 w-full rounded-lg border border-line bg-white/4 px-3 py-2.5 text-sm text-fg placeholder:text-dim/60 focus:border-aurora-2/50 focus:outline-none"
            placeholder="••••••••"
          />

          {error && (
            <div className="mt-4 flex items-center gap-2 rounded-lg border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-xs text-rose-300">
              <AlertCircle size={13} /> {error}
            </div>
          )}

          <button
            type="submit"
            disabled={busy || !username || !password}
            className="mt-5 w-full rounded-lg grad-bar py-2.5 text-sm font-medium text-ink transition-opacity disabled:opacity-40"
          >
            {busy ? '登录中…' : '进入'}
          </button>
        </form>
      </div>
    </div>
  )
}