import { useState, useCallback, useEffect } from 'react'
import type { ContextItem } from '../api/types'
import { api } from '../api/client'

/**
 * Read-only hook for the brain's private learning journal.
 * Fetches context items with scope=brain and category=memory|wisdom.
 */
export function useBrainMemory() {
  const [logs, setLogs] = useState<ContextItem[]>([])
  const [wisdom, setWisdom] = useState<ContextItem | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchLogs = useCallback(async (search?: string) => {
    try {
      const params = new URLSearchParams({ scope: 'brain', category: 'memory' })
      if (search) params.set('search', search)
      const data = await api<ContextItem[]>(`/api/context?${params}`)
      setLogs(data)
    } catch {
      setLogs([])
    }
  }, [])

  const fetchWisdom = useCallback(async () => {
    try {
      const data = await api<ContextItem[]>('/api/context?scope=brain&category=wisdom')
      setWisdom(data.length > 0 ? data[0] : null)
    } catch {
      setWisdom(null)
    }
  }, [])

  const fetchAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchLogs(), fetchWisdom()])
    setLoading(false)
  }, [fetchLogs, fetchWisdom])

  useEffect(() => { fetchAll() }, [fetchAll])

  const searchLogs = useCallback(async (search: string) => {
    await fetchLogs(search || undefined)
  }, [fetchLogs])

  const getItem = useCallback(async (id: string): Promise<ContextItem> => {
    return api<ContextItem>(`/api/context/${id}`)
  }, [])

  return { logs, wisdom, loading, fetch: fetchAll, searchLogs, getItem }
}
