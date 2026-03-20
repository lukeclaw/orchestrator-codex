import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react'
import { api } from '../api/client'

export interface SettingEntry {
  key: string
  value: unknown
  description: string | null
  category: string
  updated_at: string
}

interface SettingsState {
  settings: SettingEntry[]
  loading: boolean
  saving: boolean
  fetch: (category?: string) => Promise<void>
  save: (updates: Record<string, unknown>) => Promise<void>
  getValue: (key: string) => unknown
}

const SettingsContext = createContext<SettingsState | null>(null)

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<SettingEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const fetchSettings = useCallback(async (category?: string) => {
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

  useEffect(() => { fetchSettings() }, [fetchSettings])

  const save = useCallback(async (updates: Record<string, unknown>) => {
    setSaving(true)
    try {
      await api('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ settings: updates }),
      })
      await fetchSettings()
    } finally {
      setSaving(false)
    }
  }, [fetchSettings])

  const getValue = useCallback((key: string): unknown => {
    const entry = settings.find(s => s.key === key)
    return entry?.value ?? null
  }, [settings])

  return (
    <SettingsContext.Provider value={{ settings, loading, saving, fetch: fetchSettings, save, getValue }}>
      {children}
    </SettingsContext.Provider>
  )
}

export function useSettings(): SettingsState {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used within SettingsProvider')
  return ctx
}
