import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'

type Tone = 'ok' | 'warn' | 'bad'
type Toast = (msg: string, tone?: Tone) => void

const Ctx = createContext<Toast>(() => {})

const toneCls: Record<Tone, string> = {
  ok: 'border-teal-400/30 bg-teal-400/10 text-teal-200',
  warn: 'border-amber-400/30 bg-amber-400/10 text-amber-200',
  bad: 'border-rose-400/30 bg-rose-400/10 text-rose-200',
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; msg: string; tone: Tone }[]>([])
  const toast = useCallback<Toast>((msg, tone = 'ok') => {
    const id = Date.now() + Math.random()
    setItems((x) => [...x, { id, msg, tone }])
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 2600)
  }, [])
  return (
    <Ctx.Provider value={toast}>
      {children}
      <div className="fixed bottom-5 right-5 z-[70] flex flex-col gap-2" aria-live="polite">
        {items.map((i) => (
          <div key={i.id} className={`rounded-lg border px-4 py-2.5 text-sm backdrop-blur ${toneCls[i.tone]}`}>
            {i.msg}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}

export const useToast = () => useContext(Ctx)