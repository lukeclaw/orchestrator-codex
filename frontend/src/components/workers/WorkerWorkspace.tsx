import { useEffect, useRef, useCallback, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
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
  const navigate = useNavigate()
  const notify = useNotify()
  const { sessions } = useApp()
  const brainPanel = useBrainPanel()

  const {
    tabs, leftActiveId, rightActiveId, isSplit, focusedPane,
    openTab, closeTab, activateTab, setFocusedPane,
    enterSplit, exitSplit,
    nextTab, prevTab, reopenLastClosed, restoreFromUrl,
  } = useWorkerTabs()

  // Refs for URL sync guard and resize
  const urlSyncRef = useRef(false)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const workerRefs = useRef<Map<string, WorkerDetailHandle>>(new Map())

  // --- URL sync: incoming (URL → tab state) ---
  // Read right pane from query param (window.location, NOT useSearchParams, to avoid
  // stale React Router state — outgoing sync uses replaceState which bypasses Router).
  useEffect(() => {
    if (!urlWorkerId) return
    if (urlSyncRef.current) {
      urlSyncRef.current = false
      return
    }
    const params = new URLSearchParams(window.location.search)
    const rightId = params.get('right')
    if (rightId && rightId !== urlWorkerId) {
      restoreFromUrl(urlWorkerId, rightId)
    } else {
      openTab(urlWorkerId)
    }
  }, [urlWorkerId]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- URL sync: outgoing (tab state → URL) ---
  // Use history.replaceState directly to avoid React Router re-render cycle.
  // Left pane always goes in the path; right pane in ?right= when split is on.
  // Incoming sync reads from window.location (not useSearchParams) to stay in sync.
  useEffect(() => {
    if (tabs.length === 0) return
    if (!leftActiveId) return
    let targetUrl = `/workers/${leftActiveId}`
    if (isSplit && rightActiveId) {
      targetUrl += `?right=${rightActiveId}`
    }
    const currentUrl = window.location.pathname + window.location.search
    if (targetUrl !== currentUrl) {
      urlSyncRef.current = true
      window.history.replaceState(null, '', targetUrl)
    }
  }, [leftActiveId, rightActiveId, isSplit]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Navigate to /workers when all tabs are closed ---
  // Debounce: wait 300ms before redirecting, in case a new tab is about to open.
  // The timer is cleared if tabs.length changes (new tab added before timeout).
  const tabsLengthRef = useRef(tabs.length)
  tabsLengthRef.current = tabs.length
  const redirectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (redirectTimerRef.current) {
      clearTimeout(redirectTimerRef.current)
      redirectTimerRef.current = null
    }
    if (tabs.length === 0) {
      redirectTimerRef.current = setTimeout(() => {
        if (tabsLengthRef.current === 0) {
          navigate('/workers', { replace: true })
        }
      }, 300)
    }
    return () => {
      if (redirectTimerRef.current) clearTimeout(redirectTimerRef.current)
    }
  }, [tabs.length, navigate])

  // --- Auto-close tabs for deleted workers (silently) ---
  useEffect(() => {
    const sessionIds = new Set(sessions.map(s => s.id))
    for (const tab of tabs) {
      if (!sessionIds.has(tab.workerId)) {
        closeTab(tab.workerId)
      }
    }
  }, [sessions]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Split animation: freeze terminal resize during transition, refit after ---
  const prevSplitRef = useRef(isSplit)
  useEffect(() => {
    if (prevSplitRef.current === isSplit) return
    prevSplitRef.current = isSplit
    const container = workspaceRef.current?.querySelector('.ww-pane-container')
    if (!container) return
    container.classList.add('ww-animating')
    const timer = setTimeout(() => {
      container.classList.remove('ww-animating')
      // Refit all visible terminals after animation settles
      workerRefs.current.forEach(handle => handle.refitTerminal())
    }, 280) // slightly longer than 250ms transition
    return () => { clearTimeout(timer); container.classList.remove('ww-animating') }
  }, [isSplit])

  // --- Brain panel auto-collapse on split + one-time tip ---
  useEffect(() => {
    if (!isSplit) return
    notify('Swap right pane:\n• Right-click a tab\n• ⌥+click a tab', 'info')
    const workspace = workspaceRef.current
    if (!workspace) return
    const availableWidth = workspace.getBoundingClientRect().width
    if (availableWidth < MIN_PANE_WIDTH * 2 && !brainPanel.collapsed) {
      brainPanel.collapse()
      notify('Brain panel collapsed to fit split view', 'info')
    }
  }, [isSplit]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Window resize: auto-collapse split if too narrow ---
  // Skip the first 500ms after split opens to avoid racing with brain panel collapse animation.
  // Also guard against unmount: ResizeObserver can fire with width=0 during teardown.
  useEffect(() => {
    if (!isSplit) return
    const workspace = workspaceRef.current
    if (!workspace) return
    let armed = false
    let disposed = false
    const armTimer = setTimeout(() => { armed = true }, 500)
    const observer = new ResizeObserver(([entry]) => {
      if (armed && !disposed && entry.contentRect.width > 0 && entry.contentRect.width < MIN_PANE_WIDTH * 2) {
        exitSplit()
        notify('Split view closed — not enough space', 'info')
      }
    })
    observer.observe(workspace)
    return () => { disposed = true; clearTimeout(armTimer); observer.disconnect() }
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

  // --- Stable callbacks ---
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
  const renderPaneContent = (pane: 'left' | 'right') => {
    const mounted = pane === 'left' ? leftMounted : rightMounted
    const activeId = pane === 'left' ? leftActiveId : rightActiveId
    const isFocusedPane = focusedPane === pane

    return mounted.map(workerId => {
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
            onDelete={handleDelete}
          />
        </div>
      )
    })
  }

  return (
    <div className="worker-workspace" ref={workspaceRef}>
      <WorkerTabBar />
      <div className={`ww-pane-container${isSplit ? ' ww-pane-container--split' : ''}`}>
        <div
          className={`ww-pane ww-pane--left${focusedPane === 'left' ? ' ww-pane--focused' : ''}`}
          onClick={() => { if (isSplit) setFocusedPane('left') }}
        >
          {renderPaneContent('left')}
        </div>
        <div className="ww-split-divider" />
        <div
          className={`ww-pane ww-pane--right${focusedPane === 'right' ? ' ww-pane--focused' : ''}`}
          onClick={() => { if (isSplit) setFocusedPane('right') }}
        >
          {renderPaneContent('right')}
        </div>
      </div>
    </div>
  )
}
