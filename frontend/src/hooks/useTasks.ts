import { useState, useCallback, useEffect } from 'react'
import type { Task } from '../api/types'
import { api } from '../api/client'

interface TaskFilters {
  project_id?: string
  status?: string
  assigned_session_id?: string
}

export function useTasks(filters?: TaskFilters) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)

  const fetch = useCallback(async (f?: TaskFilters) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      const active = f || filters
      if (active?.project_id) params.set('project_id', active.project_id)
      if (active?.status) params.set('status', active.status)
      if (active?.assigned_session_id) params.set('assigned_session_id', active.assigned_session_id)
      const qs = params.toString()
      const data = await api<Task[]>(`/api/tasks${qs ? `?${qs}` : ''}`)
      setTasks(data)
    } catch {
      setTasks([])
    } finally {
      setLoading(false)
    }
  }, [filters?.project_id, filters?.status, filters?.assigned_session_id])

  useEffect(() => { fetch() }, [fetch])

  const create = useCallback(async (body: { project_id: string; title: string; description?: string; priority?: string }) => {
    const t = await api<Task>('/api/tasks', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    setTasks(prev => [t, ...prev])
    return t
  }, [])

  const update = useCallback(async (id: string, body: Partial<Pick<Task, 'title' | 'description' | 'status' | 'priority' | 'assigned_session_id'>>) => {
    const t = await api<Task>(`/api/tasks/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    })
    setTasks(prev => prev.map(x => x.id === id ? t : x))
    return t
  }, [])

  const remove = useCallback(async (id: string) => {
    await api(`/api/tasks/${id}`, { method: 'DELETE' })
    setTasks(prev => prev.filter(x => x.id !== id))
  }, [])

  return { tasks, loading, fetch, create, update, remove }
}
