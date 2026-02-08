import { useState, useCallback, useEffect } from 'react'
import type { Activity } from '../api/types'
import { api } from '../api/client'

interface ActivityFilters {
  session_id?: string
  project_id?: string
  event_type?: string
  limit?: number
}

export function useActivities(filters?: ActivityFilters) {
  const [activities, setActivities] = useState<Activity[]>([])
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(async (f?: ActivityFilters) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      const active = f || filters
      if (active?.session_id) params.set('session_id', active.session_id)
      if (active?.project_id) params.set('project_id', active.project_id)
      if (active?.event_type) params.set('event_type', active.event_type)
      params.set('limit', String(active?.limit ?? 50))
      const data = await api<Activity[]>(`/api/activities?${params}`)
      setActivities(data)
    } catch {
      setActivities([])
    } finally {
      setLoading(false)
    }
  }, [filters?.session_id, filters?.project_id, filters?.event_type, filters?.limit])

  useEffect(() => { fetch() }, [fetch])

  return { activities, loading, fetch }
}
