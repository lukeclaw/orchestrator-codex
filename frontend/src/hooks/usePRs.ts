import { useState, useCallback, useEffect } from 'react'
import type { PullRequest } from '../api/types'
import { api } from '../api/client'

interface PRFilters {
  session_id?: string
  status?: string
}

export function usePRs(filters?: PRFilters) {
  const [prs, setPrs] = useState<PullRequest[]>([])
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(async (f?: PRFilters) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      const active = f || filters
      if (active?.session_id) params.set('session_id', active.session_id)
      if (active?.status) params.set('status', active.status)
      const qs = params.toString()
      const data = await api<PullRequest[]>(`/api/prs${qs ? `?${qs}` : ''}`)
      setPrs(data)
    } catch {
      setPrs([])
    } finally {
      setLoading(false)
    }
  }, [filters?.session_id, filters?.status])

  useEffect(() => { fetch() }, [fetch])

  return { prs, loading, fetch }
}
