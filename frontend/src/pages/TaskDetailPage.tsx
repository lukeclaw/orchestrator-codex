import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Task, TaskLink } from '../api/types'
import { IconArrowLeft } from '../components/common/Icons'
import ConfirmPopover from '../components/common/ConfirmPopover'
import TagDropdown from '../components/common/TagDropdown'
import './TaskDetailPage.css'

const STATUS_OPTIONS = [
  { value: 'todo', label: 'To Do', className: 'status-todo' },
  { value: 'in_progress', label: 'In Progress', className: 'status-in_progress' },
  { value: 'done', label: 'Done', className: 'status-done' },
  { value: 'blocked', label: 'Blocked', className: 'status-blocked' },
]

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { tasks, sessions, projects, refresh } = useApp()

  const task = tasks.find(t => t.id === id) || null
  const project = task ? projects.find(p => p.id === task.project_id) : null

  // Editing states
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [isEditingDesc, setIsEditingDesc] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [status, setStatus] = useState('')
  const [assignedSession, setAssignedSession] = useState('')
  const [links, setLinks] = useState<TaskLink[]>([])
  const [subtasks, setSubtasks] = useState<Task[]>([])
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [showAddLink, setShowAddLink] = useState(false)
  const [newLinkUrl, setNewLinkUrl] = useState('')
  const [newLinkTag, setNewLinkTag] = useState('')
  const [editingLinkUrl, setEditingLinkUrl] = useState<string | null>(null)
  const [editLinkUrl, setEditLinkUrl] = useState('')
  const [editLinkTag, setEditLinkTag] = useState('')
  const [subtasksExpanded, setSubtasksExpanded] = useState(true)

  useEffect(() => {
    if (task) {
      setEditTitle(task.title)
      setEditDesc(task.description || '')
      setStatus(task.status)
      setAssignedSession(task.assigned_session_id || '')
      setLinks(task.links || [])
      
      api<Task[]>(`/api/tasks?parent_task_id=${task.id}&include_subtask_stats=false`)
        .then(setSubtasks)
        .catch(() => setSubtasks([]))
    }
  }, [task])

  const assignedWorker = sessions.find(s => s.id === task?.assigned_session_id)
  const isWorkerActive = assignedWorker && assignedWorker.status === 'working'
  const isEditable = !isWorkerActive
  const isSubtask = !!task?.parent_task_id
  const parentTask = isSubtask ? tasks.find(t => t.id === task?.parent_task_id) : null

  const formatStatus = (s: string) => {
    switch (s) {
      case 'todo': return 'To Do'
      case 'in_progress': return 'In Progress'
      case 'done': return 'Done'
      case 'blocked': return 'Blocked'
      default: return s
    }
  }

  const handleSaveField = async (field: string, value: unknown) => {
    if (!task) return
    setSaving(true)
    try {
      await api(`/api/tasks/${task.id}`, { 
        method: 'PATCH', 
        body: JSON.stringify({ [field]: value }) 
      })
      refresh()
    } finally {
      setSaving(false)
    }
  }

  const handleTitleSave = async () => {
    if (!editTitle.trim() || editTitle === task?.title) {
      setIsEditingTitle(false)
      return
    }
    await handleSaveField('title', editTitle)
    setIsEditingTitle(false)
  }

  const handleDescSave = async () => {
    if (editDesc === (task?.description || '')) {
      setIsEditingDesc(false)
      return
    }
    await handleSaveField('description', editDesc || null)
    setIsEditingDesc(false)
  }

  const handleStatusChange = async (newStatus: string) => {
    setStatus(newStatus)
    await handleSaveField('status', newStatus)
  }

  const handleAssignChange = async (sessionId: string) => {
    setAssignedSession(sessionId)
    await handleSaveField('assigned_session_id', sessionId || null)
  }

  const handleAddLink = async () => {
    if (!newLinkUrl.trim() || !task) return
    const newLink: TaskLink = {
      url: newLinkUrl.trim(),
      tag: newLinkTag.trim() || undefined,
    }
    const updatedLinks = [...links, newLink]
    setLinks(updatedLinks)
    await handleSaveField('links', updatedLinks)
    setNewLinkUrl('')
    setNewLinkTag('')
    setShowAddLink(false)
  }

  const handleRemoveLink = async (url: string) => {
    const updatedLinks = links.filter(l => l.url !== url)
    setLinks(updatedLinks)
    await handleSaveField('links', updatedLinks)
  }

  const startEditLink = (link: TaskLink) => {
    setEditingLinkUrl(link.url)
    setEditLinkUrl(link.url)
    setEditLinkTag(link.tag || '')
  }

  const cancelEditLink = () => {
    setEditingLinkUrl(null)
    setEditLinkUrl('')
    setEditLinkTag('')
  }

  const handleSaveLink = async () => {
    if (!editLinkUrl.trim() || !editingLinkUrl) return
    const updatedLinks = links.map(l => 
      l.url === editingLinkUrl 
        ? { url: editLinkUrl.trim(), tag: editLinkTag.trim() || undefined }
        : l
    )
    setLinks(updatedLinks)
    await handleSaveField('links', updatedLinks)
    cancelEditLink()
  }

  const isLinkChanged = () => {
    if (!editingLinkUrl) return false
    const original = links.find(l => l.url === editingLinkUrl)
    if (!original) return false
    return editLinkUrl !== original.url || editLinkTag !== (original.tag || '')
  }

  const handleDelete = async () => {
    if (!task) return
    setDeleting(true)
    try {
      await api(`/api/tasks/${task.id}`, { method: 'DELETE' })
      refresh()
      if (isSubtask && task.parent_task_id) {
        navigate(`/tasks/${task.parent_task_id}`)
      } else {
        navigate('/tasks')
      }
    } finally {
      setDeleting(false)
    }
  }

  if (!task) {
    return (
      <div className="task-detail-page">
        <p className="empty-state">Loading task...</p>
      </div>
    )
  }

  const doneSubtasks = subtasks.filter(st => st.status === 'done').length
  const totalSubtasks = subtasks.length
  const progressPct = totalSubtasks > 0 ? Math.round((doneSubtasks / totalSubtasks) * 100) : 0

  return (
    <div className="task-detail-page">
      {/* Header with back button */}
      <div className="tdp-top-bar">
        <button className="tdp-back-btn" onClick={() => navigate(-1)} title="Go back">
          <IconArrowLeft size={16} />
        </button>
        <nav className="tdp-breadcrumb">
          <Link to="/tasks">Tasks</Link>
          <span>/</span>
          {parentTask && <><Link to={`/tasks/${parentTask.id}`}>{parentTask.task_key}</Link><span>/</span></>}
          <span className="current">{task.task_key}</span>
        </nav>
      </div>

      <div className="tdp-layout">
        {/* Main Content */}
        <main className="tdp-main">
          {/* Header Card with Title and Description */}
          <div className="tdp-card tdp-header-card">
            <div className="tdp-title-section">
              {task.task_key && <span className="tdp-task-key">{task.task_key}</span>}
              {isEditingTitle ? (
                <div className="tdp-inline-edit">
                  <input
                    className="tdp-title-input"
                    value={editTitle}
                    onChange={e => setEditTitle(e.target.value)}
                    onBlur={() => {
                      if (editTitle === task.title) {
                        setIsEditingTitle(false)
                      }
                    }}
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleTitleSave()
                      if (e.key === 'Escape') {
                        setIsEditingTitle(false)
                        setEditTitle(task.title)
                      }
                    }}
                    autoFocus
                  />
                  <div className="tdp-inline-actions">
                    <button
                      className="tdp-action-btn save"
                      onClick={handleTitleSave}
                      disabled={!editTitle.trim() || editTitle === task.title}
                      title="Save"
                    >
                      ✓
                    </button>
                    <button
                      className="tdp-action-btn cancel"
                      onClick={() => { setIsEditingTitle(false); setEditTitle(task.title) }}
                      title="Discard"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              ) : (
                <h1 
                  className={`tdp-title ${isEditable ? 'editable' : ''}`}
                  onClick={() => isEditable && setIsEditingTitle(true)}
                  title={task.title}
                >
                  {task.title}
                </h1>
              )}
            </div>

            {/* Description - inline editable */}
            {isEditingDesc ? (
              <div className="tdp-desc-edit">
                <textarea
                  value={editDesc}
                  onChange={e => setEditDesc(e.target.value)}
                  placeholder="Add a description..."
                  rows={Math.max(1, (task.description || '').split('\n').length)}
                  autoFocus
                  onBlur={() => {
                    if (editDesc === (task.description || '')) {
                      setIsEditingDesc(false)
                    }
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Escape') {
                      setIsEditingDesc(false)
                      setEditDesc(task.description || '')
                    }
                  }}
                />
                <div className="tdp-inline-actions desc-actions">
                  <button
                    className="tdp-action-btn save"
                    onClick={handleDescSave}
                    disabled={editDesc === (task.description || '')}
                    title="Save"
                  >
                    ✓
                  </button>
                  <button
                    className="tdp-action-btn cancel"
                    onClick={() => { setIsEditingDesc(false); setEditDesc(task.description || '') }}
                    title="Discard"
                  >
                    ✕
                  </button>
                </div>
              </div>
            ) : (
              <p 
                className={`tdp-desc ${isEditable ? 'editable' : ''} ${!task.description ? 'empty' : ''}`}
                onClick={() => isEditable && setIsEditingDesc(true)}
                title={isEditable ? 'Click to edit' : undefined}
              >
                {task.description || 'Add a description...'}
              </p>
            )}

            {isWorkerActive && (
              <div className="tdp-worker-active">
                Worker <strong>{assignedWorker?.name}</strong> is working on this task
              </div>
            )}
          </div>

          {/* Links Card */}
          <div className="tdp-card">
            <div className="tdp-card-header">
              <h3>Links {links.length > 0 && <span className="count">({links.length})</span>}</h3>
              {isEditable && !showAddLink && !editingLinkUrl && (
                <button className="tdp-edit-btn" onClick={() => setShowAddLink(true)}>+ Add</button>
              )}
            </div>
            {showAddLink && (
              <div className="tdp-link-form-inline">
                <input type="text" placeholder="Tag (optional)" value={newLinkTag} onChange={e => setNewLinkTag(e.target.value)} className="tag-input" />
                <input type="url" placeholder="URL" value={newLinkUrl} onChange={e => setNewLinkUrl(e.target.value)} autoFocus />
                <div className="tdp-inline-actions">
                  <button className="tdp-action-btn save" onClick={handleAddLink} disabled={!newLinkUrl.trim()} title="Add">✓</button>
                  <button className="tdp-action-btn cancel" onClick={() => { setShowAddLink(false); setNewLinkUrl(''); setNewLinkTag('') }} title="Cancel">✕</button>
                </div>
              </div>
            )}
            {links.length === 0 && !showAddLink ? (
              <p className="tdp-desc empty">No links attached</p>
            ) : (
              <div className="tdp-links">
                {links.map(link => (
                  editingLinkUrl === link.url ? (
                    <div key={link.url} className="tdp-link-form-inline">
                      <input type="text" placeholder="Tag" value={editLinkTag} onChange={e => setEditLinkTag(e.target.value)} className="tag-input" />
                      <input type="url" placeholder="URL" value={editLinkUrl} onChange={e => setEditLinkUrl(e.target.value)} autoFocus />
                      <div className="tdp-inline-actions">
                        <button className="tdp-action-btn save" onClick={handleSaveLink} disabled={!editLinkUrl.trim() || !isLinkChanged()} title="Save">✓</button>
                        <button className="tdp-action-btn cancel" onClick={cancelEditLink} title="Cancel">✕</button>
                      </div>
                    </div>
                  ) : (
                    <div key={link.url} className="tdp-link">
                      <span className={`link-tag ${link.tag ? '' : 'empty'}`}>{link.tag || ''}</span>
                      <a href={link.url} target="_blank" rel="noopener noreferrer">{link.url}</a>
                      {isEditable && (
                        <div className="tdp-link-actions">
                          <button className="link-edit" onClick={() => startEditLink(link)} title="Edit">✎</button>
                          <button className="link-remove" onClick={() => handleRemoveLink(link.url)} title="Remove">×</button>
                        </div>
                      )}
                    </div>
                  )
                ))}
              </div>
            )}
          </div>

          {/* Subtasks Card */}
          {subtasks.length > 0 && (
            <div className="tdp-card tdp-subtasks-card">
              <div className="tdp-card-header clickable" onClick={() => setSubtasksExpanded(!subtasksExpanded)}>
                <h3>
                  <span className={`expand-icon ${subtasksExpanded ? 'expanded' : ''}`}>▶</span>
                  Subtasks
                  <span className="count">({doneSubtasks}/{totalSubtasks})</span>
                </h3>
                <div className="tdp-progress">
                  <div className="tdp-progress-bar" style={{ width: `${progressPct}%` }} />
                </div>
              </div>
              {subtasksExpanded && (
                <div className="tdp-subtasks-list">
                  {subtasks.map(st => (
                    <Link key={st.id} to={`/tasks/${st.id}`} className="tdp-subtask-item">
                      <span className={`subtask-status status-${st.status}`} />
                      <span className="subtask-key">{st.task_key}</span>
                      <span className="subtask-title">{st.title}</span>
                      <span className={`status-badge small status-${st.status}`}>{formatStatus(st.status)}</span>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="tdp-actions">
            {isEditable && (
              <ConfirmPopover
                message={`Delete "${task.title}"?`}
                confirmLabel="Delete"
                onConfirm={handleDelete}
                variant="danger"
              >
                {({ onClick }) => (
                  <button className="btn btn-danger" onClick={onClick} disabled={deleting}>
                    {deleting ? 'Deleting...' : 'Delete Task'}
                  </button>
                )}
              </ConfirmPopover>
            )}
          </div>
        </main>

        {/* Sidebar */}
        <aside className="tdp-sidebar">
          <div className="tdp-sidebar-card">
            <div className="sidebar-field">
              <label>Status</label>
              <TagDropdown
                value={status}
                options={STATUS_OPTIONS}
                onChange={handleStatusChange}
                disabled={!isEditable}
                renderTag={(opt) => (
                  <span className={`status-badge ${opt.className}`}>{opt.label}</span>
                )}
              />
            </div>

            <div className="sidebar-field">
              <label>Priority</label>
              <span className={`priority-badge priority-${task.priority}`}>
                {task.priority === 'H' ? 'High' : task.priority === 'M' ? 'Medium' : 'Low'}
              </span>
            </div>

            {!isSubtask && (
              <div className="sidebar-field">
                <label>Assigned</label>
                <select value={assignedSession} onChange={e => handleAssignChange(e.target.value)} disabled={!isEditable}>
                  <option value="">Unassigned</option>
                  {sessions.filter(s => s.session_type === 'worker').map(s => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
            )}

            <div className="sidebar-field">
              <label>Project</label>
              {project ? (
                <Link to={`/projects/${project.id}`} className="sidebar-link">{project.name}</Link>
              ) : (
                <span className="sidebar-empty">None</span>
              )}
            </div>

            <hr className="sidebar-divider" />

            <div className="sidebar-field">
              <label>Created</label>
              <span className="sidebar-date">{new Date(task.created_at).toLocaleDateString()}</span>
            </div>

            {task.started_at && (
              <div className="sidebar-field">
                <label>Started</label>
                <span className="sidebar-date">{new Date(task.started_at).toLocaleDateString()}</span>
              </div>
            )}

            {task.completed_at && (
              <div className="sidebar-field">
                <label>Completed</label>
                <span className="sidebar-date">{new Date(task.completed_at).toLocaleDateString()}</span>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
