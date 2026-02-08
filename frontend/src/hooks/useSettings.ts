import { useState, useCallback, useEffect } from 'react'
import { api } from '../api/client'

export interface SettingEntry {
  key: string
  value: unknown
  description: string | null
  category: string
  updated_at: string
}

export function useSettings() {
  const [settings, setSettings] = useState<SettingEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const fetch = useCallback(async (category?: string) => {
    setLoading(true)
    try {
      const qs = category ? `?category=${category}` : ''
      const data = await api<SettingEntry[]>(`/api/settings${qs}`)
      setSettings(data)
    } catch {
      setSettings([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetch() }, [fetch])

  const save = useCallback(async (updates: Record<string, unknown>) => {
    setSaving(true)
    try {
      await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ settings: updates }),
      })
      // Refresh after save
      await fetch()
    } finally {
      setSaving(false)
    }
  }, [fetch])

  const getValue = useCallback((key: string): unknown => {
    const entry = settings.find(s => s.key === key)
    return entry?.value ?? null
  }, [settings])

  return { settings, loading, saving, fetch, save, getValue }
}
