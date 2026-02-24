import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '../api/client'
import { useNotify } from '../context/NotificationContext'
import { useApp } from '../context/AppContext'
import { useSmartPaste } from '../hooks/useSmartPaste'
import TerminalView from '../components/terminal/TerminalView'
import { IconPause, IconPlay, IconStop, IconRefresh, IconTrash, IconSync, IconBrain } from '../components/common/Icons'
import ConfirmPopover from '../components/common/ConfirmPopover'
import './SessionDetailPage.css'

interface TunnelInfo {
  remote_port: number
  pid: number
  host: string
}

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const notify = useNotify()
  
  // Use shared state from AppContext for session
  const { sessions, tasks: allTasks, refresh } = useApp()
  const session = sessions.find(s => s.id === id) || null
  const tasks = allTasks.filter(t => t.assigned_session_id === id)
  const isRdev = session?.host?.includes('/') ?? false
  
  const { readClipboard } = useSmartPaste()
  const terminalFocusRef = useRef<(() => void) | null>(null)

  // Tunnel state for rdev workers
  const [tunnels, setTunnels] = useState<Record<string, TunnelInfo>>({})
  const tunnelIntervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  // Local state for page-specific data
  const [error, setError] = useState('')
  const [actionPending, setActionPending] = useState(false)
  const [pasting, setPasting] = useState(false)

  // Record that user viewed this session
  useEffect(() => {
    if (id) {
      api(`/api/sessions/${id}/viewed`, { method: 'POST' }).catch(() => {})
    }
  }, [id])

  // Fetch tunnels for rdev workers
  useEffect(() => {
    if (!isRdev || !id) {
      setTunnels({})
      return
    }

    const targetSessionId = id

    async function fetchTunnels() {
      try {
        const data = await api<{ tunnels: Record<string, TunnelInfo> }>(
          `/api/sessions/${targetSessionId}/tunnels`
        )
        setTunnels(data.tunnels || {})
      } catch {
        // Silently ignore tunnel fetch errors
      }
    }

    fetchTunnels()
    tunnelIntervalRef.current = setInterval(fetchTunnels, 10000)

    return () => {
      clearInterval(tunnelIntervalRef.current)
    }
  }, [id, isRdev])

  async function handlePauseOrContinue() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      const endpoint = session?.status === 'paused' ? 'continue' : 'pause'
      await api(`/api/sessions/${id}/${endpoint}`, { method: 'POST' })
      refresh()
      notify(`Worker ${endpoint === 'pause' ? 'paused' : 'resumed'}`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : `Failed to ${session?.status === 'paused' ? 'continue' : 'pause'}`, 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleStop() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${id}/stop`, { method: 'POST' })
      refresh()
      notify(`Worker stopped and cleared`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to stop worker', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleReconnect() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${id}/reconnect`, { method: 'POST' })
      refresh()
      notify(`Reconnecting worker...`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to reconnect', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleToggleAutoReconnect() {
    if (!id) return
    try {
      const result = await api<{ ok: boolean; auto_reconnect: boolean }>(
        `/api/sessions/${id}/auto-reconnect`,
        { method: 'POST' }
      )
      refresh()
      notify(`Auto-reconnect ${result.auto_reconnect ? 'enabled' : 'disabled'}`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to toggle auto-reconnect', 'error')
    }
  }

  async function handleHealthCheck() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      const result = await api<{ alive: boolean; status: string; reason: string }>(
        `/api/sessions/${id}/health-check`,
        { method: 'POST' }
      )
      await refresh()  // Wait for data to refresh before showing notification
      if (result.alive) {
        notify(`Worker is alive: ${result.reason}`, 'success')
      } else {
        notify(`Worker disconnected: ${result.reason}`, 'warning')
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to check status', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleDelete() {
    if (!id) return
    try {
      await api(`/api/sessions/${id}`, { method: 'DELETE' })
      refresh()
      navigate('/')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to delete', 'error')
    }
  }

  async function handleCheckProgress() {
    if (!id || actionPending) return
    // Validate ID is a UUID, not 'auto' or other keywords
    if (!/^[0-9a-f-]{36}$/i.test(id)) {
      notify(`Invalid worker ID: ${id}`, 'error')
      return
    }
    setActionPending(true)
    try {
      // Get brain session ID first
      const brainStatus = await api<{ session_id: string | null; running: boolean }>('/api/brain/status')
      if (!brainStatus.running || !brainStatus.session_id) {
        notify('Brain is not running. Start the brain first.', 'error')
        return
      }
      // Cancel any existing input and clear the line
      // Ctrl-C to cancel, then Ctrl-U to clear line buffer
      await api(`/api/sessions/${brainStatus.session_id}/send`, {
        method: 'POST',
        body: JSON.stringify({ message: '\x03' }),  // Ctrl-C
      })
      await new Promise(resolve => setTimeout(resolve, 50))
      await api(`/api/sessions/${brainStatus.session_id}/send`, {
        method: 'POST',
        body: JSON.stringify({ message: '\x15' }),  // Ctrl-U to clear line
      })
      await new Promise(resolve => setTimeout(resolve, 50))
      // Send check_worker command to brain for this specific worker
      const message = `/check_worker ${id}`
      await api(`/api/sessions/${brainStatus.session_id}/send`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to check progress', 'error')
    } finally {
      setActionPending(false)
    }
  }

  // Handle long text paste from Cmd+V in terminal (no permission popup)
  const handleTextPaste = useCallback(async (text: string) => {
    if (!id) return
    try {
      const res = await api<{ ok: boolean; file_path: string; filename: string }>(
        `/api/sessions/${id}/paste-text`,
        { method: 'POST', body: JSON.stringify({ text }) },
      )
      if (res.ok) {
        await api(`/api/sessions/${id}/type`, {
          method: 'POST',
          body: JSON.stringify({ text: res.file_path }),
        })
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to paste text', 'error')
    }
  }, [id, notify])

  // Handle image paste from Cmd+V in terminal (no permission popup)
  const handleImagePaste = useCallback(async (file: File) => {
    if (!id) return
    const reader = new FileReader()
    reader.onload = async () => {
      try {
        const base64 = (reader.result as string).split(',')[1]
        const res = await api<{ ok: boolean; file_path: string; filename: string }>(
          `/api/sessions/${id}/paste-image`,
          { method: 'POST', body: JSON.stringify({ image_data: base64 }) },
        )
        if (res.ok) {
          await api(`/api/sessions/${id}/type`, {
            method: 'POST',
            body: JSON.stringify({ text: res.file_path }),
          })
        }
      } catch (e) {
        notify(e instanceof Error ? e.message : 'Failed to paste image', 'error')
      }
    }
    reader.readAsDataURL(file)
  }, [id, notify])

  const handlePaste = useCallback(async () => {
    if (!id || pasting) return
    setPasting(true)
    try {
      const result = await readClipboard()
      if (result.type === 'image') {
        const res = await api<{ ok: boolean; file_path: string; filename: string }>(
          `/api/sessions/${id}/paste-image`,
          { method: 'POST', body: JSON.stringify({ image_data: result.imageData }) },
        )
        if (res.ok) {
          await api(`/api/sessions/${id}/type`, {
            method: 'POST',
            body: JSON.stringify({ text: res.file_path }),
          })
        }
      } else if (result.text && result.text.length > 1000) {
        // Long text: save to file and inject path
        const res = await api<{ ok: boolean; file_path: string; filename: string }>(
          `/api/sessions/${id}/paste-text`,
          { method: 'POST', body: JSON.stringify({ text: result.text }) },
        )
        if (res.ok) {
          await api(`/api/sessions/${id}/type`, {
            method: 'POST',
            body: JSON.stringify({ text: res.file_path }),
          })
        }
      } else {
        await api(`/api/sessions/${id}/send`, {
          method: 'POST',
          body: JSON.stringify({ message: result.text }),
        })
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'NotAllowedError') {
        notify('Clipboard access denied. Please allow clipboard permissions.', 'error')
      } else {
        notify(e instanceof Error ? e.message : 'Failed to paste', 'error')
      }
    } finally {
      setPasting(false)
      terminalFocusRef.current?.()
    }
  }, [id, pasting, readClipboard, notify])

  if (error) {
    return (
      <div className="error-page">
        <p>{error}</p>
        <button className="btn btn-secondary" onClick={() => navigate('/')}>Back to Dashboard</button>
      </div>
    )
  }

  if (!session) {
    return <p className="empty-state">Loading session...</p>
  }

  return (
    <div className="session-detail">
      {/* Top bar with session info */}
      <div className="sd-topbar">
        <div className="sd-topbar-left">
          <h2 className="sd-title">{session.name}</h2>
          {session.host.includes('/') && <span className="sd-type-tag rdev">rdev</span>}
          <span className={`status-badge ${session.status}`}>{session.status}</span>
          {/* Check Status button next to status */}
          <button
            className="sd-check-btn"
            onClick={handleHealthCheck}
            disabled={actionPending}
            title="Check if worker is alive"
          >
            <IconSync size={14} />
          </button>
        </div>
        <div className="sd-topbar-actions">
          {/* Control buttons - icon only */}
          {(session.status === 'disconnected' || session.status === 'screen_detached' || session.status === 'error') ? (
            /* Reconnect button for disconnected/screen_detached/error workers */
            <button
              className="sd-control-btn reconnect"
              onClick={handleReconnect}
              disabled={actionPending}
              title="Reconnect"
            >
              <IconRefresh size={16} />
            </button>
          ) : (
            <>
              {/* Check Progress button - always visible */}
              <button
                className="sd-control-btn check-progress"
                onClick={handleCheckProgress}
                disabled={actionPending || session.status === 'idle'}
                title="Check Progress"
              >
                <IconBrain size={16} />
              </button>
              <button
                className={`sd-control-btn ${session.status === 'paused' ? 'continue' : 'pause'}`}
                onClick={handlePauseOrContinue}
                disabled={actionPending || session.status === 'idle'}
                title={session.status === 'paused' ? 'Continue' : 'Pause'}
              >
                {session.status === 'paused' ? <IconPlay size={16} /> : <IconPause size={16} />}
              </button>
              <ConfirmPopover
                message={`Stop worker "${session.name}" and clear context?`}
                confirmLabel="Stop"
                onConfirm={handleStop}
                variant="danger"
              >
                {({ onClick }) => (
                  <button
                    className="sd-control-btn stop"
                    onClick={onClick}
                    disabled={actionPending || session.status === 'idle'}
                    title="Stop and clear"
                  >
                    <IconStop size={16} />
                  </button>
                )}
              </ConfirmPopover>
            </>
          )}

          {/* Remove button */}
          <ConfirmPopover
            message={`Remove worker "${session.name}"?`}
            confirmLabel="Remove"
            onConfirm={handleDelete}
            variant="danger"
          >
            {({ onClick }) => (
              <button
                className="sd-control-btn remove"
                data-testid="delete-session-btn"
                onClick={onClick}
                disabled={actionPending}
                title="Remove worker"
              >
                <IconTrash size={16} />
              </button>
            )}
          </ConfirmPopover>
        </div>
      </div>

      {/* Screen copy mode hint for rdev sessions - only show when Claude is running fine */}
      {isRdev && ['idle', 'working', 'waiting', 'paused'].includes(session.status) && (
        <div className="sd-rdev-hint">
          <span className="sd-rdev-hint-icon">💡</span>
          <span>Rdev claude code runs in screen. To view history: <kbd>Ctrl-A</kbd> + <kbd>[</kbd> to enter copy mode, <kbd>PageUp</kbd>/<kbd>PageDown</kbd> to scroll, <kbd>Esc</kbd> to exit</span>
        </div>
      )}

      {/* Terminal fills the rest */}
      <div className="sd-terminal-area">
        <TerminalView sessionId={session.id} sessionStatus={session.status} disableScrollback={isRdev} onFocusRef={(fn) => { terminalFocusRef.current = fn }} onImagePaste={handleImagePaste} onTextPaste={handleTextPaste} />
      </div>

      {/* Footer with task link */}
      <div className="sd-footer">
        <div className="sd-footer-left">
          {tasks.length > 0 ? (
            <Link
              to={`/tasks/${tasks[0].id}`}
              className="sd-task-badge"
              title={tasks[0].title}
            >
              <span className="sd-task-key">{tasks[0].task_key}</span>
              <span className="sd-task-title">{tasks[0].title}</span>
            </Link>
          ) : (
            <span className="sd-task-empty">No task assigned</span>
          )}
          {Object.keys(tunnels).length > 0 && (
            <div className="sd-tunnels">
              {Object.entries(tunnels).map(([port]) => (
                <a
                  key={port}
                  href={`http://localhost:${port}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="sd-tunnel-badge"
                  title={`Port forwarding: localhost:${port} → rdev:${port}`}
                >
                  :{port}
                </a>
              ))}
            </div>
          )}
        </div>
        <div className="sd-footer-right">
          {/* Auto-reconnect toggle — a worker setting, not an action */}
          <label className="sd-auto-reconnect-toggle" title="When enabled, automatically reconnect this worker if it disconnects">
            <span className="sd-auto-reconnect-label">Auto-reconnect</span>
            <button
              className={`sd-toggle-switch ${session.auto_reconnect ? 'on' : ''}`}
              onClick={handleToggleAutoReconnect}
              role="switch"
              aria-checked={session.auto_reconnect}
            >
              <span className="sd-toggle-knob" />
            </button>
          </label>
          <span className="sd-footer-divider" />
          <button
            className="sd-paste-btn"
            onClick={handlePaste}
            disabled={pasting}
            title={pasting ? 'Pasting...' : 'Paste clipboard to terminal'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
              <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
            </svg>
            <span>{pasting ? 'Pasting...' : 'Paste'}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
