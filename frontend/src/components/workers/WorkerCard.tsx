import { useState, useEffect, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import type { Session, Task } from '../../api/types'
import { api, openUrl } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import { timeAgo, parseDate } from '../common/TimeAgo'
import { IconTrash, IconPause, IconPlay, IconStop, IconRefresh, IconBrain, IconKebab } from '../common/Icons'
import ConfirmPopover from '../common/ConfirmPopover'
import './WorkerCard.css'

interface Props {
  session: Session
  assignedTask?: Task | null  // Task assigned to this worker
  onRemove?: (id: string) => void
}

interface TunnelInfo {
  remote_port: number
  pid: number
  host: string
}

function statusDuration(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  const d = parseDate(dateStr)
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 0) return '<1m'
  if (secs < 60) return '<1m'
  if (secs < 3600) return `${Math.floor(secs / 60)}m`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`
  return `${Math.floor(secs / 86400)}d`
}

export default function WorkerCard({
  session, assignedTask, onRemove,
}: Props) {
  const navigate = useNavigate()
  const notify = useNotify()
  const [removing, setRemoving] = useState(false)
  const [actionPending, setActionPending] = useState(false)
  const [showOverflow, setShowOverflow] = useState(false)
  const [tunnels, setTunnels] = useState<Record<string, TunnelInfo>>({})
  const tunnelIntervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const sessionIdRef = useRef(session.id)
  const overflowRef = useRef<HTMLDivElement>(null)

  // Check if this is an rdev worker
  const isRdev = session.host.includes('/')

  // Whether to always show action buttons (error states)
  const alwaysShowActions = session.status === 'error' || session.status === 'disconnected' || session.status === 'screen_detached'

  // Update ref when session changes (for interval callbacks to read current value)
  sessionIdRef.current = session.id

  // Fetch tunnels for rdev workers
  useEffect(() => {
    if (!isRdev) {
      setTunnels({})
      return
    }

    const targetSessionId = session.id

    async function fetchTunnels() {
      if (sessionIdRef.current !== targetSessionId) return

      try {
        const data = await api<{ tunnels: Record<string, TunnelInfo> }>(
          `/api/sessions/${targetSessionId}/tunnels`
        )
        if (sessionIdRef.current === targetSessionId) {
          setTunnels(data.tunnels || {})
        }
      } catch {
        // Silently ignore tunnel fetch errors
      }
    }

    fetchTunnels()
    tunnelIntervalRef.current = setInterval(fetchTunnels, 10000)

    return () => {
      clearInterval(tunnelIntervalRef.current)
    }
  }, [session.id, isRdev])

  // Close overflow menu on click outside
  useEffect(() => {
    if (!showOverflow) return

    function handleClickOutside(e: MouseEvent) {
      if (overflowRef.current && !overflowRef.current.contains(e.target as Node)) {
        setShowOverflow(false)
      }
    }

    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setShowOverflow(false)
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [showOverflow])

  async function handleRemove() {
    if (removing) return
    setRemoving(true)
    setShowOverflow(false)
    try {
      // Stop the worker first, then delete
      try {
        await api(`/api/sessions/${session.id}/stop`, { method: 'POST' })
      } catch {
        // may already be stopped
      }
      await api(`/api/sessions/${session.id}`, { method: 'DELETE' })
      notify(`Removed worker ${session.name}`, 'success')
      onRemove?.(session.id)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to remove worker', 'error')
      setRemoving(false)
    }
  }

  async function handlePauseOrContinue(e: React.MouseEvent) {
    e.stopPropagation()
    if (actionPending) return
    setActionPending(true)
    try {
      const isPaused = session.status === 'paused'
      const endpoint = isPaused ? 'continue' : 'pause'
      await api(`/api/sessions/${session.id}/${endpoint}`, { method: 'POST' })
      notify(`Worker ${session.name} ${isPaused ? 'continued' : 'paused'}`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Action failed', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleStop() {
    if (actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${session.id}/stop`, { method: 'POST' })
      notify(`Worker ${session.name} stopped and cleared`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to stop worker', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleReconnect(e: React.MouseEvent) {
    e.stopPropagation()
    if (actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${session.id}/reconnect`, { method: 'POST' })
      notify(`Reconnecting worker ${session.name}...`, 'info')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to reconnect', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleCheckProgress(e: React.MouseEvent) {
    e.stopPropagation()
    if (actionPending) return
    // Validate ID is a UUID, not 'auto' or other keywords
    if (!/^[0-9a-f-]{36}$/i.test(session.id)) {
      notify(`Invalid worker ID: ${session.id}`, 'error')
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
      const message = `/check_worker ${session.id}`
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

  // Take last ~20 lines for the preview (from session data, populated by AppContext)
  const previewLines = session.preview ? session.preview.split('\n').slice(-20).join('\n') : ''

  return (
    <div
      className={`worker-card ${session.status}${removing ? ' removing' : ''}${alwaysShowActions ? ' show-actions' : ''}`}
      data-testid="worker-card"
      data-session-id={session.id}
      onClick={() => navigate(`/workers/${session.id}`)}
    >
      <div className="wc-header">
        <div className="wc-header-left">
          <span className={`status-indicator ${session.status}`} />
          {isRdev && session.name.includes('_') ? (
            <span className="wc-name">
              <span className="wc-name-prefix">{session.name.slice(0, session.name.indexOf('_') + 1)}</span>
              {session.name.slice(session.name.indexOf('_') + 1)}
            </span>
          ) : (
            <span className="wc-name">{session.name}</span>
          )}
          {isRdev && <span className="wc-type-tag rdev">rdev</span>}
          <span className={`status-badge ${session.status}`}>{session.status}</span>
          {session.last_status_changed_at && (
            <span className="wc-duration">{statusDuration(session.last_status_changed_at)}</span>
          )}
        </div>
        <div className="wc-actions">
          {/* Hoverable action buttons */}
          <div className="wc-actions-hoverable">
            {(session.status === 'disconnected' || session.status === 'screen_detached' || session.status === 'error') ? (
              /* Reconnect button for disconnected/screen_detached/error workers */
              <button
                className="wc-action-btn reconnect"
                onClick={handleReconnect}
                disabled={actionPending}
                title="Reconnect"
              >
                <IconRefresh size={14} />
              </button>
            ) : (
              <>
                {/* Check Progress button for waiting workers, Pause/Continue for others */}
                {session.status === 'waiting' ? (
                  <button
                    className="wc-action-btn check-progress"
                    onClick={handleCheckProgress}
                    disabled={actionPending}
                    title="Check Progress"
                  >
                    <IconBrain size={14} />
                  </button>
                ) : (
                  <button
                    className={`wc-action-btn ${session.status === 'paused' ? 'continue' : 'pause'}`}
                    onClick={handlePauseOrContinue}
                    disabled={actionPending || session.status === 'idle'}
                    title={session.status === 'paused' ? 'Continue' : 'Pause'}
                  >
                    {session.status === 'paused' ? <IconPlay size={14} /> : <IconPause size={14} />}
                  </button>
                )}
              </>
            )}
          </div>

          {/* Kebab overflow menu — always visible */}
          <div className="wc-overflow-menu" ref={overflowRef}>
            <button
              className="wc-kebab-btn"
              onClick={e => { e.stopPropagation(); setShowOverflow(!showOverflow) }}
              title="More actions"
            >
              <IconKebab size={14} />
            </button>
            {showOverflow && (
              <div className="wc-overflow-dropdown">
                <ConfirmPopover
                  message={`Stop worker "${session.name}" and clear context?`}
                  confirmLabel="Stop"
                  onConfirm={handleStop}
                  variant="danger"
                >
                  {({ onClick }) => (
                    <button
                      className="wc-overflow-item"
                      onClick={onClick}
                      disabled={actionPending || session.status === 'idle'}
                    >
                      <IconStop size={13} />
                      <span>Stop & clear</span>
                    </button>
                  )}
                </ConfirmPopover>
                <ConfirmPopover
                  message={`Remove worker "${session.name}"?`}
                  confirmLabel="Remove"
                  onConfirm={handleRemove}
                  variant="danger"
                >
                  {({ onClick }) => (
                    <button
                      className="wc-overflow-item danger"
                      onClick={onClick}
                      disabled={removing}
                    >
                      <IconTrash size={13} />
                      <span>Remove</span>
                    </button>
                  )}
                </ConfirmPopover>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="wc-terminal-preview">
        <pre>{previewLines || 'No terminal output yet...'}</pre>
      </div>

      <div className="wc-footer">
        <div className="wc-footer-left">
          {assignedTask ? (
            <Link
              to={`/tasks/${assignedTask.id}`}
              className="wc-task-badge"
              onClick={e => e.stopPropagation()}
              title={assignedTask.title}
            >
              <span className="wc-task-key">{assignedTask.task_key}</span>
              <span className="wc-task-title">{assignedTask.title}</span>
            </Link>
          ) : (
            <span
              className="wc-assign-task-btn"
              onClick={e => { e.stopPropagation(); navigate(`/workers/${session.id}`) }}
              title="Assign a task to this worker"
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="16" /><line x1="8" y1="12" x2="16" y2="12" />
              </svg>
              Assign task
            </span>
          )}
          {Object.keys(tunnels).length > 0 && (
            <div className="wc-tunnels">
              {Object.entries(tunnels).map(([port]) => (
                <a
                  key={port}
                  href={`http://localhost:${port}`}
                  className="wc-tunnel-badge"
                  onClick={e => { e.preventDefault(); e.stopPropagation(); openUrl(`http://localhost:${port}`) }}
                  title={`Port forwarding: localhost:${port} → rdev:${port}`}
                >
                  :{port}
                </a>
              ))}
            </div>
          )}
        </div>
        <span className="wc-activity" title="Last viewed">{timeAgo(session.last_viewed_at || session.created_at)}</span>
      </div>
    </div>
  )
}
