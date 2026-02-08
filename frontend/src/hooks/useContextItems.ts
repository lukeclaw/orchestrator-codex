import { useState, useCallback, useEffect } from 'react'
import type { ContextItem } from '../api/types'
import { api } from '../api/client'

interface ContextFilters {
  scope?: string
  project_id?: string
  category?: string
  search?: string
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
      if (active?.project_id) params.set('project_id', active.project_id)
      if (active?.category) params.set('category', active.category)
      if (active?.search) params.set('search', active.search)
      const qs = params.toString()
      const data = await api<ContextItem[]>(`/api/context${qs ? `?${qs}` : ''}`)
      setItems(data)
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [filters?.scope, filters?.project_id, filters?.category, filters?.search])

  useEffect(() => { fetchItems() }, [fetchItems])

  const create = useCallback(async (body: {
    title: string
    content: string
    scope?: string
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

  const update = useCallback(async (id: string, body: Partial<Pick<ContextItem, 'title' | 'content' | 'scope' | 'project_id' | 'category' | 'source'>>) => {
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

  return { items, loading, fetch: fetchItems, create, update, remove }
}
