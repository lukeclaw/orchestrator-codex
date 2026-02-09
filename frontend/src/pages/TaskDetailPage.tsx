import { useState, useEffect, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Task, TaskLink } from '../api/types'
import TaskBoard from '../components/tasks/TaskBoard'
import TaskTable from '../components/tasks/TaskTable'
import ConfirmPopover from '../components/common/ConfirmPopover'
import './TaskDetailPage.css'

const STATUS_OPTIONS = ['todo', 'in_progress', 'done', 'blocked']
const LINK_TYPE_OPTIONS = ['pr', 'doc', 'reference', 'design', 'issue']

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { tasks, sessions, projects, refresh } = useApp()

  // Find the task from shared state
  const task = tasks.find(t => t.id === id) || null
  const project = task ? projects.find(p => p.id === task.project_id) : null

  // Form state
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
  const [hasChanges, setHasChanges] = useState(false)

  // Load task data when task changes
  useEffect(() => {
    if (task) {
      setTitle(task.title)
      setDescription(task.description || '')
      setAssignedSession(task.assigned_session_id || '')
      setStatus(task.status)
      setLinks(task.links || [])
      setHasChanges(false)
      
      // Fetch subtasks
      api<Task[]>(`/api/tasks?parent_task_id=${task.id}&include_subtask_stats=false`)
        .then(setSubtasks)
        .catch(() => setSubtasks([]))
    }
  }, [task])

  // Check if a worker is actively working on this task
  const assignedWorker = sessions.find(s => s.id === task?.assigned_session_id)
  const isWorkerActive = assignedWorker && assignedWorker.status === 'working'
  const isEditable = !isWorkerActive
  const isSubtask = !!task?.parent_task_id

  // Track changes
  useEffect(() => {
    if (!task) return
    const changed = 
      title !== task.title ||
      description !== (task.description || '') ||
      status !== task.status ||
      assignedSession !== (task.assigned_session_id || '') ||
      JSON.stringify(links) !== JSON.stringify(task.links || [])
    setHasChanges(changed)
  }, [task, title, description, status, assignedSession, links])

  const handleSave = async () => {
    if (!task) return
    setSaving(true)
    try {
      const body: Record<string, unknown> = {}
      if (title !== task.title) body.title = title
      if (description !== (task.description || '')) body.description = description || null
      if (status !== task.status) body.status = status
      if (assignedSession !== (task.assigned_session_id || '')) {
        body.assigned_session_id = assignedSession || null
      }
      const linksChanged = JSON.stringify(links) !== JSON.stringify(task.links || [])
      if (linksChanged) {
        body.links = links
      }
      if (Object.keys(body).length > 0) {
        await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify(body) })
        refresh()
      }
      setHasChanges(false)
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
    if (!task) return
    setDeleting(true)
    try {
      await api(`/api/tasks/${task.id}`, { method: 'DELETE' })
      refresh()
      // Navigate back to tasks page or parent task
      if (isSubtask && task.parent_task_id) {
        navigate(`/tasks/${task.parent_task_id}`)
      } else {
        navigate('/tasks')
      }
    } finally {
      setDeleting(false)
    }
  }

  const handleSubtaskClick = (subtask: Task) => {
    navigate(`/tasks/${subtask.id}`)
  }

  if (!task) {
    return (
      <div className="task-detail-page">
        <p className="empty-state">Loading task...</p>
      </div>
    )
  }

  const formatStatus = (s: string) => {
    switch (s) {
      case 'todo': return 'To Do'
      case 'in_progress': return 'In Progress'
      case 'done': return 'Done'
      case 'blocked': return 'Blocked'
      default: return s
    }
  }

  return (
    <div className="task-detail-page">
      {/* Header */}
      <div className="tdp-header">
        <div className="tdp-breadcrumb">
          <Link to="/tasks" className="tdp-back-link">Tasks</Link>
          <span className="tdp-sep">/</span>
          {project && (
            <>
              <Link to={`/projects/${project.id}`} className="tdp-project-link">{project.name}</Link>
              <span className="tdp-sep">/</span>
            </>
          )}
          {isSubtask && task.parent_task_id && (
            <>
              <Link to={`/tasks/${task.parent_task_id}`} className="tdp-parent-link">Parent Task</Link>
              <span className="tdp-sep">/</span>
            </>
          )}
          <span className="tdp-current">{task.task_key || 'Task'}</span>
        </div>
        
        <div className="tdp-title-row">
          <h1>{task.task_key && <span className="tdp-key">{task.task_key}</span>} {task.title}</h1>
          <span className={`status-badge status-${task.status}`}>{formatStatus(task.status)}</span>
        </div>
      </div>

      {isWorkerActive && (
        <div className="tdp-warning">
          ⚠️ Worker <strong>{assignedWorker?.name}</strong> is actively working on this task. Editing is disabled.
        </div>
      )}

      {/* Main Content */}
      <div className="tdp-content">
        <div className="tdp-main">
          {/* Title */}
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

          {/* Description */}
          <div className="form-group">
            <label>Description</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              disabled={!isEditable}
              rows={4}
              placeholder="Task description..."
            />
          </div>

          {/* Links Section */}
          <div className="tdp-section">
            <div className="tdp-section-header">
              <label>Links ({links.length})</label>
              {isEditable && !showAddLink && (
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => setShowAddLink(true)}
                >
                  + Add Link
                </button>
              )}
            </div>
            {showAddLink && (
              <div className="tdp-add-link-form">
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
                <div className="tdp-link-actions">
                  <button type="button" className="btn btn-sm btn-primary" onClick={handleAddLink}>
                    Add
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-secondary"
                    onClick={() => {
                      setShowAddLink(false)
                      setNewLinkUrl('')
                      setNewLinkTitle('')
                      setNewLinkType('reference')
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {links.length === 0 ? (
              <p className="tdp-empty">No links</p>
            ) : (
              <ul className="tdp-links-list">
                {links.map(link => (
                  <li key={link.url} className="tdp-link-item">
                    <span className={`tdp-link-type type-${link.type}`}>{link.type}</span>
                    <a href={link.url} target="_blank" rel="noopener noreferrer">{link.title}</a>
                    {isEditable && (
                      <button
                        type="button"
                        className="tdp-link-remove"
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
          {subtasks.length > 0 && (() => {
            const doneSubtasks = subtasks.filter(st => st.status === 'done').length
            const totalSubtasks = subtasks.length
            const pct = Math.round((doneSubtasks / totalSubtasks) * 100)
            return (
              <div className="tdp-section tdp-subtasks-section">
                <div className="tdp-section-header">
                  <label>Subtasks</label>
                  <span className="tdp-subtasks-progress">{doneSubtasks}/{totalSubtasks} ({pct}%)</span>
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
                <div className="tdp-subtasks-container">
                  {subtaskViewMode === 'board' ? (
                    <TaskBoard tasks={subtasks} onTaskClick={handleSubtaskClick} />
                  ) : (
                    <TaskTable tasks={subtasks} onTaskClick={handleSubtaskClick} />
                  )}
                </div>
              </div>
            )
          })()}
        </div>

        {/* Sidebar */}
        <div className="tdp-sidebar">
          <div className="tdp-sidebar-section">
            <label>Status</label>
            <select
              className="filter-select"
              value={status}
              onChange={e => setStatus(e.target.value)}
              disabled={!isEditable}
            >
              {STATUS_OPTIONS.map(s => (
                <option key={s} value={s}>{formatStatus(s)}</option>
              ))}
            </select>
          </div>

          <div className="tdp-sidebar-section">
            <label>Priority</label>
            <span className={`priority-badge priority-${task.priority}`}>
              {task.priority === 'H' ? 'High' : task.priority === 'M' ? 'Medium' : 'Low'}
            </span>
          </div>

          {!isSubtask && (
            <div className="tdp-sidebar-section">
              <label>Assigned Worker</label>
              <select
                className="filter-select"
                value={assignedSession}
                onChange={e => setAssignedSession(e.target.value)}
                disabled={!isEditable}
              >
                <option value="">Unassigned</option>
                {sessions.filter(s => s.session_type === 'worker').map(s => (
                  <option key={s.id} value={s.id}>{s.name} ({s.status})</option>
                ))}
              </select>
            </div>
          )}

          <div className="tdp-sidebar-section">
            <label>Project</label>
            {project ? (
              <Link to={`/projects/${project.id}`} className="tdp-project-badge">
                {project.name}
              </Link>
            ) : (
              <span className="tdp-empty">No project</span>
            )}
          </div>

          <div className="tdp-sidebar-section">
            <label>Created</label>
            <span className="tdp-date">{new Date(task.created_at).toLocaleString()}</span>
          </div>

          {task.started_at && (
            <div className="tdp-sidebar-section">
              <label>Started</label>
              <span className="tdp-date">{new Date(task.started_at).toLocaleString()}</span>
            </div>
          )}

          {task.completed_at && (
            <div className="tdp-sidebar-section">
              <label>Completed</label>
              <span className="tdp-date">{new Date(task.completed_at).toLocaleString()}</span>
            </div>
          )}
        </div>
      </div>

      {/* Footer Actions */}
      <div className="tdp-footer">
        {isEditable && (
          <ConfirmPopover
            message={`Delete ${isSubtask ? 'subtask' : 'task'} "${task.title}"?`}
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
        <div className="tdp-footer-spacer" />
        {isEditable && hasChanges && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving || !title.trim()}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        )}
      </div>
    </div>
  )
}
