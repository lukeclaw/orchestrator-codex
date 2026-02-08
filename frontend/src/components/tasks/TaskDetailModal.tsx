import { useState, useEffect } from 'react'
import Modal from '../common/Modal'
import ConfirmPopover from '../common/ConfirmPopover'
import TaskBoard from './TaskBoard'
import TaskTable from './TaskTable'
import { api } from '../../api/client'
import type { Task, Session, TaskLink } from '../../api/types'
import './TaskDetailModal.css'

interface Props {
  task: Task | null
  sessions: Session[]
  onClose: () => void
  onUpdate: (id: string, body: Partial<Task>) => Promise<unknown>
  onDelete?: (id: string) => Promise<unknown>
}

const STATUS_OPTIONS = ['todo', 'in_progress', 'done', 'blocked']
const LINK_TYPE_OPTIONS = ['pr', 'doc', 'reference', 'design', 'issue']

export default function TaskDetailModal({ task, sessions, onClose, onUpdate, onDelete }: Props) {
  // Navigation state for parent/subtask drill-down
  const [currentTask, setCurrentTask] = useState<Task | null>(null)
  const [parentTask, setParentTask] = useState<Task | null>(null)

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [assignedSession, setAssignedSession] = useState('')
  const [status, setStatus] = useState('')
  const [links, setLinks] = useState<TaskLink[]>([])
  const [subtasks, setSubtasks] = useState<Task[]>([])
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [newLinkUrl, setNewLinkUrl] = useState('')
  const [newLinkTitle, setNewLinkTitle] = useState('')
  const [newLinkType, setNewLinkType] = useState('reference')
  const [showAddLink, setShowAddLink] = useState(false)
  const [subtaskViewMode, setSubtaskViewMode] = useState<'board' | 'table'>('board')

  // Reset navigation when task prop changes (modal opens with new task)
  useEffect(() => {
    if (task) {
      setCurrentTask(task)
      setParentTask(null)
    } else {
      setCurrentTask(null)
      setParentTask(null)
    }
  }, [task])

  // Load form state when currentTask changes
  useEffect(() => {
    if (currentTask) {
      setTitle(currentTask.title)
      setDescription(currentTask.description || '')
      setAssignedSession(currentTask.assigned_session_id || '')
      setStatus(currentTask.status)
      setLinks(currentTask.links || [])
      // Fetch subtasks
      api<Task[]>(`/api/tasks?parent_task_id=${currentTask.id}&include_subtask_stats=false`)
        .then(setSubtasks)
        .catch(() => setSubtasks([]))
    }
  }, [currentTask])

  const handleSubtaskClick = (subtask: Task) => {
    if (currentTask) {
      setParentTask(currentTask)
      setCurrentTask(subtask)
    }
  }

  const handleBack = () => {
    if (parentTask) {
      setCurrentTask(parentTask)
      setParentTask(null)
    }
  }

  if (!task || !currentTask) return null

  // Check if a worker is actively working on this task
  const assignedWorker = sessions.find(s => s.id === currentTask.assigned_session_id)
  const isWorkerActive = assignedWorker && assignedWorker.status === 'working'
  const isEditable = !isWorkerActive
  const isSubtask = !!parentTask

  const handleSave = async () => {
    setSaving(true)
    try {
      const body: Record<string, unknown> = {}
      if (title !== currentTask.title) body.title = title
      if (description !== (currentTask.description || '')) body.description = description || null
      if (status !== currentTask.status) body.status = status
      if (assignedSession !== (currentTask.assigned_session_id || '')) {
        body.assigned_session_id = assignedSession || null
      }
      // Check if links changed
      const linksChanged = JSON.stringify(links) !== JSON.stringify(currentTask.links || [])
      if (linksChanged) {
        body.links = links
      }
      if (Object.keys(body).length > 0) {
        await onUpdate(currentTask.id, body as Partial<Task>)
      }
      // If we're on a subtask, go back to parent; otherwise close
      if (isSubtask) {
        handleBack()
      } else {
        onClose()
      }
    } finally {
      setSaving(false)
    }
  }

  const handleAddLink = () => {
    if (!newLinkUrl.trim()) return
    const newLink: TaskLink = {
      url: newLinkUrl.trim(),
      title: newLinkTitle.trim() || newLinkUrl.trim(),
      type: newLinkType,
    }
    setLinks([...links, newLink])
    setNewLinkUrl('')
    setNewLinkTitle('')
    setNewLinkType('reference')
    setShowAddLink(false)
  }

  const handleRemoveLink = (url: string) => {
    setLinks(links.filter(l => l.url !== url))
  }

  const handleDelete = async () => {
    if (!onDelete) return
    setDeleting(true)
    try {
      await onDelete(currentTask.id)
      // If we're on a subtask, go back to parent; otherwise close
      if (isSubtask) {
        handleBack()
      } else {
        onClose()
      }
    } finally {
      setDeleting(false)
    }
  }

  const modalTitle = isSubtask ? 'Subtask Details' : 'Task Details'

  return (
    <Modal open={!!task} onClose={onClose} title={modalTitle} extraWide>
      {/* Back button for subtask navigation */}
      {isSubtask && (
        <div className="tdm-back-nav">
          <button type="button" className="tdm-back-btn" onClick={handleBack}>
            ← Back to {parentTask?.title}
          </button>
        </div>
      )}
      <div className="modal-body">
        {isWorkerActive && (
          <div className="tdm-warning">
            ⚠️ Worker <strong>{assignedWorker?.name}</strong> is actively working on this task. Editing is disabled.
          </div>
        )}

        <div className="form-group">
          <label>Title</label>
          <input
            type="text"
            value={title}
            onChange={e => setTitle(e.target.value)}
            disabled={!isEditable}
            required
          />
        </div>

        <div className="form-group">
          <label>Description</label>
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            disabled={!isEditable}
            rows={3}
            placeholder="Optional description..."
          />
        </div>

        <div className="tdm-row">
          <div className="form-group">
            <label>Status</label>
            <select
              className="filter-select"
              value={status}
              onChange={e => setStatus(e.target.value)}
              disabled={!isEditable}
            >
              {STATUS_OPTIONS.map(s => (
                <option key={s} value={s}>
                  {s === 'todo' ? 'To Do' : s === 'in_progress' ? 'In Progress' : s === 'done' ? 'Done' : 'Blocked'}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label>Assigned Worker</label>
            <select
              className="filter-select"
              value={assignedSession}
              onChange={e => setAssignedSession(e.target.value)}
              data-testid="assign-session-select"
              disabled={!isEditable}
            >
              <option value="">Unassigned</option>
              {sessions.map(s => (
                <option key={s.id} value={s.id}>{s.name} ({s.status})</option>
              ))}
            </select>
          </div>
        </div>

        {/* Links Section */}
        <div className="tdm-section">
          <div className="tdm-section-header">
            <label>Links ({links.length})</label>
            {isEditable && (
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                onClick={() => setShowAddLink(!showAddLink)}
              >
                + Add Link
              </button>
            )}
          </div>
          {showAddLink && (
            <div className="tdm-add-link-form">
              <input
                type="url"
                placeholder="URL"
                value={newLinkUrl}
                onChange={e => setNewLinkUrl(e.target.value)}
              />
              <input
                type="text"
                placeholder="Title (optional)"
                value={newLinkTitle}
                onChange={e => setNewLinkTitle(e.target.value)}
              />
              <select value={newLinkType} onChange={e => setNewLinkType(e.target.value)}>
                {LINK_TYPE_OPTIONS.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <button type="button" className="btn btn-sm btn-primary" onClick={handleAddLink}>
                Add
              </button>
            </div>
          )}
          {links.length === 0 ? (
            <p className="tdm-empty">No links</p>
          ) : (
            <ul className="tdm-links-list">
              {links.map(link => (
                <li key={link.url} className="tdm-link-item">
                  <span className={`tdm-link-type type-${link.type}`}>{link.type}</span>
                  <a href={link.url} target="_blank" rel="noopener noreferrer">{link.title}</a>
                  {isEditable && (
                    <button
                      type="button"
                      className="tdm-link-remove"
                      onClick={() => handleRemoveLink(link.url)}
                      title="Remove link"
                    >
                      ×
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Subtasks Section */}
        {subtasks.length > 0 && (
          <div className="tdm-section tdm-subtasks-section">
            <div className="tdm-section-header">
              <label>Subtasks ({subtasks.length})</label>
              <div className="toggle-group toggle-sm">
                <button
                  type="button"
                  className={`toggle-btn${subtaskViewMode === 'board' ? ' active' : ''}`}
                  onClick={() => setSubtaskViewMode('board')}
                >
                  Board
                </button>
                <button
                  type="button"
                  className={`toggle-btn${subtaskViewMode === 'table' ? ' active' : ''}`}
                  onClick={() => setSubtaskViewMode('table')}
                >
                  Table
                </button>
              </div>
            </div>
            <div className="tdm-subtasks-container">
              {subtaskViewMode === 'board' ? (
                <TaskBoard tasks={subtasks} onTaskClick={handleSubtaskClick} />
              ) : (
                <TaskTable tasks={subtasks} onTaskClick={handleSubtaskClick} />
              )}
            </div>
          </div>
        )}
      </div>
      <div className="modal-footer">
        {isEditable && onDelete && (
          <ConfirmPopover
            message={`Delete ${isSubtask ? 'subtask' : 'task'} "${currentTask.title}"?`}
            confirmLabel="Delete"
            onConfirm={handleDelete}
            variant="danger"
          >
            {({ onClick }) => (
              <button
                type="button"
                className="btn btn-danger"
                onClick={onClick}
                disabled={deleting}
              >
                {deleting ? 'Deleting...' : 'Delete'}
              </button>
            )}
          </ConfirmPopover>
        )}
        <div className="tdm-footer-spacer" />
        <button type="button" className="btn btn-secondary" onClick={onClose}>
          {isEditable ? 'Cancel' : 'Close'}
        </button>
        {isEditable && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving || !title.trim()}
            data-testid="save-task-btn"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        )}
      </div>
    </Modal>
  )
}
