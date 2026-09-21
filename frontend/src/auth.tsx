import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

interface AuthState {
  authed: boolean | null
  logout: () => void
}

const Ctx = createContext<AuthState>({ authed: null, logout: () => {} })

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null)

  useEffect(() => {
    let on = true
    if (window.location.pathname.startsWith('/login')) {
      setAuthed(false)
    } else {
      fetch('/api/auth/me', { credentials: 'include' })
        .then((r) => { if (on) setAuthed(r.ok) })
        .catch(() => { if (on) setAuthed(false) })
    }
    const onUnauth = () => setAuthed(false)
    window.addEventListener('aurora:unauth', onUnauth)
    return () => { on = false; window.removeEventListener('aurora:unauth', onUnauth) }
  }, [])

  const logout = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }) } catch { /* noop */ }
    setAuthed(false)
    window.history.replaceState(null, '', '/login')
  }

  return <Ctx.Provider value={{ authed, logout }}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
