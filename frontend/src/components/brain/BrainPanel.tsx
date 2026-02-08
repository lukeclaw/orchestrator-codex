import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import BrainTerminal from './BrainTerminal'
import type { BrainStatus } from './BrainTerminal'
import AutoSyncTimer from './AutoSyncTimer'
import { IconPanelRight, IconChevronRight } from '../common/Icons'
import './BrainPanel.css'

interface BrainPanelProps {
  collapsed: boolean
  onToggleCollapsed: () => void
  width: number
  onWidthChange: (w: number) => void
  minWidth: number
  maxWidth: number
}

export default function BrainPanel({
  collapsed,
  onToggleCollapsed,
  width,
  onWidthChange,
  minWidth,
  maxWidth,
}: BrainPanelProps) {
  const notify = useNotify()
  const [brainStatus, setBrainStatus] = useState<BrainStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const isDragging = useRef(false)

  // Poll brain status
  const fetchStatus = useCallback(async () => {
    try {
      const status = await api<BrainStatus>('/api/brain/status')
      setBrainStatus(status)
    } catch {
      setBrainStatus({ running: false, session_id: null, status: null })
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  async function handleStart() {
    setStarting(true)
    try {
      await api('/api/brain/start', { method: 'POST' })
      await fetchStatus()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to start brain', 'error')
    } finally {
      setStarting(false)
    }
  }

  async function handleStop() {
    setStopping(true)
    try {
      await api('/api/brain/stop', { method: 'POST' })
      await fetchStatus()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to stop brain', 'error')
    } finally {
      setStopping(false)
    }
  }

  // User interaction tracking for auto-sync pause
  const userInteractionRef = useRef<(() => void) | null>(null)
  function handleUserInput() {
    userInteractionRef.current?.()
  }

  // Drag resize
  function handleMouseDown(e: React.MouseEvent) {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = width
    isDragging.current = true

    function onMouseMove(ev: MouseEvent) {
      const delta = startX - ev.clientX
      onWidthChange(startWidth + delta)
    }

    function onMouseUp() {
      isDragging.current = false
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  const isRunning = brainStatus?.running && brainStatus?.session_id

  if (collapsed) {
    return (
      <div className="brain-panel-sidebar collapsed" data-testid="brain-sidebar">
        <button
          className="bp-toggle-btn"
          onClick={onToggleCollapsed}
          title="Expand brain panel"
        >
          <span className={`brain-indicator ${isRunning ? 'active' : 'inactive'}`} />
        </button>
      </div>
    )
  }

  return (
    <div
      className={`brain-panel-sidebar ${isDragging.current ? 'dragging' : ''}`}
      style={{ width }}
      data-testid="brain-sidebar"
    >
      <div className="bp-resize-handle" onMouseDown={handleMouseDown} />

      <div className="bp-header">
        <div className="bp-header-left">
          <span className={`brain-indicator ${isRunning ? 'active' : 'inactive'}`} />
          <span className="bp-title">Brain</span>
          {brainStatus?.status && (
            <span className={`status-badge ${brainStatus.status}`}>{brainStatus.status}</span>
          )}
        </div>
        <div className="bp-header-right">
          {isRunning ? (
            <button
              className="btn btn-danger btn-sm"
              onClick={handleStop}
              disabled={stopping}
            >
              {stopping ? 'Stopping...' : 'Stop'}
            </button>
          ) : (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleStart}
              disabled={starting}
            >
              {starting ? 'Starting...' : 'Start'}
            </button>
          )}
          <button
            className="bp-collapse-btn"
            onClick={onToggleCollapsed}
            title="Collapse brain panel"
          >
            <IconChevronRight size={14} />
          </button>
        </div>
      </div>

      <AutoSyncTimer
        brainSessionId={brainStatus?.session_id || null}
        brainStatus={brainStatus?.status || null}
        brainRunning={!!isRunning}
        userInteractionRef={userInteractionRef}
      />

      <div className="bp-content">
        <BrainTerminal
          brainStatus={brainStatus}
          starting={starting}
          stopping={stopping}
          onStart={handleStart}
          onStop={handleStop}
          onUserInput={handleUserInput}
        />
      </div>
    </div>
  )
}
