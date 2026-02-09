import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import type { Session, Project, Task } from '../api/types'
import { api } from '../api/client'

interface AppState {
  sessions: Session[]
  workers: Session[]
  projects: Project[]
  tasks: Task[]
  connected: boolean
  loading: boolean
  refresh: () => void
  removeSession: (id: string) => void
}

const AppContext = createContext<AppState>({
  sessions: [],
  workers: [],
  projects: [],
  tasks: [],
  connected: false,
  loading: true,
  refresh: () => {},
  removeSession: () => {},
})

export function useApp() {
  return useContext(AppContext)
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(true)

  const fetchAll = useCallback(async () => {
    try {
      const [s, p, t] = await Promise.all([
        api<Session[]>('/api/sessions?session_type=worker'),
        api<Project[]>('/api/projects').catch(() => []),
        api<Task[]>('/api/tasks').catch(() => []),
      ])
      setSessions(s)
      setProjects(p)
      setTasks(t)
    } catch (e) {
      console.error('Failed to fetch data:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  // WebSocket
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout>

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${window.location.host}/ws`)

      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        reconnectTimer = setTimeout(connect, 3000)
      }
      ws.onerror = () => ws?.close()
      ws.onmessage = () => {
        fetchAll()
      }
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [fetchAll])

  // Initial fetch + polling
  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 10000)
    return () => clearInterval(interval)
  }, [fetchAll])

  // sessions already filtered by session_type=worker from API
  const workers = sessions

  const removeSession = useCallback((id: string) => {
    setSessions(prev => prev.filter(s => s.id !== id))
  }, [])

  // Track current URL for brain context
  const location = useLocation()
  useEffect(() => {
    api('/api/brain/focus', {
      method: 'POST',
      body: JSON.stringify({ url: location.pathname }),
    }).catch(() => {})
  }, [location.pathname])

  return (
    <AppContext.Provider value={{ sessions, workers, projects, tasks, connected, loading, refresh: fetchAll, removeSession }}>
      {children}
    </AppContext.Provider>
  )
}
