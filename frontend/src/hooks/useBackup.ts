import { useState, useCallback, useEffect } from 'react'
import { api } from '../api/client'

export interface BackupSettings {
  directory: string | null
  has_password: boolean
  retention_count: number
  last_run: string | null
  last_status: string | null
}

export interface BackupEntry {
  filename: string
  timestamp: string
  size_bytes: number
}

export interface BackupResult {
  ok: boolean
  filename: string
  size_bytes: number
  timestamp: string
  pruned: string[]
  error: string | null
}

export function useBackup() {
  const [settings, setSettings] = useState<BackupSettings | null>(null)
  const [backups, setBackups] = useState<BackupEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [lastResult, setLastResult] = useState<BackupResult | null>(null)

  const fetchSettings = useCallback(async () => {
    try {
      const data = await api<BackupSettings>('/api/backup/settings')
      setSettings(data)
    } catch {
      setSettings(null)
    }
  }, [])

  const fetchBackups = useCallback(async () => {
    try {
      const data = await api<{ backups: BackupEntry[]; error?: string }>('/api/backup/list')
      setBackups(data.backups || [])
    } catch {
      setBackups([])
    }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchSettings(), fetchBackups()])
    setLoading(false)
  }, [fetchSettings, fetchBackups])

  useEffect(() => { refresh() }, [refresh])

  const saveSettings = useCallback(async (updates: {
    directory?: string
    password?: string
    retention_count?: number
  }) => {
    setSaving(true)
    try {
      await api('/api/backup/settings', {
        method: 'PUT',
        body: JSON.stringify(updates),
      })
      await fetchSettings()
    } finally {
      setSaving(false)
    }
  }, [fetchSettings])

  const runBackup = useCallback(async () => {
    setRunning(true)
    setLastResult(null)
    try {
      const result = await api<BackupResult>('/api/backup/run', { method: 'POST' })
      setLastResult(result)
      // Refresh everything after backup
      await Promise.all([fetchSettings(), fetchBackups()])
      return result
    } finally {
      setRunning(false)
    }
  }, [fetchSettings, fetchBackups])

  return {
    settings,
    backups,
    loading,
    saving,
    running,
    lastResult,
    saveSettings,
    runBackup,
    refresh,
  }
}
