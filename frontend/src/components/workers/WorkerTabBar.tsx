import { useState, useRef, useEffect, useCallback } from 'react'
import { useWorkerTabs } from '../../context/WorkerTabsContext'
import { useApp } from '../../context/AppContext'
import { WORKER_STATUS_COLORS } from '../../utils/statusColors'
import './WorkerTabBar.css'

interface WorkerPickerProps {
  onClose: () => void
  onSelect: (workerId: string) => void
  excludeIds: Set<string>
}

function WorkerPicker({ onClose, onSelect, excludeIds }: WorkerPickerProps) {
  const { workers } = useApp()
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const pickerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  const available = workers.filter(w => !excludeIds.has(w.id))
  const filtered = query
    ? available.filter(w => w.name.toLowerCase().includes(query.toLowerCase()))
    : available

  return (
    <div className="wt-picker" ref={pickerRef}>
      <input
        ref={inputRef}
        type="text"
        className="wt-picker-input"
        placeholder="Search workers..."
        value={query}
        onChange={e => setQuery(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && filtered.length > 0) {
            onSelect(filtered[0].id)
            onClose()
          }
        }}
      />
      <div className="wt-picker-list">
        {filtered.length === 0 && (
          <div className="wt-picker-empty">No workers available</div>
        )}
        {filtered.map(w => (
          <button
            key={w.id}
            className="wt-picker-item"
            onClick={() => { onSelect(w.id); onClose() }}
          >
            <span
              className="wt-status-dot"
              style={{ background: WORKER_STATUS_COLORS[w.status as keyof typeof WORKER_STATUS_COLORS] || 'var(--text-muted)' }}
            />
            <span className="wt-picker-name">{w.name}</span>
            <span className={`wt-picker-status status-badge ${w.status}`}>{w.status}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

export default function WorkerTabBar() {
  const {
    tabs, leftActiveId, rightActiveId, isSplit, focusedPane,
    activateTab, closeTab, pinTab, openTab, toggleSplit, enterSplit, setFocusedPane,
  } = useWorkerTabs()
  const { sessions } = useApp()

  const [showPicker, setShowPicker] = useState(false)
  const [scrollFade, setScrollFade] = useState<'none' | 'left' | 'right' | 'both'>('none')
  const tabBarRef = useRef<HTMLDivElement>(null)

  // Convert vertical wheel to horizontal scroll + update fade indicators
  const handleWheel = useCallback((e: React.WheelEvent<HTMLDivElement>) => {
    const el = tabBarRef.current
    if (!el) return
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
      e.preventDefault()
      el.scrollLeft += e.deltaY
    }
    updateScrollFade()
  }, [])

  const updateScrollFade = useCallback(() => {
    const el = tabBarRef.current
    if (!el) return
    const hasLeft = el.scrollLeft > 2
    const hasRight = el.scrollLeft < el.scrollWidth - el.clientWidth - 2
    const fade = hasLeft && hasRight ? 'both' : hasLeft ? 'left' : hasRight ? 'right' : 'none'
    setScrollFade(fade)
  }, [])

  // Update fade on mount and when tabs change
  useEffect(() => {
    updateScrollFade()
  }, [tabs.length, updateScrollFade])

  // Also update on native scroll (e.g. trackpad horizontal gesture)
  useEffect(() => {
    const el = tabBarRef.current
    if (!el) return
    el.addEventListener('scroll', updateScrollFade, { passive: true })
    return () => el.removeEventListener('scroll', updateScrollFade)
  }, [updateScrollFade])

  const sessionMap = new Map(sessions.map(s => [s.id, s]))
  const tabbedIds = new Set(tabs.map(t => t.workerId))

  const handleTabClick = useCallback((e: React.MouseEvent, workerId: string) => {
    // Alt+click = activate in other pane
    if (e.altKey && isSplit) {
      const otherPane = focusedPane === 'left' ? 'right' : 'left'
      activateTab(workerId, otherPane)
      return
    }
    if (e.altKey && !isSplit) {
      // Enter split with this tab in the other pane
      enterSplit(workerId)
      return
    }
    activateTab(workerId)
  }, [isSplit, focusedPane, activateTab, enterSplit])

  const handleTabAuxClick = useCallback((e: React.MouseEvent, workerId: string) => {
    // Middle-click = close
    if (e.button === 1) {
      e.preventDefault()
      closeTab(workerId)
    }
  }, [closeTab])

  const handleTabDoubleClick = useCallback((workerId: string) => {
    pinTab(workerId)
  }, [pinTab])

  const handlePickerSelect = useCallback((workerId: string) => {
    openTab(workerId, true)  // Explicit picker = pinned
  }, [openTab])

  // Determine underline type for each tab
  const getUnderlineClass = (workerId: string): string => {
    const isLeft = workerId === leftActiveId
    const isRight = isSplit && workerId === rightActiveId
    if (isLeft && isRight) return 'wt-tab--active-both'
    if (isLeft) return 'wt-tab--active-left'
    if (isRight) return 'wt-tab--active-right'
    return ''
  }

  return (
    <div className="wt-bar" role="tablist" aria-label="Worker tabs">
      <div className={`wt-tabs-scroll wt-tabs-scroll--fade-${scrollFade}`} ref={tabBarRef} onWheel={handleWheel}>
        {tabs.map(tab => {
          const session = sessionMap.get(tab.workerId)
          if (!session) return null
          const isActive = tab.workerId === leftActiveId || (isSplit && tab.workerId === rightActiveId)
          const underlineClass = getUnderlineClass(tab.workerId)

          return (
            <button
              key={tab.workerId}
              className={`wt-tab ${underlineClass} ${isActive ? 'wt-tab--active' : ''} ${tab.isPreview ? 'wt-tab--preview' : ''}`}
              role="tab"
              aria-selected={isActive}
              onClick={e => handleTabClick(e, tab.workerId)}
              onAuxClick={e => handleTabAuxClick(e, tab.workerId)}
              onDoubleClick={() => handleTabDoubleClick(tab.workerId)}
            >
              <span
                className={`wt-status-dot ${session.status === 'working' ? 'wt-status-dot--pulse' : ''}`}
                style={{ background: WORKER_STATUS_COLORS[session.status as keyof typeof WORKER_STATUS_COLORS] || 'var(--text-muted)' }}
              />
              <span className="wt-tab-name">{session.name}</span>
              <span
                className="wt-tab-close"
                role="button"
                aria-label={`Close ${session.name}`}
                onClick={e => { e.stopPropagation(); closeTab(tab.workerId) }}
                onAuxClick={e => e.stopPropagation()}
              >
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </span>
            </button>
          )
        })}
      </div>
      <div className="wt-controls">
        <div className="wt-control-wrapper">
          <button
            className="wt-control-btn"
            onClick={() => setShowPicker(!showPicker)}
            aria-label="Open worker"
            title="Open a worker tab"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
          {showPicker && (
            <WorkerPicker
              onClose={() => setShowPicker(false)}
              onSelect={handlePickerSelect}
              excludeIds={tabbedIds}
            />
          )}
        </div>
        <button
          className={`wt-control-btn wt-split-btn ${isSplit ? 'wt-split-btn--active' : ''}`}
          onClick={toggleSplit}
          disabled={!isSplit && tabs.length < 2}
          aria-label={isSplit ? 'Exit split view' : 'Split view'}
          title={isSplit ? 'Exit split view' : tabs.length < 2 ? 'Open another tab to split' : 'Split view'}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <rect x="3" y="3" width="18" height="18" rx="2" /><line x1="12" y1="3" x2="12" y2="21" />
          </svg>
        </button>
      </div>
    </div>
  )
}
