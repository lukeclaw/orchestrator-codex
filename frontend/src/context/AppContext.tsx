import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import type { Session, Project, Task, Rdev } from '../api/types'
import { api } from '../api/client'

export interface SmartPastePayload {
  title?: string
  content?: string
  description?: string
  category?: string
}

interface AppState {
  sessions: Session[]
  workers: Session[]
  projects: Project[]
  tasks: Task[]
  rdevs: Rdev[]
  notificationCount: number
  connected: boolean
  loading: boolean
  smartPastePayload: SmartPastePayload | null
  setSmartPastePayload: (payload: SmartPastePayload | null) => void
  refresh: () => void
  refreshRdevs: (forceRefresh?: boolean) => Promise<void>
  refreshNotificationCount: () => Promise<void>
  removeSession: (id: string) => void
}

const AppContext = createContext<AppState>({
  sessions: [],
  workers: [],
  projects: [],
  tasks: [],
  rdevs: [],
  notificationCount: 0,
  connected: false,
  loading: true,
  smartPastePayload: null,
  setSmartPastePayload: () => {},
  refresh: () => {},
  refreshRdevs: async () => {},
  refreshNotificationCount: async () => {},
  removeSession: () => {},
})

export function useApp() {
  return useContext(AppContext)
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [rdevs, setRdevs] = useState<Rdev[]>([])
  const [notificationCount, setNotificationCount] = useState(0)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(true)
  const [smartPastePayload, setSmartPastePayload] = useState<SmartPastePayload | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [s, p, t, r] = await Promise.all([
        api<Session[]>('/api/sessions?session_type=worker&include_preview=true'),
        api<Project[]>('/api/projects').catch(() => []),
        api<Task[]>('/api/tasks').catch(() => []),
        api<Rdev[]>('/api/rdevs').catch(() => []),
      ])
      setSessions(s)
      setProjects(p)
      setTasks(t)
      setRdevs(r)
    } catch (e) {
      console.error('Failed to fetch data:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshNotificationCount = useCallback(async () => {
    try {
      const data = await api<{ count: number }>('/api/notifications/count')
      setNotificationCount(data.count)
    } catch {
      // Ignore errors
    }
  }, [])

  const refreshRdevs = useCallback(async (forceRefresh = false) => {
    try {
      const url = forceRefresh ? '/api/rdevs?refresh=true' : '/api/rdevs'
      const data = await api<Rdev[]>(url)
      setRdevs(data)
    } catch (e) {
      console.error('Failed to fetch rdevs:', e)
    }
  }, [])

  // WebSocket
  const location = useLocation()
  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout>
    let intentionalClose = false

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${window.location.host}/ws`)

      ws.onopen = () => {
        setConnected(true)
        // Send current focus on connect
        ws?.send(JSON.stringify({ type: 'focus_update', url: location.pathname }))
      }
      ws.onclose = () => {
        setConnected(false)
        // Only reconnect on unexpected disconnects, not effect cleanup
        if (!intentionalClose) {
          reconnectTimer = setTimeout(connect, 3000)
        }
      }
      ws.onerror = () => ws?.close()
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'request_focus') {
            // Backend requesting current URL - respond immediately
            ws?.send(JSON.stringify({ type: 'focus_response', url: location.pathname }))
          } else {
            // Other messages trigger data refresh
            fetchAll()
          }
        } catch {
          fetchAll()
        }
      }
    }

    connect()
    return () => {
      intentionalClose = true
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [fetchAll, location.pathname])

  // Initial fetch + polling
  useEffect(() => {
    fetchAll()
    refreshNotificationCount()
    const interval = setInterval(() => { fetchAll(); refreshNotificationCount() }, 10000)
    return () => clearInterval(interval)
  }, [fetchAll, refreshNotificationCount])

  // Periodic health check to detect disconnected workers (every 5 minutes)
  useEffect(() => {
    const healthCheck = async () => {
      try {
        const result = await api<{ disconnected: string[] }>('/api/sessions/health-check-all', { method: 'POST' })
        if (result.disconnected && result.disconnected.length > 0) {
          // Refresh data if any workers were marked disconnected
          fetchAll()
        }
      } catch {
        // Ignore health check errors
      }
    }
    
    // Run health check every 5 minutes (300000ms)
    const interval = setInterval(healthCheck, 300000)
    // Also run once after initial load (after 10 seconds)
    const timeout = setTimeout(healthCheck, 10000)
    
    return () => {
      clearInterval(interval)
      clearTimeout(timeout)
    }
  }, [fetchAll])

  // sessions already filtered by session_type=worker from API
  const workers = sessions

  const removeSession = useCallback((id: string) => {
    setSessions(prev => prev.filter(s => s.id !== id))
  }, [])

  // Focus tracking now handled via WebSocket (see above)

  return (
    <AppContext.Provider value={{ sessions, workers, projects, tasks, rdevs, notificationCount, connected, loading, smartPastePayload, setSmartPastePayload, refresh: fetchAll, refreshRdevs, refreshNotificationCount, removeSession }}>
      {children}
    </AppContext.Provider>
  )
}
