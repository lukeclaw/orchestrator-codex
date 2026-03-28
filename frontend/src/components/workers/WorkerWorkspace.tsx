import { useEffect, useRef, useCallback, useMemo, useState } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { useWorkerTabs } from '../../context/WorkerTabsContext'
import { useBrainPanel } from '../../context/BrainPanelContext'
import { useApp } from '../../context/AppContext'
import { useNotify } from '../../context/NotificationContext'
import WorkerTabBar from './WorkerTabBar'
import WorkerDetail from './WorkerDetail'
import type { WorkerDetailHandle } from './WorkerDetail'
import './WorkerWorkspace.css'

const MAX_LIVE_INSTANCES = 8
const MIN_PANE_WIDTH = 360

export default function WorkerWorkspace() {
  const { id: urlWorkerId } = useParams<{ id: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const notify = useNotify()
  const { sessions } = useApp()
  const brainPanel = useBrainPanel()

  const {
    tabs, leftActiveId, rightActiveId, isSplit, focusedPane, splitRatio,
    openTab, closeTab, pinTab, activateTab, setFocusedPane,
    enterSplit, exitSplit, updateSplitRatio,
    nextTab, prevTab, reopenLastClosed,
  } = useWorkerTabs()

  // Refs for URL sync guard and resize
  const urlSyncRef = useRef(false)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const resizingRef = useRef(false)
  const workerRefs = useRef<Map<string, WorkerDetailHandle>>(new Map())

  // --- URL sync: incoming (URL → tab state) ---
  useEffect(() => {
    if (!urlWorkerId) return
    if (urlSyncRef.current) {
      urlSyncRef.current = false
      return
    }
    const pin = searchParams.get('pin') === 'true'
    const splitWorkerId = searchParams.get('split')
    // Clear one-shot params (outgoing sync will re-add ?split if needed)
    if (pin || splitWorkerId) {
      setSearchParams({}, { replace: true })
    }
    openTab(urlWorkerId, pin || !!splitWorkerId)
    if (splitWorkerId) {
      openTab(splitWorkerId, true)
      enterSplit(splitWorkerId)
    }
  }, [urlWorkerId]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- URL sync: outgoing (tab state → URL) ---
  // Use history.replaceState directly to avoid React Router re-render cycle
  useEffect(() => {
    if (tabs.length === 0) return
    const activeId = focusedPane === 'left' ? leftActiveId : rightActiveId
    if (!activeId) return
    const otherActiveId = focusedPane === 'left' ? rightActiveId : leftActiveId
    let url = `/workers/${activeId}`
    if (isSplit && otherActiveId) {
      url += `?split=${otherActiveId}`
    }
    const currentUrl = window.location.pathname + window.location.search
    if (url !== currentUrl) {
      urlSyncRef.current = true
      window.history.replaceState(null, '', url)
    }
  }, [leftActiveId, rightActiveId, focusedPane, isSplit]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Navigate to /workers when all tabs are closed ---
  // Skip on first render (URL sync effect hasn't opened the initial tab yet)
  const mountedRef = useRef(false)
  useEffect(() => {
    if (!mountedRef.current) { mountedRef.current = true; return }
    if (tabs.length === 0) {
      navigate('/workers', { replace: true })
    }
  }, [tabs.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Auto-close tabs for deleted workers (silently) ---
  useEffect(() => {
    const sessionIds = new Set(sessions.map(s => s.id))
    for (const tab of tabs) {
      if (!sessionIds.has(tab.workerId)) {
        closeTab(tab.workerId)
      }
    }
  }, [sessions]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Brain panel auto-collapse on split ---
  useEffect(() => {
    if (!isSplit) return
    const workspace = workspaceRef.current
    if (!workspace) return
    const availableWidth = workspace.getBoundingClientRect().width
    if (availableWidth < MIN_PANE_WIDTH * 2 && !brainPanel.collapsed) {
      brainPanel.collapse()
      notify('Brain panel collapsed to fit split view', 'info')
    }
  }, [isSplit]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Window resize: auto-collapse split if too narrow ---
  // Skip the first 500ms after split opens to avoid racing with brain panel collapse animation
  useEffect(() => {
    if (!isSplit) return
    const workspace = workspaceRef.current
    if (!workspace) return
    let armed = false
    const armTimer = setTimeout(() => { armed = true }, 500)
    const observer = new ResizeObserver(([entry]) => {
      if (armed && entry.contentRect.width < MIN_PANE_WIDTH * 2) {
        exitSplit()
        notify('Split view closed — not enough space', 'info')
      }
    })
    observer.observe(workspace)
    return () => { clearTimeout(armTimer); observer.disconnect() }
  }, [isSplit, exitSplit, notify])

  // --- Keyboard shortcuts ---
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey

      // Cmd+Shift+[ — previous tab
      if (meta && e.shiftKey && e.key === '[') {
        e.preventDefault()
        prevTab()
        return
      }
      // Cmd+Shift+] — next tab
      if (meta && e.shiftKey && e.key === ']') {
        e.preventDefault()
        nextTab()
        return
      }
      // Cmd+W — close tab (only if no open editor tabs in focused WorkerDetail)
      if (meta && e.key === 'w' && !e.shiftKey) {
        const activeId = focusedPane === 'left' ? leftActiveId : rightActiveId
        if (activeId) {
          const handle = workerRefs.current.get(`${focusedPane}-${activeId}`)
          if (!handle?.hasEditorTabs()) {
            e.preventDefault()
            closeTab(activeId)
          }
        }
        return
      }
      // Cmd+\ — toggle split
      if (meta && e.key === '\\') {
        e.preventDefault()
        if (isSplit) {
          exitSplit()
        } else if (tabs.length >= 2) {
          enterSplit()
        }
        return
      }
      // Ctrl+1/2 — focus left/right pane
      if (e.ctrlKey && !e.metaKey && !e.shiftKey) {
        if (e.key === '1') { e.preventDefault(); setFocusedPane('left'); return }
        if (e.key === '2' && isSplit) { e.preventDefault(); setFocusedPane('right'); return }
      }
      // Cmd+Shift+T — reopen last closed
      if (meta && e.shiftKey && e.key === 'T') {
        e.preventDefault()
        reopenLastClosed()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [focusedPane, leftActiveId, rightActiveId, isSplit, prevTab, nextTab, closeTab, enterSplit, exitSplit, setFocusedPane, reopenLastClosed])

  // --- Split resize handle ---
  const handleSplitResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const workspace = workspaceRef.current
    if (!workspace) return
    const startX = e.clientX
    const containerWidth = workspace.getBoundingClientRect().width
    const startRatio = splitRatio
    resizingRef.current = true
    workspace.classList.add('ww-resizing')

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX
      const newRatio = startRatio + delta / containerWidth
      const minRatio = MIN_PANE_WIDTH / containerWidth
      const maxRatio = 1 - minRatio
      updateSplitRatio(Math.max(minRatio, Math.min(maxRatio, newRatio)))
    }
    const onUp = () => {
      resizingRef.current = false
      workspace.classList.remove('ww-resizing')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [splitRatio, updateSplitRatio])

  // --- Grow-only mounted sets per pane (lazy mount on first activation) ---
  // Workers mount when first activated and stay mounted until their tab is closed.
  // The array never reorders — new workers append at the end. This prevents
  // remounting on tab switch (stable keys, stable array reference).
  const tabWorkerIds = useMemo(() => tabs.map(t => t.workerId).join(','), [tabs])
  const [leftMounted, setLeftMounted] = useState<string[]>([])
  const [rightMounted, setRightMounted] = useState<string[]>([])

  useEffect(() => {
    if (!leftActiveId) return
    setLeftMounted(prev => {
      if (prev.includes(leftActiveId)) return prev // same ref → no re-render
      const next = [...prev, leftActiveId]
      if (next.length > MAX_LIVE_INSTANCES) {
        const evictIdx = next.findIndex(id => id !== leftActiveId)
        if (evictIdx !== -1) next.splice(evictIdx, 1)
      }
      return next
    })
  }, [leftActiveId])

  useEffect(() => {
    if (!rightActiveId) return
    setRightMounted(prev => {
      if (prev.includes(rightActiveId)) return prev
      const next = [...prev, rightActiveId]
      if (next.length > MAX_LIVE_INSTANCES) {
        const evictIdx = next.findIndex(id => id !== rightActiveId)
        if (evictIdx !== -1) next.splice(evictIdx, 1)
      }
      return next
    })
  }, [rightActiveId])

  // Prune when tabs are closed
  useEffect(() => {
    const ids = new Set(tabs.map(t => t.workerId))
    setLeftMounted(prev => { const next = prev.filter(id => ids.has(id)); return next.length === prev.length ? prev : next })
    setRightMounted(prev => { const next = prev.filter(id => ids.has(id)); return next.length === prev.length ? prev : next })
  }, [tabWorkerIds]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Stable callbacks (no per-worker closures — WorkerDetail calls with its own workerId) ---
  const handleEngagement = useCallback((workerId: string) => {
    pinTab(workerId)
  }, [pinTab])

  const handleDelete = useCallback((workerId: string) => {
    closeTab(workerId)
  }, [closeTab])

  // --- Stable ref callbacks — cached per key to avoid React cycling refs ---
  const refCallbacks = useRef(new Map<string, (h: WorkerDetailHandle | null) => void>())
  const getRefCallback = useCallback((key: string) => {
    let fn = refCallbacks.current.get(key)
    if (!fn) {
      fn = (handle: WorkerDetailHandle | null) => {
        if (handle) workerRefs.current.set(key, handle)
        else workerRefs.current.delete(key)
      }
      refCallbacks.current.set(key, fn)
    }
    return fn
  }, [])

  // --- Refit terminal when tab becomes active ---
  const prevLeftRef = useRef(leftActiveId)
  const prevRightRef = useRef(rightActiveId)
  useEffect(() => {
    if (leftActiveId && leftActiveId !== prevLeftRef.current) {
      requestAnimationFrame(() => {
        workerRefs.current.get(`left-${leftActiveId}`)?.refitTerminal()
      })
    }
    prevLeftRef.current = leftActiveId
  }, [leftActiveId])
  useEffect(() => {
    if (rightActiveId && rightActiveId !== prevRightRef.current) {
      requestAnimationFrame(() => {
        workerRefs.current.get(`right-${rightActiveId}`)?.refitTerminal()
      })
    }
    prevRightRef.current = rightActiveId
  }, [rightActiveId])

  // --- Render pane content ---
  // Both panes use hidden-DOM preservation with grow-only mounted arrays
  const renderPane = (pane: 'left' | 'right') => {
    const isLeft = pane === 'left'
    const activeId = isLeft ? leftActiveId : rightActiveId
    const mounted = isLeft ? leftMounted : rightMounted
    const isFocusedPane = focusedPane === pane

    if (!isLeft && !activeId) return null

    return (
      <div
        className={`ww-pane ${isFocusedPane ? 'ww-pane--focused' : ''} ${isLeft ? 'ww-pane--left' : 'ww-pane--right'}`}
        style={isSplit ? { width: `${(isLeft ? splitRatio : 1 - splitRatio) * 100}%` } : undefined}
        onClick={() => { if (isSplit) setFocusedPane(pane) }}
      >
        {mounted.map(workerId => {
          const isVisible = workerId === activeId
          const refKey = `${pane}-${workerId}`
          return (
            <div
              key={refKey}
              className={isVisible ? 'ww-pane-content ww-pane-content--visible' : 'ww-pane-content ww-pane-content--hidden'}
            >
              <WorkerDetail
                ref={getRefCallback(refKey)}
                workerId={workerId}
                isActive={isVisible}
                isFocused={isVisible && isFocusedPane}
                onEngagement={handleEngagement}
                onDelete={handleDelete}
              />
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div className="worker-workspace" ref={workspaceRef}>
      <WorkerTabBar />
      <div className="ww-pane-container">
        {renderPane('left')}
        {isSplit && (
          <>
            <div
              className="ww-resize-handle"
              onMouseDown={handleSplitResizeStart}
            />
            {renderPane('right')}
          </>
        )}
      </div>
    </div>
  )
}
