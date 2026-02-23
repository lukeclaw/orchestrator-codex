import { useState, useCallback, useEffect } from 'react'
import type { Skill } from '../api/types'
import { api } from '../api/client'

interface SkillFilters {
  target?: string
  search?: string
}

export function useSkills(filters?: SkillFilters) {
  const [items, setItems] = useState<Skill[]>([])
  const [loading, setLoading] = useState(true)

  const fetchItems = useCallback(async (f?: SkillFilters) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      const active = f || filters
      if (active?.target) params.set('target', active.target)
      if (active?.search) params.set('search', active.search)
      const qs = params.toString()
      const data = await api<Skill[]>(`/api/skills${qs ? `?${qs}` : ''}`)
      setItems(data)
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [filters?.target, filters?.search])

  useEffect(() => { fetchItems() }, [fetchItems])

  const getItem = useCallback(async (skill: Skill): Promise<Skill> => {
    if (skill.type === 'built_in') {
      // Parse builtin:target:name from synthetic ID
      const parts = skill.id.split(':')
      const target = parts[1]
      const name = parts.slice(2).join(':')
      return api<Skill>(`/api/skills/builtin/${target}/${name}`)
    }
    return api<Skill>(`/api/skills/${skill.id}`)
  }, [])

  const create = useCallback(async (body: {
    name: string
    target: string
    content: string
    description?: string
  }) => {
    const item = await api<Skill>('/api/skills', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    setItems(prev => [...prev, item])
    return item
  }, [])

  const update = useCallback(async (id: string, body: Partial<Pick<Skill, 'name' | 'target' | 'content' | 'description'>>) => {
    const item = await api<Skill>(`/api/skills/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    })
    setItems(prev => prev.map(x => x.id === id ? item : x))
    return item
  }, [])

  const remove = useCallback(async (id: string) => {
    await api(`/api/skills/${id}`, { method: 'DELETE' })
    setItems(prev => prev.filter(x => x.id !== id))
  }, [])

  const toggleEnabled = useCallback(async (skill: Skill) => {
    const newEnabled = !skill.enabled

    // Optimistic update
    setItems(prev => prev.map(s => s.id === skill.id ? { ...s, enabled: newEnabled } : s))

    try {
      if (skill.type === 'built_in') {
        const parts = skill.id.split(':')
        const target = parts[1]
        const name = parts.slice(2).join(':')
        await api(`/api/skills/builtin/${target}/${name}`, {
          method: 'PATCH',
          body: JSON.stringify({ enabled: newEnabled }),
        })
      } else {
        await api(`/api/skills/${skill.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ enabled: newEnabled }),
        })
      }
    } catch {
      // Revert optimistic update on failure
      setItems(prev => prev.map(s => s.id === skill.id ? { ...s, enabled: skill.enabled } : s))
    }
  }, [])

  return { items, loading, fetch: fetchItems, getItem, create, update, remove, toggleEnabled }
}
