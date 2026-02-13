import { useState, useEffect, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import type { Session, Task } from '../../api/types'
import { api } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import { timeAgo } from '../common/TimeAgo'
import { IconTrash, IconPause, IconPlay, IconStop, IconRefresh } from '../common/Icons'
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

export default function WorkerCard({
  session, assignedTask, onRemove,
}: Props) {
  const navigate = useNavigate()
  const notify = useNotify()
  const [preview, setPreview] = useState('')
  const [removing, setRemoving] = useState(false)
  const [actionPending, setActionPending] = useState(false)
  const [tunnels, setTunnels] = useState<Record<string, TunnelInfo>>({})
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const tunnelIntervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const hasLoadedRef = useRef(false)
  const sessionIdRef = useRef(session.id)  // Track current session.id for race protection
  const fetchGenRef = useRef(0)  // Generation counter to ignore stale fetches

  // Check if this is an rdev worker
  const isRdev = session.host.includes('/')

  // Update ref when session changes (for interval callbacks to read current value)
  sessionIdRef.current = session.id

  useEffect(() => {
    // Increment generation - any in-flight fetches from previous session are now stale
    const currentGen = ++fetchGenRef.current
    const targetSessionId = session.id
    
    hasLoadedRef.current = false
    setPreview('')  // Clear old preview immediately

    async function fetchPreview() {
      // Double-check we're still fetching for the right session
      if (sessionIdRef.current !== targetSessionId || fetchGenRef.current !== currentGen) {
        return  // Stale - session changed since this fetch/interval was created
      }
      
      try {
        const data = await api<{ content: string; status: string }>(
          `/api/sessions/${targetSessionId}/preview`
        )
        
        // Triple-check after await: session might have changed during fetch
        if (sessionIdRef.current !== targetSessionId || fetchGenRef.current !== currentGen) {
          return  // Stale response - discard
        }
        
        if (data.content) {
          setPreview(data.content)
          hasLoadedRef.current = true
        } else if (!hasLoadedRef.current) {
          hasLoadedRef.current = true
        }
      } catch {
        if (sessionIdRef.current === targetSessionId && fetchGenRef.current === currentGen && !hasLoadedRef.current) {
          hasLoadedRef.current = true
        }
      }
    }

    fetchPreview()
    intervalRef.current = setInterval(fetchPreview, 5000)

    return () => {
      clearInterval(intervalRef.current)
    }
  }, [session.id])

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

  async function handleRemove() {
    if (removing) return
    setRemoving(true)
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
      notify(`Reconnecting worker ${session.name}...`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to reconnect', 'error')
    } finally {
      setActionPending(false)
    }
  }

  // Take last ~20 lines for the preview
  const previewLines = preview ? preview.split('\n').slice(-20).join('\n') : ''

  return (
    <div
      className={`worker-card ${session.status}${removing ? ' removing' : ''}`}
      data-testid="worker-card"
      data-session-id={session.id}
      onClick={() => navigate(`/workers/${session.id}`)}
    >
      <div className="wc-header">
        <div className="wc-header-left">
          <span className={`status-indicator ${session.status}`} />
          <span className="wc-name">{session.name}</span>
          {session.host.includes('/') && <span className="wc-type-tag rdev">rdev</span>}
          <span className={`status-badge ${session.status}`}>{session.status}</span>
        </div>
        <div className="wc-actions">
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
              {/* Pause/Continue button */}
              <button
                className={`wc-action-btn ${session.status === 'paused' ? 'continue' : 'pause'}`}
                onClick={handlePauseOrContinue}
                disabled={actionPending || session.status === 'idle'}
                title={session.status === 'paused' ? 'Continue' : 'Pause'}
              >
                {session.status === 'paused' ? <IconPlay size={14} /> : <IconPause size={14} />}
              </button>

              {/* Stop button */}
              <ConfirmPopover
                message={`Stop worker "${session.name}" and clear context?`}
                confirmLabel="Stop"
                onConfirm={handleStop}
                variant="danger"
              >
                {({ onClick }) => (
                  <button
                    className="wc-action-btn stop"
                    onClick={onClick}
                    disabled={actionPending || session.status === 'idle'}
                    title="Stop and clear"
                  >
                    <IconStop size={14} />
                  </button>
                )}
              </ConfirmPopover>
            </>
          )}

          {/* Remove button */}
          <ConfirmPopover
            message={`Remove worker "${session.name}"?`}
            confirmLabel="Remove"
            onConfirm={handleRemove}
            variant="danger"
          >
            {({ onClick }) => (
              <button
                className="wc-remove-btn"
                onClick={onClick}
                disabled={removing}
                title="Remove worker"
              >
                <IconTrash size={14} />
              </button>
            )}
          </ConfirmPopover>
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
            <span className="wc-task-empty">No task assigned</span>
          )}
          {Object.keys(tunnels).length > 0 && (
            <div className="wc-tunnels">
              {Object.entries(tunnels).map(([port]) => (
                <a
                  key={port}
                  href={`http://localhost:${port}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="wc-tunnel-badge"
                  onClick={e => e.stopPropagation()}
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
