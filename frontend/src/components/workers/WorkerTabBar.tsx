import { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react'
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
  const { workers, taskBySession } = useApp()
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
        {filtered.map(w => {
          const task = taskBySession.get(w.id)
          return (
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
              {task && (
                <span className="wt-picker-task">
                  {task.task_key && <span className="wt-picker-task-key">{task.task_key}</span>}
                  <span className="wt-picker-task-title">{task.title}</span>
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function WorkerTabBar() {
  const {
    tabs, leftActiveId, rightActiveId, isSplit, focusedPane,
    activateTab, closeTab, openTab, toggleSplit, enterSplit, setFocusedPane, moveTab, swapPanes,
  } = useWorkerTabs()
  const { sessions } = useApp()

  const [showPicker, setShowPicker] = useState(false)
  const [scrollFade, setScrollFade] = useState<'none' | 'left' | 'right' | 'both'>('none')
  const [activeHidden, setActiveHidden] = useState<{ side: 'none' | 'left' | 'right'; opacity: number }>({ side: 'none', opacity: 0 })
  const leftActiveIdRef = useRef(leftActiveId)
  leftActiveIdRef.current = leftActiveId
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<'left' | 'right' | null>(null)
  const tabBarRef = useRef<HTMLDivElement>(null)
  const rightGroupRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{
    workerId: string
    startX: number
    fromIndex: number
    currentToIndex: number
    isDragging: boolean
    tabEls: HTMLElement[]
    tabRects: { id: string; left: number; width: number; center: number }[]
  } | null>(null)

  // --- FLIP animation for tabs moving between groups ---
  const flipRectsRef = useRef<Map<string, DOMRect>>(new Map())
  const flipColorsRef = useRef<Map<string, string>>(new Map())
  const barRef = useRef<HTMLDivElement>(null)

  // Snapshot tab positions + border colors before React commits DOM changes
  const snapshotTabPositions = useCallback(() => {
    const bar = barRef.current
    if (!bar) return
    const rects = new Map<string, DOMRect>()
    const colors = new Map<string, string>()
    bar.querySelectorAll<HTMLElement>('[data-worker-id]').forEach(el => {
      const id = el.dataset.workerId!
      rects.set(id, el.getBoundingClientRect())
      colors.set(id, getComputedStyle(el).borderTopColor)
    })
    flipRectsRef.current = rects
    flipColorsRef.current = colors
  }, [])

  // After render, animate tabs that moved (position + border color cross-fade)
  useLayoutEffect(() => {
    const bar = barRef.current
    const oldRects = flipRectsRef.current
    if (!bar || oldRects.size === 0) return

    bar.querySelectorAll<HTMLElement>('[data-worker-id]').forEach(el => {
      const id = el.dataset.workerId!
      const oldRect = oldRects.get(id)
      if (!oldRect) return
      const newRect = el.getBoundingClientRect()
      const dx = oldRect.left - newRect.left
      if (Math.abs(dx) < 2) return

      // Set old border color inline so it can transition to the new CSS-defined color
      const oldColor = flipColorsRef.current.get(id)
      if (oldColor) el.style.borderTopColor = oldColor

      el.style.transform = `translateX(${dx}px)`
      el.style.transition = 'none'
      // Force reflow then animate to final position
      el.offsetHeight // eslint-disable-line @typescript-eslint/no-unused-expressions
      el.style.transition = 'transform 250ms ease, border-top-color 250ms ease'
      el.style.transform = ''
      el.style.borderTopColor = '' // reverts to CSS → triggers color transition
      el.addEventListener('transitionend', () => {
        el.style.transition = ''
        el.style.borderTopColor = ''
      }, { once: true })
    })
    flipRectsRef.current = new Map()
    flipColorsRef.current = new Map()
  }, [leftActiveId, rightActiveId])

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

    // Check if active tab is scrolled out of view
    // Use ref to always read latest leftActiveId (avoids stale closure from scroll listener)
    const activeId = leftActiveIdRef.current
    if (!activeId) { setActiveHidden({ side: 'none', opacity: 0 }); return }
    const activeTab = el.querySelector(`[data-worker-id="${activeId}"]`) as HTMLElement
    if (!activeTab) { setActiveHidden({ side: 'none', opacity: 0 }); return }
    const wrapperEl = el.parentElement
    if (!wrapperEl) { setActiveHidden({ side: 'none', opacity: 0 }); return }
    const wrapperRect = wrapperEl.getBoundingClientRect()
    const tabRect = activeTab.getBoundingClientRect()
    const tabWidth = tabRect.width || 1
    // How much of the tab is clipped on each side (0 = fully visible, 1 = fully hidden)
    const clippedLeft = Math.max(0, wrapperRect.left - tabRect.left) / tabWidth
    const clippedRight = Math.max(0, tabRect.right - wrapperRect.right) / tabWidth
    if (clippedLeft > 0.1) {
      setActiveHidden({ side: 'left', opacity: Math.min(1, clippedLeft) })
    } else if (clippedRight > 0.1) {
      setActiveHidden({ side: 'right', opacity: Math.min(1, clippedRight) })
    } else {
      setActiveHidden({ side: 'none', opacity: 0 })
    }
  }, [])

  // Update fade on mount and when tabs change; auto-scroll to show new tabs
  const prevTabCount = useRef(tabs.length)
  useEffect(() => {
    if (tabs.length > prevTabCount.current) {
      const el = tabBarRef.current
      if (el) {
        requestAnimationFrame(() => {
          el.scrollTo({ left: el.scrollWidth, behavior: 'smooth' })
        })
      }
    }
    prevTabCount.current = tabs.length
    updateScrollFade()
  }, [tabs.length, updateScrollFade])

  // Re-check when active tab changes. Defer the check so FLIP animations
  // (which use translateX) have settled — during animation, getBoundingClientRect
  // returns intermediate positions that make visible tabs look clipped.
  useEffect(() => {
    // Immediately clear stale hint (avoids flash of wrong indicator during FLIP)
    setActiveHidden({ side: 'none', opacity: 0 })
    const timer = setTimeout(updateScrollFade, 300)
    return () => clearTimeout(timer)
  }, [leftActiveId, updateScrollFade])

  // Also update on native scroll (e.g. trackpad horizontal gesture)
  useEffect(() => {
    const el = tabBarRef.current
    if (!el) return
    el.addEventListener('scroll', updateScrollFade, { passive: true })
    return () => el.removeEventListener('scroll', updateScrollFade)
  }, [updateScrollFade])

  // Toggle fade mask on tab names only when text is actually clipped
  const updateTabNameFades = useCallback(() => {
    const bar = barRef.current
    if (!bar) return
    bar.querySelectorAll<HTMLElement>('.wt-tab-name').forEach(el => {
      el.classList.toggle('wt-tab-name--faded', el.scrollWidth > el.clientWidth + 1)
    })
  }, [])

  useEffect(() => {
    updateTabNameFades()
  }, [tabs.length, isSplit, updateTabNameFades])

  // Dynamic tab max-width: share space evenly when crowded, up to 240px
  const updateTabMaxWidth = useCallback(() => {
    const el = tabBarRef.current
    if (!el) return
    const tabCount = el.querySelectorAll('[role="tab"]').length
    if (tabCount === 0) return
    const available = el.clientWidth
    const maxPerTab = Math.floor(available / tabCount)
    // Clamp between 120px and 240px
    const clamped = Math.max(120, Math.min(240, maxPerTab))
    el.style.setProperty('--wt-tab-max-width', `${clamped}px`)
  }, [])

  useEffect(() => {
    updateTabMaxWidth()
  }, [tabs.length, isSplit, updateTabMaxWidth])

  useEffect(() => {
    const bar = barRef.current
    if (!bar) return
    const observer = new ResizeObserver(() => {
      updateTabNameFades()
      updateTabMaxWidth()
    })
    observer.observe(bar)
    return () => observer.disconnect()
  }, [updateTabNameFades, updateTabMaxWidth])

  const sessionMap = new Map(sessions.map(s => [s.id, s]))
  const tabbedIds = new Set(tabs.map(t => t.workerId))

  const handleTabClick = useCallback((e: React.MouseEvent, workerId: string) => {
    // Normal click already handled by mousedown — only handle modifier clicks here
    // ⌥+click always opens in right pane (enters split if needed)
    if (e.altKey) {
      if (workerId === leftActiveId) {
        // ⌥+click on left active tab in split mode → swap panes
        if (isSplit) {
          snapshotTabPositions()
          swapPanes()
        }
        return
      }
      snapshotTabPositions()
      if (isSplit) {
        activateTab(workerId, 'right')
        setFocusedPane('right')
      } else {
        enterSplit(workerId)
      }
    }
  }, [isSplit, leftActiveId, activateTab, enterSplit, setFocusedPane, swapPanes, snapshotTabPositions])

  // Click on tab in the right group: activate it in the right pane + focus right
  const handleRightTabClick = useCallback((e: React.MouseEvent, workerId: string) => {
    snapshotTabPositions()
    if (e.altKey) {
      activateTab(workerId, 'left')
      return
    }
    activateTab(workerId, 'right')
    setFocusedPane('right')
  }, [activateTab, setFocusedPane, snapshotTabPositions])

  const handleTabAuxClick = useCallback((e: React.MouseEvent, workerId: string) => {
    if (e.button === 1) {
      e.preventDefault()
      closeTab(workerId)
    }
  }, [closeTab])

  const handlePickerSelect = useCallback((workerId: string) => {
    openTab(workerId)
  }, [openTab])

  // --- Right-click tab → open in right pane (or swap if it's the left active tab) ---
  const handleTabRightClick = useCallback((e: React.MouseEvent, workerId: string) => {
    e.preventDefault()
    e.stopPropagation()
    // Right-click the left active tab in split mode → swap left and right panes
    if (workerId === leftActiveId) {
      if (isSplit) {
        snapshotTabPositions()
        swapPanes()
      }
      return
    }
    snapshotTabPositions()
    if (isSplit) {
      activateTab(workerId, 'right')
      setFocusedPane('right')
    } else {
      enterSplit(workerId)
    }
  }, [isSplit, leftActiveId, activateTab, setFocusedPane, enterSplit, swapPanes, snapshotTabPositions])

  // --- Split mode: separate left tabs from right-active tab ---
  const leftTabs = isSplit
    ? tabs.filter(t => t.workerId !== rightActiveId)
    : tabs
  const rightTab = isSplit
    ? tabs.find(t => t.workerId === rightActiveId)
    : null

  // --- Drag-to-reorder + cross-pane drop ---
  const handleTabMouseDown = useCallback((e: React.MouseEvent, workerId: string) => {
    if (e.button !== 0 || e.altKey || e.ctrlKey || e.metaKey) return

    // Left click always opens in left pane
    if (isSplit) snapshotTabPositions()
    activateTab(workerId, 'left')
    if (isSplit) setFocusedPane('left')

    const scrollEl = tabBarRef.current
    if (!scrollEl) return

    const tabEls = Array.from(scrollEl.querySelectorAll<HTMLElement>('[role="tab"]'))
    // Use leftTabs for index mapping — the scroll container only has left group tabs
    const localIndex = leftTabs.findIndex(t => t.workerId === workerId)
    if (localIndex === -1) return

    const containerLeft = scrollEl.getBoundingClientRect().left
    const tabRects = tabEls.map((el, i) => {
      const r = el.getBoundingClientRect()
      const sl = scrollEl.scrollLeft
      return { id: leftTabs[i]?.workerId ?? '', left: r.left - containerLeft + sl, width: r.width, center: r.left - containerLeft + sl + r.width / 2 }
    })

    dragRef.current = { workerId, startX: e.clientX, fromIndex: localIndex, currentToIndex: localIndex, isDragging: false, tabEls, tabRects }

    const onMove = (ev: MouseEvent) => {
      const drag = dragRef.current
      if (!drag) return
      const delta = ev.clientX - drag.startX
      if (!drag.isDragging && Math.abs(delta) < 4) return
      if (!drag.isDragging) {
        drag.isDragging = true
        setDraggingId(workerId)
      }

      const draggedEl = drag.tabEls[drag.fromIndex]
      if (draggedEl) draggedEl.style.transform = `translateX(${delta}px)`

      // Check if dragging over the right group area (cross-pane drop)
      if (isSplit && rightGroupRef.current) {
        const rightRect = rightGroupRef.current.getBoundingClientRect()
        if (ev.clientX >= rightRect.left && ev.clientX <= rightRect.right) {
          setDropTarget('right')
          // Clear shift transforms when over drop zone
          for (let i = 0; i < drag.tabEls.length; i++) {
            if (i !== drag.fromIndex) drag.tabEls[i].style.transform = ''
          }
          return
        } else {
          setDropTarget(null)
        }
      }

      // Reorder within left group
      const cursorInContainer = ev.clientX - containerLeft + scrollEl.scrollLeft
      let toIndex = drag.fromIndex
      for (let i = 0; i < drag.tabRects.length; i++) {
        if (i === drag.fromIndex) continue
        const r = drag.tabRects[i]
        if (i < drag.fromIndex && cursorInContainer < r.center) { toIndex = i; break }
        if (i > drag.fromIndex && cursorInContainer > r.center) { toIndex = i }
      }
      drag.currentToIndex = toIndex

      const draggedWidth = drag.tabRects[drag.fromIndex].width
      for (let i = 0; i < drag.tabEls.length; i++) {
        if (i === drag.fromIndex) continue
        const el = drag.tabEls[i]
        if (drag.fromIndex < toIndex && i > drag.fromIndex && i <= toIndex) {
          el.style.transform = `translateX(${-draggedWidth}px)`
        } else if (drag.fromIndex > toIndex && i >= toIndex && i < drag.fromIndex) {
          el.style.transform = `translateX(${draggedWidth}px)`
        } else {
          el.style.transform = ''
        }
      }

      // Auto-scroll near edges
      const edgeZone = 40
      const containerRect = scrollEl.getBoundingClientRect()
      if (ev.clientX < containerRect.left + edgeZone) {
        scrollEl.scrollLeft -= 8
      } else if (ev.clientX > containerRect.right - edgeZone) {
        scrollEl.scrollLeft += 8
      }
    }

    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      const drag = dragRef.current
      if (!drag) return

      for (const el of drag.tabEls) el.style.transform = ''

      if (drag.isDragging) {
        if (dropTarget === 'right' && isSplit) {
          if (workerId !== rightActiveId) {
            activateTab(workerId, 'right')
            setFocusedPane('right')
          }
        } else if (drag.fromIndex !== drag.currentToIndex) {
          // Map local leftTabs indices back to full tabs array indices
          const fromId = leftTabs[drag.fromIndex]?.workerId
          const toId = leftTabs[drag.currentToIndex]?.workerId
          const fromFull = tabs.findIndex(t => t.workerId === fromId)
          const toFull = tabs.findIndex(t => t.workerId === toId)
          if (fromFull !== -1 && toFull !== -1) {
            moveTab(fromFull, toFull)
          }
        }
        setDraggingId(null)
        setDropTarget(null)
      }
      dragRef.current = null
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [tabs, leftTabs, moveTab, isSplit, focusedPane, rightActiveId, activateTab, setFocusedPane, dropTarget, snapshotTabPositions])

  // Drag from right group back to left
  const handleRightTabMouseDown = useCallback((e: React.MouseEvent, workerId: string) => {
    if (e.button !== 0 || e.altKey || e.ctrlKey || e.metaKey) return
    const startX = e.clientX
    let isDragging = false

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX
      if (!isDragging && Math.abs(delta) < 4) return
      if (!isDragging) {
        isDragging = true
        setDraggingId(workerId)
      }

      // Check if dragging over the left group area
      if (tabBarRef.current) {
        const leftRect = tabBarRef.current.getBoundingClientRect()
        if (ev.clientX >= leftRect.left && ev.clientX <= leftRect.right) {
          setDropTarget('left')
        } else {
          setDropTarget(null)
        }
      }
    }

    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      if (isDragging) {
        if (dropTarget === 'left') {
          // Drop onto left pane — activate this tab there
          if (workerId !== leftActiveId) {
            activateTab(workerId, 'left')
            setFocusedPane('left')
          }
        }
        setDraggingId(null)
        setDropTarget(null)
      }
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [leftActiveId, activateTab, setFocusedPane, dropTarget])

  // --- Render a single tab element ---
  const renderTab = (
    workerId: string,
    isActive: boolean,
    paneClass: string,
    onClick: (e: React.MouseEvent, id: string) => void,
    onMouseDown?: (e: React.MouseEvent, id: string) => void,
  ) => {
    const session = sessionMap.get(workerId)
    if (!session) return null

    return (
      <button
        key={workerId}
        data-worker-id={workerId}
        className={`wt-tab ${isActive ? `wt-tab--active ${paneClass}` : ''} ${draggingId === workerId ? 'wt-tab--dragging' : ''}`}
        role="tab"
        aria-selected={isActive}
        onClick={e => { if (!draggingId) onClick(e, workerId) }}
        onMouseDown={onMouseDown ? e => onMouseDown(e, workerId) : undefined}
        onAuxClick={e => handleTabAuxClick(e, workerId)}
        onContextMenu={e => handleTabRightClick(e, workerId)}
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
          onMouseDown={e => e.stopPropagation()}
          onClick={e => { e.stopPropagation(); closeTab(workerId) }}
          onAuxClick={e => e.stopPropagation()}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </span>
      </button>
    )
  }

  const controls = (
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
  )

  return (
    <div ref={barRef} className={`wt-bar ${draggingId ? 'wt-bar--dragging' : ''} ${isSplit ? `wt-bar--focus-${focusedPane}` : ''}`} role="tablist" aria-label="Worker tabs">
      {/* Left tab group — wrapper holds fixed-position fade overlays */}
      <div
        className={`wt-left-wrapper${activeHidden.side === 'left' ? ' wt-left-wrapper--active-left' : ''}${activeHidden.side === 'right' ? ' wt-left-wrapper--active-right' : ''}`}
        style={activeHidden.side !== 'none' ? { '--wt-hint-opacity': activeHidden.opacity } as React.CSSProperties : undefined}
      >
        <div
          className={`wt-tabs-scroll${dropTarget === 'left' ? ' wt-drop-target' : ''}`}
          ref={tabBarRef}
          onWheel={handleWheel}
        >
          {leftTabs.map(tab =>
            renderTab(
              tab.workerId,
              tab.workerId === leftActiveId,
              'wt-tab--active-left',
              handleTabClick,
              handleTabMouseDown,
            )
          )}
        </div>
      </div>

      {/* Split mode: divider + right group */}
      {isSplit && rightTab && (
        <>
          <div className="wt-divider" />
          <div
            className={`wt-right-group ${dropTarget === 'right' ? 'wt-drop-target' : ''}`}
            ref={rightGroupRef}
          >
            {renderTab(
              rightTab.workerId,
              true,
              'wt-tab--active-right',
              handleRightTabClick,
              handleRightTabMouseDown,
            )}
          </div>
        </>
      )}

      {controls}
    </div>
  )
}
