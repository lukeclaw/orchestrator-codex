import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import BrainTerminal from './BrainTerminal'
import type { BrainStatus } from './BrainTerminal'
import AutoSyncTimer from './AutoSyncTimer'
import { IconChevronLeft, IconChevronRight, IconImage } from '../common/Icons'
import ConfirmPopover from '../common/ConfirmPopover'
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
  const [pastingImage, setPastingImage] = useState(false)
  const isDragging = useRef(false)
  const terminalInputRef = useRef<((text: string) => void) | null>(null)

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

  async function handlePasteImage() {
    if (pastingImage) return
    
    setPastingImage(true)
    try {
      // Read image from clipboard
      const clipboardItems = await navigator.clipboard.read()
      let imageBlob: Blob | null = null
      
      for (const item of clipboardItems) {
        // Check for image types
        const imageType = item.types.find(type => type.startsWith('image/'))
        if (imageType) {
          imageBlob = await item.getType(imageType)
          break
        }
      }
      
      if (!imageBlob) {
        notify('No image found in clipboard', 'error')
        return
      }
      
      // Convert blob to base64
      const reader = new FileReader()
      const base64Promise = new Promise<string>((resolve, reject) => {
        reader.onload = () => resolve(reader.result as string)
        reader.onerror = reject
      })
      reader.readAsDataURL(imageBlob)
      const base64Data = await base64Promise
      
      // Send to backend
      const result = await api<{ ok: boolean; file_path: string; filename: string }>(
        '/api/brain/paste-image',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_data: base64Data }),
        }
      )
      
      if (result.ok && result.file_path) {
        // Inject the file path into the terminal input
        if (terminalInputRef.current) {
          terminalInputRef.current(result.file_path)
        }
        notify(`Image saved: ${result.filename}`, 'success')
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'NotAllowedError') {
        notify('Clipboard access denied. Please allow clipboard permissions.', 'error')
      } else {
        notify(e instanceof Error ? e.message : 'Failed to paste image', 'error')
      }
    } finally {
      setPastingImage(false)
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
          <IconChevronLeft size={16} />
        </button>
        <span className={`brain-indicator ${isRunning ? 'active' : 'inactive'}`} style={{ marginTop: 8 }} />
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
        <button
          className="bp-collapse-btn"
          onClick={onToggleCollapsed}
          title="Collapse brain panel"
        >
          <IconChevronRight size={14} />
        </button>
        <div className="bp-header-center">
          <span className={`brain-indicator ${isRunning ? 'active' : 'inactive'}`} />
          <span className="bp-title">Brain</span>
          {brainStatus?.status && (
            <span className={`status-badge ${brainStatus.status}`}>{brainStatus.status}</span>
          )}
        </div>
        <div className="bp-header-right">
          {isRunning && (
            <button
              className="bp-paste-image-btn"
              onClick={handlePasteImage}
              disabled={pastingImage}
              title="Paste image from clipboard"
            >
              <IconImage size={14} />
            </button>
          )}
          {isRunning ? (
            <ConfirmPopover
              message="Stop the brain?"
              confirmLabel="Stop"
              onConfirm={handleStop}
              variant="danger"
            >
              {({ onClick }) => (
                <button
                  className="btn btn-danger btn-sm"
                  onClick={onClick}
                  disabled={stopping}
                >
                  {stopping ? 'Stopping...' : 'Stop'}
                </button>
              )}
            </ConfirmPopover>
          ) : (
            <button
              className="btn btn-primary btn-sm"
              onClick={handleStart}
              disabled={starting}
            >
              {starting ? 'Starting...' : 'Start'}
            </button>
          )}
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
          onTerminalInputRef={(fn: (text: string) => void) => { terminalInputRef.current = fn }}
        />
      </div>
    </div>
  )
}
