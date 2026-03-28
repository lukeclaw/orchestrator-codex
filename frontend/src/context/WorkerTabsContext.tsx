import { createContext, useContext, useCallback, useState, useEffect, useRef, type ReactNode } from 'react'
import { useApp } from './AppContext'

// --- Types ---

export interface WorkerTab {
  workerId: string
  openedAt: number
  lastActiveAt: number
}

interface WorkerTabsState {
  tabs: WorkerTab[]
  leftActiveId: string | null
  rightActiveId: string | null
  isSplit: boolean
  focusedPane: 'left' | 'right'
  splitRatio: number
  recentlyClosed: string[]
}

interface WorkerTabsContextValue extends WorkerTabsState {
  openTab: (workerId: string) => void
  closeTab: (workerId: string) => void
  activateTab: (workerId: string, pane?: 'left' | 'right') => void
  setFocusedPane: (pane: 'left' | 'right') => void
  toggleSplit: () => void
  enterSplit: (rightWorkerId?: string) => void
  exitSplit: () => void
  updateSplitRatio: (ratio: number) => void
  reopenLastClosed: () => void
  nextTab: () => void
  prevTab: () => void
}

// --- Constants ---

const STORAGE_KEY = 'orchestrator-worker-tabs'
const MAX_RECENTLY_CLOSED = 5

const EMPTY_STATE: WorkerTabsState = {
  tabs: [],
  leftActiveId: null,
  rightActiveId: null,
  isSplit: false,
  focusedPane: 'left',
  splitRatio: 0.5,
  recentlyClosed: [],
}

// --- Persistence ---

function loadState(): WorkerTabsState {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return EMPTY_STATE
    const parsed = JSON.parse(raw) as Partial<WorkerTabsState>
    return {
      tabs: Array.isArray(parsed.tabs) ? parsed.tabs : [],
      leftActiveId: parsed.leftActiveId ?? null,
      rightActiveId: parsed.rightActiveId ?? null,
      isSplit: parsed.isSplit ?? false,
      focusedPane: parsed.focusedPane === 'right' ? 'right' : 'left',
      splitRatio: typeof parsed.splitRatio === 'number' ? parsed.splitRatio : 0.5,
      recentlyClosed: Array.isArray(parsed.recentlyClosed) ? parsed.recentlyClosed : [],
    }
  } catch {
    return EMPTY_STATE
  }
}

function saveState(state: WorkerTabsState) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      tabs: state.tabs,
      leftActiveId: state.leftActiveId,
      rightActiveId: state.rightActiveId,
      isSplit: state.isSplit,
      focusedPane: state.focusedPane,
      splitRatio: state.splitRatio,
      recentlyClosed: state.recentlyClosed,
    }))
  } catch { /* ignore */ }
}

// --- Helpers ---

function findNearestTab(tabs: WorkerTab[], closedId: string): string | null {
  const idx = tabs.findIndex(t => t.workerId === closedId)
  if (idx === -1) return tabs.length > 0 ? tabs[0].workerId : null
  if (idx + 1 < tabs.length) return tabs[idx + 1].workerId
  if (idx - 1 >= 0) return tabs[idx - 1].workerId
  return null
}

function getMostRecentlyActive(tabs: WorkerTab[], exclude?: string): string | null {
  const candidates = exclude ? tabs.filter(t => t.workerId !== exclude) : tabs
  if (candidates.length === 0) return null
  return candidates.reduce((best, t) => t.lastActiveAt > best.lastActiveAt ? t : best).workerId
}

// --- Context ---

const WorkerTabsContext = createContext<WorkerTabsContextValue | null>(null)

export function WorkerTabsProvider({ children }: { children: ReactNode }) {
  const { sessions } = useApp()
  const [state, setState] = useState<WorkerTabsState>(loadState)
  const initialPruneRef = useRef(false)
  const sessionsRef = useRef(sessions)
  sessionsRef.current = sessions

  // Prune stale tabs on mount (workers that no longer exist)
  useEffect(() => {
    if (initialPruneRef.current || sessions.length === 0) return
    initialPruneRef.current = true
    const sessionIds = new Set(sessions.map(s => s.id))
    setState(prev => {
      const validTabs = prev.tabs.filter(t => sessionIds.has(t.workerId))
      if (validTabs.length === prev.tabs.length) return prev
      const newState = {
        ...prev,
        tabs: validTabs,
        leftActiveId: prev.leftActiveId && sessionIds.has(prev.leftActiveId) ? prev.leftActiveId : (validTabs[0]?.workerId ?? null),
        rightActiveId: prev.rightActiveId && sessionIds.has(prev.rightActiveId) ? prev.rightActiveId : null,
        isSplit: prev.isSplit && prev.rightActiveId != null && sessionIds.has(prev.rightActiveId),
      }
      saveState(newState)
      return newState
    })
  }, [sessions])

  // Auto-close tabs for deleted workers (runtime)
  useEffect(() => {
    if (!initialPruneRef.current) return
    const sessionIds = new Set(sessions.map(s => s.id))
    setState(prev => {
      const removed = prev.tabs.filter(t => !sessionIds.has(t.workerId))
      if (removed.length === 0) return prev
      const validTabs = prev.tabs.filter(t => sessionIds.has(t.workerId))
      const newState = {
        ...prev,
        tabs: validTabs,
        leftActiveId: prev.leftActiveId && sessionIds.has(prev.leftActiveId) ? prev.leftActiveId : (validTabs[0]?.workerId ?? null),
        rightActiveId: prev.rightActiveId && sessionIds.has(prev.rightActiveId) ? prev.rightActiveId : null,
      }
      if (!newState.rightActiveId && newState.isSplit) newState.isSplit = false
      saveState(newState)
      return newState
    })
  }, [sessions])

  // --- Actions ---

  const openTab = useCallback((workerId: string) => {
    setState(prev => {
      const now = Date.now()
      const existing = prev.tabs.find(t => t.workerId === workerId)
      const pane = prev.focusedPane

      if (existing) {
        // Already open — just activate it
        const tabs = prev.tabs.map(t =>
          t.workerId === workerId ? { ...t, lastActiveAt: now } : t
        )
        const newState = {
          ...prev,
          tabs,
          [pane === 'left' ? 'leftActiveId' : 'rightActiveId']: workerId,
        }
        saveState(newState)
        return newState
      }

      // New tab — append at end
      const newTab: WorkerTab = { workerId, openedAt: now, lastActiveAt: now }
      const tabs = [...prev.tabs, newTab]
      const newState = {
        ...prev,
        tabs,
        [pane === 'left' ? 'leftActiveId' : 'rightActiveId']: workerId,
      }
      saveState(newState)
      return newState
    })
  }, [])

  const closeTab = useCallback((workerId: string) => {
    setState(prev => {
      const tab = prev.tabs.find(t => t.workerId === workerId)
      if (!tab) return prev

      const remaining = prev.tabs.filter(t => t.workerId !== workerId)
      const recentlyClosed = [workerId, ...prev.recentlyClosed.filter(id => id !== workerId)]
        .slice(0, MAX_RECENTLY_CLOSED)

      let { leftActiveId, rightActiveId, isSplit } = prev

      if (leftActiveId === workerId) {
        leftActiveId = findNearestTab(remaining, workerId)
      }
      if (rightActiveId === workerId) {
        rightActiveId = findNearestTab(remaining, workerId)
        if (!rightActiveId) isSplit = false
      }
      if (isSplit && leftActiveId === rightActiveId) {
        isSplit = false
        rightActiveId = null
      }

      const newState = { ...prev, tabs: remaining, leftActiveId, rightActiveId, isSplit, recentlyClosed, focusedPane: isSplit ? prev.focusedPane : 'left' as const }
      saveState(newState)
      return newState
    })
  }, [])

  const activateTab = useCallback((workerId: string, pane?: 'left' | 'right') => {
    setState(prev => {
      const tab = prev.tabs.find(t => t.workerId === workerId)
      if (!tab) return prev
      const targetPane = pane ?? prev.focusedPane

      if (prev.isSplit && !pane) {
        const otherPane: 'left' | 'right' = targetPane === 'left' ? 'right' : 'left'
        const otherActiveId = otherPane === 'left' ? prev.leftActiveId : prev.rightActiveId
        if (workerId === otherActiveId) {
          const newState = { ...prev, focusedPane: otherPane }
          saveState(newState)
          return newState
        }
      }

      const now = Date.now()
      const tabs = prev.tabs.map(t =>
        t.workerId === workerId ? { ...t, lastActiveAt: now } : t
      )
      const newState = {
        ...prev,
        tabs,
        [targetPane === 'left' ? 'leftActiveId' : 'rightActiveId']: workerId,
      }
      saveState(newState)
      return newState
    })
  }, [])

  const setFocusedPane = useCallback((pane: 'left' | 'right') => {
    setState(prev => {
      if (prev.focusedPane === pane) return prev
      const newState = { ...prev, focusedPane: pane }
      saveState(newState)
      return newState
    })
  }, [])

  const enterSplit = useCallback((rightWorkerId?: string) => {
    setState(prev => {
      if (prev.isSplit) return prev
      const rightId = rightWorkerId ?? getMostRecentlyActive(prev.tabs, prev.leftActiveId ?? undefined)
      const newState = { ...prev, isSplit: true, rightActiveId: rightId, focusedPane: 'left' as const }
      saveState(newState)
      return newState
    })
  }, [])

  const exitSplit = useCallback(() => {
    setState(prev => {
      if (!prev.isSplit) return prev
      const newState = { ...prev, isSplit: false, rightActiveId: null, focusedPane: 'left' as const }
      saveState(newState)
      return newState
    })
  }, [])

  const toggleSplit = useCallback(() => {
    setState(prev => {
      if (prev.isSplit) {
        const newState = { ...prev, isSplit: false, rightActiveId: null, focusedPane: 'left' as const }
        saveState(newState)
        return newState
      }
      const rightId = getMostRecentlyActive(prev.tabs, prev.leftActiveId ?? undefined)
      const newState = { ...prev, isSplit: true, rightActiveId: rightId, focusedPane: 'left' as const }
      saveState(newState)
      return newState
    })
  }, [])

  const updateSplitRatio = useCallback((ratio: number) => {
    setState(prev => {
      const clamped = Math.max(0.2, Math.min(0.8, ratio))
      const newState = { ...prev, splitRatio: clamped }
      saveState(newState)
      return newState
    })
  }, [])

  const reopenLastClosed = useCallback(() => {
    setState(prev => {
      if (prev.recentlyClosed.length === 0) return prev
      const sessionIds = new Set(sessionsRef.current.map(s => s.id))
      const validIdx = prev.recentlyClosed.findIndex(id => sessionIds.has(id))
      if (validIdx === -1) return { ...prev, recentlyClosed: [] }
      const workerId = prev.recentlyClosed[validIdx]
      const rest = [...prev.recentlyClosed.slice(0, validIdx), ...prev.recentlyClosed.slice(validIdx + 1)]
      const now = Date.now()
      const newTab: WorkerTab = { workerId, openedAt: now, lastActiveAt: now }
      const tabs = [...prev.tabs, newTab]
      const pane = prev.focusedPane
      const newState = {
        ...prev,
        tabs,
        recentlyClosed: rest,
        [pane === 'left' ? 'leftActiveId' : 'rightActiveId']: workerId,
      }
      saveState(newState)
      return newState
    })
  }, [])

  const nextTab = useCallback(() => {
    setState(prev => {
      if (prev.tabs.length <= 1) return prev
      const pane = prev.focusedPane
      const activeId = pane === 'left' ? prev.leftActiveId : prev.rightActiveId
      const idx = prev.tabs.findIndex(t => t.workerId === activeId)
      if (idx === -1) return prev
      const nextIdx = (idx + 1) % prev.tabs.length
      const now = Date.now()
      const nextId = prev.tabs[nextIdx].workerId
      const tabs = prev.tabs.map(t =>
        t.workerId === nextId ? { ...t, lastActiveAt: now } : t
      )
      const newState = {
        ...prev,
        tabs,
        [pane === 'left' ? 'leftActiveId' : 'rightActiveId']: nextId,
      }
      saveState(newState)
      return newState
    })
  }, [])

  const prevTab = useCallback(() => {
    setState(prev => {
      if (prev.tabs.length <= 1) return prev
      const pane = prev.focusedPane
      const activeId = pane === 'left' ? prev.leftActiveId : prev.rightActiveId
      const idx = prev.tabs.findIndex(t => t.workerId === activeId)
      if (idx === -1) return prev
      const prevIdx = (idx - 1 + prev.tabs.length) % prev.tabs.length
      const now = Date.now()
      const prevId = prev.tabs[prevIdx].workerId
      const tabs = prev.tabs.map(t =>
        t.workerId === prevId ? { ...t, lastActiveAt: now } : t
      )
      const newState = {
        ...prev,
        tabs,
        [pane === 'left' ? 'leftActiveId' : 'rightActiveId']: prevId,
      }
      saveState(newState)
      return newState
    })
  }, [])

  const value: WorkerTabsContextValue = {
    ...state,
    openTab,
    closeTab,
    activateTab,
    setFocusedPane,
    toggleSplit,
    enterSplit,
    exitSplit,
    updateSplitRatio,
    reopenLastClosed,
    nextTab,
    prevTab,
  }

  return (
    <WorkerTabsContext.Provider value={value}>
      {children}
    </WorkerTabsContext.Provider>
  )
}

export function useWorkerTabs(): WorkerTabsContextValue {
  const ctx = useContext(WorkerTabsContext)
  if (!ctx) throw new Error('useWorkerTabs must be used within WorkerTabsProvider')
  return ctx
}
