import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import { useSmartPaste } from '../../hooks/useSmartPaste'
import BrainTerminal from './BrainTerminal'
import type { BrainStatus } from './BrainTerminal'
import { IconChevronLeft, IconChevronRight, IconClipboard, IconStop } from '../common/Icons'
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
  const [pasting, setPasting] = useState(false)
  const [ctxPasting, setCtxPasting] = useState(false)
  const isDragging = useRef(false)
  const terminalInputRef = useRef<((text: string) => void) | null>(null)
  const terminalFocusRef = useRef<(() => void) | null>(null)
  const { readClipboard } = useSmartPaste()

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

  async function handlePaste() {
    if (pasting) return

    setPasting(true)
    try {
      const clip = await readClipboard()

      if (clip.type === 'image' && clip.imageData) {
        // Image: save to file via backend, then inject file path
        const result = await api<{ ok: boolean; file_path: string; filename: string }>(
          '/api/brain/paste-image',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_data: clip.imageData }),
          }
        )
        if (result.ok && result.file_path) {
          if (terminalInputRef.current) {
            terminalInputRef.current(result.file_path)
          }
        }
      } else if (clip.text && clip.text.length > 1000) {
        // Long text: save to file and inject path
        const result = await api<{ ok: boolean; file_path: string; filename: string }>(
          '/api/brain/paste-text',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: clip.text }),
          }
        )
        if (result.ok && result.file_path) {
          if (terminalInputRef.current) {
            terminalInputRef.current(result.file_path)
          }
        }
      } else if (clip.text) {
        // Short text: inject directly into terminal
        if (terminalInputRef.current) {
          terminalInputRef.current(clip.text)
        }
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'NotAllowedError') {
        notify('Clipboard access denied. Please allow clipboard permissions.', 'error')
      } else {
        notify(e instanceof Error ? e.message : 'Failed to paste from clipboard', 'error')
      }
    } finally {
      setPasting(false)
      terminalFocusRef.current?.()
    }
  }

  // Handle long text paste from Cmd+V in terminal
  const handleTextPaste = useCallback(async (text: string) => {
    try {
      const result = await api<{ ok: boolean; file_path: string; filename: string }>(
        '/api/brain/paste-text',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        }
      )
      if (result.ok && result.file_path) {
        if (terminalInputRef.current) {
          terminalInputRef.current(result.file_path)
        }
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to paste text', 'error')
    }
  }, [notify])

  // Handle image paste from Cmd+V in terminal
  const handleImagePaste = useCallback(async (file: File) => {
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const base64 = (reader.result as string).split(',')[1]
        const result = await api<{ ok: boolean; file_path: string; filename: string }>(
          '/api/brain/paste-image',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_data: base64 }),
          }
        )
        if (result.ok && result.file_path) {
          if (terminalInputRef.current) {
            terminalInputRef.current(result.file_path)
          }
        }
      } catch (e) {
        notify(e instanceof Error ? e.message : 'Failed to paste image', 'error')
      }
    }
    reader.readAsDataURL(file)
  }, [notify])

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
          {isRunning ? (
            <ConfirmPopover
              message="Stop the brain?"
              confirmLabel="Stop"
              onConfirm={handleStop}
              variant="danger"
            >
              {({ onClick }) => (
                <button
                  className="bp-stop-btn"
                  onClick={onClick}
                  disabled={stopping}
                  title="Stop brain"
                >
                  <IconStop size={14} />
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

      <div className="bp-content">
        <BrainTerminal
          brainStatus={brainStatus}
          starting={starting}
          stopping={stopping}
          onStart={handleStart}
          onStop={handleStop}
          onTerminalInputRef={(fn: (text: string) => void) => { terminalInputRef.current = fn }}
          onTerminalFocusRef={(fn: () => void) => { terminalFocusRef.current = fn }}
          onImagePaste={handleImagePaste}
          onTextPaste={handleTextPaste}
          onPastingChange={setCtxPasting}
        />
      </div>

      {isRunning && (
        <div className="bp-footer">
          <button
            className={`bp-footer-paste-btn${pasting || ctxPasting ? ' pasting' : ''}`}
            onClick={handlePaste}
            disabled={pasting || ctxPasting}
            title={pasting || ctxPasting ? 'Pasting...' : 'Paste from clipboard'}
          >
            <IconClipboard size={12} />
            <span>{pasting || ctxPasting ? 'Pasting...' : 'Paste'}</span>
          </button>
        </div>
      )}
    </div>
  )
}
