import { useState, useCallback, useEffect } from 'react'
import type { Project } from '../api/types'
import { api } from '../api/client'

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(async (status?: string) => {
    setLoading(true)
    try {
      const qs = status ? `?status=${status}` : ''
      const data = await api<Project[]>(`/api/projects${qs}`)
      setProjects(data)
    } catch {
      setProjects([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetch() }, [fetch])

  const create = useCallback(async (body: { name: string; description?: string; target_date?: string }) => {
    const p = await api<Project>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    setProjects(prev => [p, ...prev])
    return p
  }, [])

  const update = useCallback(async (id: string, body: Partial<Pick<Project, 'name' | 'description' | 'status' | 'target_date'>>) => {
    const p = await api<Project>(`/api/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    })
    setProjects(prev => prev.map(x => x.id === id ? p : x))
    return p
  }, [])

  const remove = useCallback(async (id: string) => {
    await api(`/api/projects/${id}`, { method: 'DELETE' })
    setProjects(prev => prev.filter(x => x.id !== id))
  }, [])

  return { projects, loading, fetch, create, update, remove }
}
