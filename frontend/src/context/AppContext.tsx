import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import type { Session, Project, Task, Rdev, PrSearchItem, PrSearchResponse } from '../api/types'
import { api, ApiError } from '../api/client'

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
  prBadgeCount: number
  prCache: Record<string, { prs: PrSearchItem[]; fetchedAt: number }>
  prRefreshing: boolean
  prErrors: Record<string, string>
  fetchPrs: (tab: 'active' | 'recent', days?: number, refresh?: boolean) => void
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
  prBadgeCount: 0,
  prCache: {},
  prRefreshing: false,
  prErrors: {},
  fetchPrs: () => {},
})

export function useApp() {
  return useContext(AppContext)
}

const PR_CACHE_TTL = 20 * 60 * 1000 // 20 minutes

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
  const [prBadgeCount, setPrBadgeCount] = useState(0)
  const [prCache, setPrCache] = useState<Record<string, { prs: PrSearchItem[]; fetchedAt: number }>>({})
  const [prRefreshing, setPrRefreshing] = useState(false)
  const [prErrors, setPrErrors] = useState<Record<string, string>>({})
  const prCacheRef = useRef<Record<string, { prs: PrSearchItem[]; fetchedAt: number }>>({})
  const location = useLocation()

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

  const wsRef = useRef<WebSocket | null>(null)
  const locationRef = useRef(location.pathname)
  const prAbortRef = useRef<Record<string, AbortController>>({})


  const fetchPrs = useCallback(async (tab: 'active' | 'recent', days = 7, refresh = false) => {
    const cacheKey = tab === 'active' ? 'active' : `recent:${days}`

    // Check cache (skip on manual refresh)
    if (!refresh) {
      const cached = prCacheRef.current[cacheKey]
      if (cached && Date.now() - cached.fetchedAt < PR_CACHE_TTL) {
        return
      }
    }

    // Per-key abort: only cancels a previous fetch for the same cache key
    prAbortRef.current[cacheKey]?.abort()
    const ctrl = new AbortController()
    prAbortRef.current[cacheKey] = ctrl

    setPrRefreshing(true)

    try {
      const params = new URLSearchParams({ tab })
      if (tab === 'recent') params.set('days', String(days))
      if (refresh) params.set('refresh', 'true')

      const data = await api<PrSearchResponse>(`/api/prs?${params}`, { signal: ctrl.signal })
      if (ctrl.signal.aborted) return

      const now = Date.now()
      const entry = { prs: data.prs, fetchedAt: now }
      prCacheRef.current[cacheKey] = entry
      setPrCache(prev => ({ ...prev, [cacheKey]: entry }))
      setPrErrors(prev => {
        if (!(cacheKey in prev)) return prev
        const next = { ...prev }
        delete next[cacheKey]
        return next
      })

      // Update badge from active tab
      if (cacheKey === 'active') {
        setPrBadgeCount(data.prs.filter(p => p.attention_level === 1).length)
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return
      if (e instanceof ApiError && e.status === 401) {
        setPrErrors(prev => ({ ...prev, [cacheKey]: 'auth' }))
      } else {
        setPrErrors(prev => ({ ...prev, [cacheKey]: e instanceof Error ? e.message : 'Failed to fetch PRs' }))
      }
    } finally {
      if (!ctrl.signal.aborted) {
        setPrRefreshing(false)
      }
    }
  }, [])

  // Auto-refresh active PRs on mount and every 20 minutes
  useEffect(() => {
    fetchPrs('active')
    const interval = setInterval(() => {
      delete prCacheRef.current['active']
      fetchPrs('active')
    }, PR_CACHE_TTL)
    return () => clearInterval(interval)
  }, [fetchPrs])

  // Background pre-fetch recent PRs (default 7 days) on mount and every 20 minutes
  useEffect(() => {
    fetchPrs('recent')
    const interval = setInterval(() => {
      delete prCacheRef.current['recent:7']
      fetchPrs('recent')
    }, PR_CACHE_TTL)
    return () => clearInterval(interval)
  }, [fetchPrs])

  // Keep locationRef in sync
  useEffect(() => {
    locationRef.current = location.pathname
  })
  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>
    let intentionalClose = false

    function connect() {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${proto}//${window.location.host}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
        // Send current focus on connect
        ws.send(JSON.stringify({ type: 'focus_update', url: locationRef.current }))
      }
      ws.onclose = () => {
        wsRef.current = null
        setConnected(false)
        // Only reconnect on unexpected disconnects, not effect cleanup
        if (!intentionalClose) {
          reconnectTimer = setTimeout(connect, 3000)
        }
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'request_focus') {
            // Backend requesting current URL - respond immediately
            ws.send(JSON.stringify({ type: 'focus_response', url: locationRef.current }))
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
          } else if (msg.type === 'reconnect.step_changed' && msg.data?.session_id) {
            setSessions(prev => prev.map(s =>
              s.id === msg.data.session_id
                ? { ...s, reconnect_step: msg.data.step }
                : s
            ))
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
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [fetchAll])

  // Send focus_update when the route changes (without tearing down the WebSocket)
  useEffect(() => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'focus_update', url: location.pathname }))
    }
  }, [location.pathname])

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
    <AppContext.Provider value={{ sessions, workers, projects, tasks, rdevs, notificationCount, updateAvailable, connected, loading, smartPastePayload, interactiveCliSessions, interactiveCliMinimized, browserViewSessions, browserViewMinimized, setSmartPastePayload, refresh: fetchAll, refreshRdevs, refreshNotificationCount, removeSession, closeInteractiveCli, closeBrowserView, setUpdateAvailable, prBadgeCount, prCache, prRefreshing, prErrors, fetchPrs }}>
      {children}
    </AppContext.Provider>
  )
}
