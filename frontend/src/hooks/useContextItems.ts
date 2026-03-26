import { useState, useCallback, useEffect } from 'react'
import type { ContextItem } from '../api/types'
import { api } from '../api/client'

interface ContextFilters {
  scope?: string
  provider?: string
  project_id?: string
  category?: string
  search?: string
  include_content?: boolean
  include_shared?: boolean
  /** Client-side filter: hide items matching these scope+category pairs (e.g. brain memory/wisdom) */
  excludeScopeCategories?: Array<{ scope: string; category: string }>
}

export function useContextItems(filters?: ContextFilters) {
  const [items, setItems] = useState<ContextItem[]>([])
  const [loading, setLoading] = useState(true)

  const fetchItems = useCallback(async (f?: ContextFilters) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      const active = f || filters
      if (active?.scope) params.set('scope', active.scope)
      if (active?.provider) params.set('provider', active.provider)
      if (active?.project_id) params.set('project_id', active.project_id)
      if (active?.category) params.set('category', active.category)
      if (active?.search) params.set('search', active.search)
      if (active?.include_content) params.set('include_content', 'true')
      if (active?.include_shared === false) params.set('include_shared', 'false')
      const qs = params.toString()
      let data = await api<ContextItem[]>(`/api/context${qs ? `?${qs}` : ''}`)
      const excludes = (f || filters)?.excludeScopeCategories
      if (excludes?.length) {
        data = data.filter(item => !excludes.some(
          ex => item.scope === ex.scope && item.category === ex.category
        ))
      }
      setItems(data)
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [filters?.scope, filters?.provider, filters?.project_id, filters?.category, filters?.search, filters?.include_content, filters?.include_shared])

  useEffect(() => { fetchItems() }, [fetchItems])

  // Fetch a single context item with full content
  const getItem = useCallback(async (id: string): Promise<ContextItem> => {
    return api<ContextItem>(`/api/context/${id}`)
  }, [])

  const create = useCallback(async (body: {
    title: string
    content: string
    description?: string
    scope?: string
    provider?: string | null
    project_id?: string
    category?: string
    source?: string
  }) => {
    const item = await api<ContextItem>('/api/context', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    setItems(prev => [item, ...prev])
    return item
  }, [])

  const update = useCallback(async (id: string, body: Partial<Pick<ContextItem, 'title' | 'content' | 'description' | 'scope' | 'provider' | 'project_id' | 'category' | 'source'>>) => {
    const item = await api<ContextItem>(`/api/context/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    })
    setItems(prev => prev.map(x => x.id === id ? item : x))
    return item
  }, [])

  const remove = useCallback(async (id: string) => {
    await api(`/api/context/${id}`, { method: 'DELETE' })
    setItems(prev => prev.filter(x => x.id !== id))
  }, [])

  return { items, loading, fetch: fetchItems, getItem, create, update, remove }
}
