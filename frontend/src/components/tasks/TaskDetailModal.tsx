import { useState, useEffect } from 'react'
import Modal from '../common/Modal'
import type { Task, Session } from '../../api/types'
import './TaskCard.css'

interface Props {
  task: Task | null
  sessions: Session[]
  onClose: () => void
  onUpdate: (id: string, body: Partial<Pick<Task, 'status' | 'assigned_session_id'>>) => Promise<unknown>
}

const STATUS_OPTIONS = ['todo', 'in_progress', 'done', 'blocked']

export default function TaskDetailModal({ task, sessions, onClose, onUpdate }: Props) {
  const [assignedSession, setAssignedSession] = useState('')
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (task) {
      setAssignedSession(task.assigned_session_id || '')
      setStatus(task.status)
    }
  }, [task])

  if (!task) return null

  const handleSave = async () => {
    setSaving(true)
    try {
      const body: Record<string, string | null> = {}
      if (status !== task.status) body.status = status
      if (assignedSession !== (task.assigned_session_id || '')) {
        body.assigned_session_id = assignedSession || null
      }
      if (Object.keys(body).length > 0) {
        await onUpdate(task.id, body)
      }
      onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={!!task} onClose={onClose} title="Task Details" wide>
      <div className="modal-body">
        <h3 style={{ margin: '0 0 8px 0', fontSize: 16 }}>{task.title}</h3>
        {task.description && (
          <p style={{ color: 'var(--text-secondary)', fontSize: 13, margin: '0 0 16px 0', lineHeight: 1.5 }}>
            {task.description}
          </p>
        )}

        <div className="form-group">
          <label>Status</label>
          <select
            className="filter-select"
            style={{ width: '100%', padding: '8px 12px', fontSize: 14 }}
            value={status}
            onChange={e => setStatus(e.target.value)}
          >
            {STATUS_OPTIONS.map(s => (
              <option key={s} value={s}>
                {s === 'todo' ? 'To Do' : s === 'in_progress' ? 'In Progress' : s === 'done' ? 'Done' : 'Blocked'}
              </option>
            ))}
          </select>
        </div>

        <div className="form-group">
          <label>Assigned Session</label>
          <select
            className="filter-select"
            style={{ width: '100%', padding: '8px 12px', fontSize: 14 }}
            value={assignedSession}
            onChange={e => setAssignedSession(e.target.value)}
            data-testid="assign-session-select"
          >
            <option value="">Unassigned</option>
            {sessions.map(s => (
              <option key={s.id} value={s.id}>{s.name} ({s.status})</option>
            ))}
          </select>
        </div>
      </div>
      <div className="modal-footer">
        <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving}
          data-testid="save-task-btn"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
