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
  updateAvailable: boolean
  connected: boolean
  loading: boolean
  smartPastePayload: SmartPastePayload | null
  interactiveCliSessions: Set<string>
  interactiveCliMinimized: Set<string>
  browserViewSessions: Set<string>
  browserViewMinimized: Set<string>
  setSmartPastePayload: (payload: SmartPastePayload | null) => void
  refresh: () => void
  refreshRdevs: (forceRefresh?: boolean) => Promise<void>
  refreshNotificationCount: () => Promise<void>
  removeSession: (id: string) => void
  closeInteractiveCli: (sessionId: string) => void
  closeBrowserView: (sessionId: string) => void
  setUpdateAvailable: (available: boolean) => void
}

const AppContext = createContext<AppState>({
  sessions: [],
  workers: [],
  projects: [],
  tasks: [],
  rdevs: [],
  notificationCount: 0,
  updateAvailable: false,
  connected: false,
  loading: true,
  smartPastePayload: null,
  interactiveCliSessions: new Set(),
  interactiveCliMinimized: new Set(),
  browserViewSessions: new Set(),
  browserViewMinimized: new Set(),
  setSmartPastePayload: () => {},
  refresh: () => {},
  refreshRdevs: async () => {},
  refreshNotificationCount: async () => {},
  removeSession: () => {},
  closeInteractiveCli: () => {},
  closeBrowserView: () => {},
  setUpdateAvailable: () => {},
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
  const [interactiveCliSessions, setInteractiveCliSessions] = useState<Set<string>>(new Set())
  const [interactiveCliMinimized, setInteractiveCliMinimized] = useState<Set<string>>(new Set())
  const [browserViewSessions, setBrowserViewSessions] = useState<Set<string>>(new Set())
  const [browserViewMinimized, setBrowserViewMinimized] = useState<Set<string>>(new Set())
  const [updateAvailable, setUpdateAvailable] = useState(false)

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
          } else if (msg.type === 'interactive_cli_opened' && msg.data?.session_id) {
            setInteractiveCliSessions(prev => new Set([...prev, msg.data.session_id]))
            fetchAll()
          } else if (msg.type === 'interactive_cli_closed' && msg.data?.session_id) {
            setInteractiveCliSessions(prev => {
              const next = new Set(prev)
              next.delete(msg.data.session_id)
              return next
            })
            setInteractiveCliMinimized(prev => {
              const next = new Set(prev)
              next.delete(msg.data.session_id)
              return next
            })
            fetchAll()
          } else if (msg.type === 'interactive_cli_minimized' && msg.data?.session_id) {
            setInteractiveCliMinimized(prev => new Set([...prev, msg.data.session_id]))
          } else if (msg.type === 'interactive_cli_restored' && msg.data?.session_id) {
            setInteractiveCliMinimized(prev => {
              const next = new Set(prev)
              next.delete(msg.data.session_id)
              return next
            })
          } else if (msg.type === 'browser_view_started' && msg.data?.session_id) {
            setBrowserViewSessions(prev => new Set([...prev, msg.data.session_id]))
            fetchAll()
          } else if (msg.type === 'browser_view_closed' && msg.data?.session_id) {
            setBrowserViewSessions(prev => {
              const next = new Set(prev)
              next.delete(msg.data.session_id)
              return next
            })
            setBrowserViewMinimized(prev => {
              const next = new Set(prev)
              next.delete(msg.data.session_id)
              return next
            })
            fetchAll()
          } else if (msg.type === 'browser_view_minimized' && msg.data?.session_id) {
            setBrowserViewMinimized(prev => new Set([...prev, msg.data.session_id]))
          } else if (msg.type === 'browser_view_restored' && msg.data?.session_id) {
            setBrowserViewMinimized(prev => {
              const next = new Set(prev)
              next.delete(msg.data.session_id)
              return next
            })
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
        const result = await api<{ disconnected: string[]; auto_reconnected: string[] }>('/api/sessions/health-check-all', { method: 'POST' })
        if ((result.disconnected && result.disconnected.length > 0) ||
            (result.auto_reconnected && result.auto_reconnected.length > 0)) {
          // Refresh data if any workers were marked disconnected or auto-reconnected
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

  // Global update check — runs once on load and every 24 hours
  useEffect(() => {
    const checkUpdate = async () => {
      try {
        const data = await api<{ update_available: boolean }>('/api/updates/check')
        setUpdateAvailable(data.update_available)
      } catch {
        // Ignore update check errors
      }
    }
    checkUpdate()
    const interval = setInterval(checkUpdate, 24 * 60 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  // sessions already filtered by session_type=worker from API
  const workers = sessions

  const removeSession = useCallback((id: string) => {
    setSessions(prev => prev.filter(s => s.id !== id))
  }, [])

  const closeInteractiveCli = useCallback((sessionId: string) => {
    setInteractiveCliSessions(prev => {
      const next = new Set(prev)
      next.delete(sessionId)
      return next
    })
    setInteractiveCliMinimized(prev => {
      const next = new Set(prev)
      next.delete(sessionId)
      return next
    })
  }, [])

  const closeBrowserView = useCallback((sessionId: string) => {
    setBrowserViewSessions(prev => {
      const next = new Set(prev)
      next.delete(sessionId)
      return next
    })
    setBrowserViewMinimized(prev => {
      const next = new Set(prev)
      next.delete(sessionId)
      return next
    })
  }, [])

  // Focus tracking now handled via WebSocket (see above)

  return (
    <AppContext.Provider value={{ sessions, workers, projects, tasks, rdevs, notificationCount, updateAvailable, connected, loading, smartPastePayload, interactiveCliSessions, interactiveCliMinimized, browserViewSessions, browserViewMinimized, setSmartPastePayload, refresh: fetchAll, refreshRdevs, refreshNotificationCount, removeSession, closeInteractiveCli, closeBrowserView, setUpdateAvailable }}>
      {children}
    </AppContext.Provider>
  )
}
