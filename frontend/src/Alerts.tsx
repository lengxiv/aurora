import { useEffect, useRef, useState } from 'react'
import { useMetrics, fetchSettings } from './lib/api'
import { useToast } from './toast'

export default function Alerts() {
  const { data } = useMetrics(4000)
  const toast = useToast()
  const prev = useRef<Record<string, string>>({})
  const [enabled, setEnabled] = useState(true)

  useEffect(() => {
    fetchSettings().then((s) => setEnabled(!!s?.alerts?.torrent))
  }, [])

  useEffect(() => {
    if (!enabled) return
    const cur: Record<string, string> = {}
    for (const t of data.torrents) {
      cur[t.id] = t.state
      const p = prev.current[t.id]
      if (t.state === 'done' && p && p !== 'done') toast(`下载完成：${t.name}`)
      if (t.state === 'error' && p && p !== 'error') toast(`下载失败：${t.name}`, 'bad')
    }
    prev.current = cur
  }, [data.torrents, enabled]) // eslint-disable-line react-hooks/exhaustive-deps

  return null
}