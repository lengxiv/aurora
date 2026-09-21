import { NavLink, Route, Routes } from 'react-router-dom'
import { Gauge, MonitorPlay, Activity, Loader, LogOut, Settings as SettingsIcon, Command, Upload, Cloud } from 'lucide-react'
import ConsoleView from './views/Console'
import PlayerView from './views/Player'
import MonitorView from './views/Monitor'
import SeedingView from './views/Seeding'
import SettingsView from './views/Settings'
import NetdiskView from './views/Netdisk'
import Login from './views/Login'
import { useAuth } from './auth'
import CommandPalette from './CommandPalette'
import Alerts from './Alerts'
import { MetricsProvider } from './lib/api'

const NAV = [
  { to: '/', label: '管理台', icon: Gauge, end: true },
  { to: '/seeding', label: '做种监控', icon: Upload, end: false },
  { to: '/netdisk', label: '网盘', icon: Cloud, end: false },
  { to: '/player', label: '媒资库', icon: MonitorPlay, end: false },
  { to: '/monitor', label: '实时监控', icon: Activity, end: false },
  { to: '/settings', label: '设置', icon: SettingsIcon, end: false },
]

function Shell() {
  const { logout } = useAuth()
  return (
    <MetricsProvider><div className="aurora-bg relative min-h-screen">
      <CommandPalette onLogout={logout} />
      <Alerts />
      <div className="mx-auto flex min-h-screen max-w-[1400px] flex-col md:flex-row">
        {/* mobile top nav */}
        <header className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-line bg-ink-2/85 px-3 py-2.5 backdrop-blur md:hidden">
          <div className="flex shrink-0 items-center gap-2">
            <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg grad-bar text-ink"><MonitorPlay size={18} strokeWidth={2.2} /></div>
            <span className="hidden text-sm font-semibold tracking-wide min-[400px]:inline">Aurora</span>
          </div>
          <nav className="no-scrollbar flex min-w-0 flex-1 items-center justify-end gap-1 overflow-x-auto">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} aria-label={label}
                className={({ isActive }) => `grid h-8 w-8 shrink-0 place-items-center rounded-lg transition-colors ${isActive ? 'bg-white/10 text-fg' : 'text-dim hover:bg-white/5'}`}>
                <Icon size={17} strokeWidth={1.9} />
              </NavLink>
            ))}
          </nav>
          <button onClick={logout} aria-label="退出登录" className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-dim hover:bg-white/5 hover:text-rose-300">
            <LogOut size={17} strokeWidth={1.9} />
          </button>
        </header>
        {/* side rail (desktop only) */}
        <aside className="hidden md:flex md:sticky md:top-0 md:h-screen md:w-56 md:shrink-0 md:flex-col md:border-r md:border-line md:px-4 md:py-6">
          <div className="flex items-center gap-2.5 px-2">
            <div className="grid h-9 w-9 place-items-center rounded-xl grad-bar text-ink">
              <MonitorPlay size={20} strokeWidth={2.2} />
            </div>
            <div>
              <div className="text-sm font-semibold tracking-wide">Aurora</div>
              <div className="text-[10px] uppercase tracking-[0.3em] text-dim">media hub</div>
            </div>
            <div className="ml-auto live-dot bg-teal-400 text-teal-400" />
          </div>

          <nav className="mt-10 flex flex-col gap-1">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                    isActive
                      ? 'bg-white/8 text-fg font-medium'
                      : 'text-dim hover:bg-white/4 hover:text-fg'
                  }`
                }
              >
                <Icon size={17} strokeWidth={1.9} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>

          <div className="mt-auto px-2 text-[11px] leading-relaxed text-dim/70">
            <div>systemd 服务运行中</div>
            <div className="mt-1 text-dim/80">带宽 eth0 实测 · 磁盘 df 实测</div>
          </div>
          <div className="mt-2 flex items-center gap-1.5 px-2 text-[11px] text-dim/60">
            <Command size={12} /> <span className="num">Ctrl / ⌘ K</span> 命令
          </div>
          <button onClick={logout} className="mt-4 flex items-center gap-2 rounded-lg border border-line bg-white/4 px-3 py-2 text-sm text-dim hover:text-rose-300">
            <LogOut size={14} /> 退出登录
          </button>
        </aside>

        {/* content */}
        <main className="min-w-0 flex-1">
          <Routes>
            <Route path="/" element={<ConsoleView />} />
            <Route path="/seeding" element={<SeedingView />} />
            <Route path="/netdisk" element={<NetdiskView />} />
            <Route path="/player" element={<PlayerView />} />
            <Route path="/monitor" element={<MonitorView />} />
            <Route path="/settings" element={<SettingsView onLogout={logout} />} />
          </Routes>
        </main>
      </div>
    </div></MetricsProvider>
  )
}

function Splash() {
  return (
    <div className="aurora-bg grid min-h-screen place-items-center">
      <Loader className="animate-spin text-aurora-2" size={26} />
    </div>
  )
}

export default function App() {
  const { authed } = useAuth()
  if (authed === null) return <Splash />
  if (!authed) return <Login />
  return <Shell />
}
