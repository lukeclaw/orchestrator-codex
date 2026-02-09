import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import type { Activity } from '../api/types'
import { api } from '../api/client'
import { useNotify } from '../context/NotificationContext'
import { useApp } from '../context/AppContext'
import TerminalView from '../components/terminal/TerminalView'
import { timeAgo } from '../components/common/TimeAgo'
import { IconArrowLeft } from '../components/common/Icons'
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
  const [activities, setActivities] = useState<Activity[]>([])
  const [error, setError] = useState('')
  const [sendMsg, setSendMsg] = useState('')

  // Load page-specific data (activities)
  const loadPageData = useCallback(async () => {
    if (!id) return
    try {
      const a = await api<Activity[]>(`/api/activities?session_id=${id}&limit=10`).catch(() => [])
      setActivities(a)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load session data')
    }
  }, [id])

  // Initial load of page-specific data
  useEffect(() => { loadPageData() }, [loadPageData])

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

  async function handleSendMessage(e: React.FormEvent) {
    e.preventDefault()
    if (!id || !sendMsg.trim()) return
    try {
      await api(`/api/sessions/${id}/send`, {
        method: 'POST',
        body: JSON.stringify({ message: sendMsg.trim() }),
      })
      setSendMsg('')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to send', 'error')
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
          <Link to="/workers" className="sd-back-link">
            <IconArrowLeft size={16} />
          </Link>
          <h2 className="sd-title">{session.name}</h2>
          <span className={`status-badge ${session.status}`}>{session.status}</span>
        </div>
        <div className="sd-topbar-meta">
          <span className="sd-meta-item">
            <span className="sd-meta-label">Host</span>
            <span className="sd-meta-value">{session.host}</span>
          </span>
          {session.work_dir && (
            <span className="sd-meta-item">
              <span className="sd-meta-label">Path</span>
              <span className="sd-meta-value">{session.work_dir}</span>
            </span>
          )}
          <span className="sd-meta-item">
            <span className="sd-meta-label">Last active</span>
            <span className="sd-meta-value">{session.last_activity ? timeAgo(session.last_activity) : 'Never'}</span>
          </span>
        </div>
        <div className="sd-topbar-actions">
          {tasks.length > 0 && (
            <span className="sd-chip">{tasks.length} task{tasks.length > 1 ? 's' : ''}</span>
          )}
          {activities.length > 0 && (
            <span className="sd-chip">{activities.length} events</span>
          )}
          <ConfirmPopover
            message={`Remove session "${session.name}"?`}
            confirmLabel="Remove"
            onConfirm={handleDelete}
            variant="danger"
          >
            {({ onClick }) => (
              <button
                className="btn btn-danger btn-sm"
                data-testid="delete-session-btn"
                onClick={onClick}
              >
                Remove
              </button>
            )}
          </ConfirmPopover>
        </div>
      </div>

      {/* Terminal fills the rest */}
      <div className="sd-terminal-area">
        <TerminalView sessionId={session.id} />
      </div>

      {/* Message bar at bottom */}
      <form className="sd-send-form" onSubmit={handleSendMessage}>
        <input
          type="text"
          value={sendMsg}
          onChange={e => setSendMsg(e.target.value)}
          placeholder={`Send message to ${session.name}...`}
          data-testid="send-message-input"
        />
        <button type="submit" className="btn btn-primary btn-sm" data-testid="send-message-btn">
          Send
        </button>
      </form>
    </div>
  )
}
