import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api } from '../api/client'
import { useNotify } from '../context/NotificationContext'
import { useApp } from '../context/AppContext'
import TerminalView from '../components/terminal/TerminalView'
import { IconArrowLeft, IconPause, IconPlay, IconStop, IconRefresh, IconTrash, IconSync } from '../components/common/Icons'
import ConfirmPopover from '../components/common/ConfirmPopover'
import './SessionDetailPage.css'

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const notify = useNotify()
  
  // Use shared state from AppContext for session
  const { sessions, tasks: allTasks, refresh } = useApp()
  const session = sessions.find(s => s.id === id) || null
  const tasks = allTasks.filter(t => t.assigned_session_id === id)
  
  // Local state for page-specific data
  const [error, setError] = useState('')
  const [actionPending, setActionPending] = useState(false)

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
          <button className="sd-back-btn" onClick={() => navigate(-1)} title="Go back">
            <IconArrowLeft size={16} />
          </button>
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
          {/* Task link */}
          {tasks.length > 0 && (
            <Link to={`/tasks/${tasks[0].id}`} className="sd-task-link">
              <span className="sd-task-label">Task:</span>
              <span className="sd-task-title">{tasks[0].title}</span>
            </Link>
          )}

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

      {/* Terminal fills the rest */}
      <div className="sd-terminal-area">
        <TerminalView sessionId={session.id} />
      </div>
    </div>
  )
}
