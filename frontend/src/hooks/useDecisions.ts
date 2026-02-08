import { useState, useCallback, useEffect } from 'react'
import type { Decision } from '../api/types'
import { api } from '../api/client'

interface DecisionFilters {
  status?: string
  project_id?: string
}

export function useDecisions(filters?: DecisionFilters) {
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(async (f?: DecisionFilters) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      const active = f || filters
      if (active?.status) params.set('status', active.status)
      if (active?.project_id) params.set('project_id', active.project_id)
      const qs = params.toString()
      const data = await api<Decision[]>(`/api/decisions${qs ? `?${qs}` : ''}`)
      setDecisions(data)
    } catch {
      setDecisions([])
    } finally {
      setLoading(false)
    }
  }, [filters?.status, filters?.project_id])

  useEffect(() => { fetch() }, [fetch])

  const respond = useCallback(async (id: string, response: string) => {
    await api(`/api/decisions/${id}/respond`, {
      method: 'POST',
      body: JSON.stringify({ response, resolved_by: 'user' }),
    })
    setDecisions(prev => prev.map(d =>
      d.id === id ? { ...d, status: 'responded' as const, response } : d
    ))
  }, [])

  const dismiss = useCallback(async (id: string) => {
    await api(`/api/decisions/${id}/dismiss`, { method: 'POST' })
    setDecisions(prev => prev.map(d =>
      d.id === id ? { ...d, status: 'dismissed' as const } : d
    ))
  }, [])

  return { decisions, loading, fetch, respond, dismiss }
}
