import { useState, useCallback, useEffect } from 'react'
import type { ContextItem } from '../api/types'
import { api } from '../api/client'
import { DEFAULT_PROVIDER_ID } from './useProviderRegistry'

/**
 * Read-only hook for the brain's private learning journal.
 * Fetches context items with scope=brain and category=memory|wisdom.
 */
export function useBrainMemory() {
  const [logs, setLogs] = useState<ContextItem[]>([])
  const [wisdom, setWisdom] = useState<ContextItem | null>(null)
  const [provider, setProvider] = useState(DEFAULT_PROVIDER_ID)
  const [loading, setLoading] = useState(true)

  const fetchLogs = useCallback(async (search?: string, providerId: string = provider) => {
    try {
      const params = new URLSearchParams({ scope: 'brain', category: 'memory', provider: providerId })
      if (search) params.set('search', search)
      const data = await api<ContextItem[]>(`/api/context?${params}`)
      setLogs(data)
    } catch {
      setLogs([])
    }
  }, [provider])

  const fetchWisdom = useCallback(async (providerId: string = provider) => {
    try {
      const data = await api<ContextItem[]>(`/api/context?scope=brain&category=wisdom&provider=${providerId}`)
      setWisdom(data.length > 0 ? data[0] : null)
    } catch {
      setWisdom(null)
    }
  }, [provider])

  const fetchProvider = useCallback(async () => {
    try {
      const data = await api<{ provider?: string }>('/api/brain/status')
      const nextProvider = data.provider || DEFAULT_PROVIDER_ID
      setProvider(nextProvider)
      return nextProvider
    } catch {
      setProvider(DEFAULT_PROVIDER_ID)
      return DEFAULT_PROVIDER_ID
    }
  }, [])

  const fetchAll = useCallback(async () => {
    setLoading(true)
    const activeProvider = await fetchProvider()
    await Promise.all([fetchLogs(undefined, activeProvider), fetchWisdom(activeProvider)])
    setLoading(false)
  }, [fetchLogs, fetchProvider, fetchWisdom])

  useEffect(() => { fetchAll() }, [fetchAll])

  const searchLogs = useCallback(async (search: string) => {
    const activeProvider = await fetchProvider()
    await fetchLogs(search || undefined, activeProvider)
  }, [fetchLogs, fetchProvider])

  const getItem = useCallback(async (id: string): Promise<ContextItem> => {
    return api<ContextItem>(`/api/context/${id}`)
  }, [])

  return { logs, wisdom, loading, fetch: fetchAll, searchLogs, getItem }
}
