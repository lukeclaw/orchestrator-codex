import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import type { Session, Decision, Activity, Project, Task, PullRequest } from '../api/types'
import { api } from '../api/client'

interface AppState {
  sessions: Session[]
  workers: Session[]
  decisions: Decision[]
  activities: Activity[]
  projects: Project[]
  tasks: Task[]
  prs: PullRequest[]
  connected: boolean
  loading: boolean
  refresh: () => void
  removeSession: (id: string) => void
}

const AppContext = createContext<AppState>({
  sessions: [],
  workers: [],
  decisions: [],
  activities: [],
  projects: [],
  tasks: [],
  prs: [],
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
  const [decisions, setDecisions] = useState<Decision[]>([])
  const [activities, setActivities] = useState<Activity[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [prs, setPrs] = useState<PullRequest[]>([])
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(true)

  const fetchAll = useCallback(async () => {
    try {
      const [s, d, a, p, t, pr] = await Promise.all([
        api<Session[]>('/api/sessions'),
        api<Decision[]>('/api/decisions/pending'),
        api<Activity[]>('/api/activities?limit=20'),
        api<Project[]>('/api/projects').catch(() => []),
        api<Task[]>('/api/tasks').catch(() => []),
        api<PullRequest[]>('/api/prs').catch(() => []),
      ])
      setSessions(s)
      setDecisions(d)
      setActivities(a)
      setProjects(p)
      setTasks(t)
      setPrs(pr)
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
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${location.host}/ws`)

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

  const workers = sessions.filter(s => s.name !== 'brain')

  const removeSession = useCallback((id: string) => {
    setSessions(prev => prev.filter(s => s.id !== id))
  }, [])

  return (
    <AppContext.Provider value={{ sessions, workers, decisions, activities, projects, tasks, prs, connected, loading, refresh: fetchAll, removeSession }}>
      {children}
    </AppContext.Provider>
  )
}
