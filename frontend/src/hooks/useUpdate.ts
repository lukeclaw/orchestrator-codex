import { useState, useCallback } from 'react'
import { api } from '../api/client'

export interface UpdateInfo {
  current_version: string
  latest_version: string | null
  update_available: boolean
  release_url?: string
  dmg_url?: string | null
  release_notes?: string
  pub_date?: string
  error?: string
}

export function useUpdate() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)

  const check = useCallback(async (force = false) => {
    setChecking(true)
    try {
      const qs = force ? '?force=true' : ''
      const data = await api<UpdateInfo>(`/api/updates/check${qs}`)
      setInfo(data)
      return data
    } catch {
      setInfo(null)
      return null
    } finally {
      setChecking(false)
    }
  }, [])

  const openRelease = useCallback(async (url: string) => {
    await api('/api/open-url', {
      method: 'POST',
      body: JSON.stringify({ url }),
    })
  }, [])

  return { info, checking, check, openRelease }
}
