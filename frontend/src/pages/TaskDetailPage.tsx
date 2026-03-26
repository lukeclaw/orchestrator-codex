import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { useNotify } from '../context/NotificationContext'
import { api } from '../api/client'
import {
  IconPencil,
  IconTrash,
} from '../components/common/Icons'
import { timeAgo, parseDate } from '../components/common/TimeAgo'
import TagDropdown from '../components/common/TagDropdown'
import ConfirmPopover from '../components/common/ConfirmPopover'
import Markdown from '../components/common/Markdown'
import ProviderBadge from '../components/common/ProviderBadge'
import TaskLinksCard from '../components/tasks/TaskLinksCard'
import TaskSubtasksCard from '../components/tasks/TaskSubtasksCard'
import TaskNotificationsCard from '../components/tasks/TaskNotificationsCard'
import TaskWorkerPreview from '../components/tasks/TaskWorkerPreview'
import WorkerAssignModal from '../components/tasks/WorkerAssignModal'
import './TaskDetailPage.css'
import './NotificationsPage.css'

const STATUS_OPTIONS = [
  { value: 'todo', label: 'To Do', className: 'status-todo' },
  { value: 'in_progress', label: 'In Progress', className: 'status-in_progress' },
  { value: 'done', label: 'Done', className: 'status-done' },
  { value: 'blocked', label: 'Blocked', className: 'status-blocked' },
]

const PRIORITY_OPTIONS = [
  { value: 'H', label: 'High', className: 'priority-H' },
  { value: 'M', label: 'Medium', className: 'priority-M' },
  { value: 'L', label: 'Low', className: 'priority-L' },
]

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { tasks, sessions, projects, refresh } = useApp()
  const notify = useNotify()

  const task = tasks.find(t => t.id === id) || null
  const project = task ? projects.find(p => p.id === task.project_id) : null

  // Editing states
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [isEditingDesc, setIsEditingDesc] = useState(false)
  const [isEditingNotes, setIsEditingNotes] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [editNotes, setEditNotes] = useState('')
  const [status, setStatus] = useState('')
  const [priority, setPriority] = useState('')
  const [assignedSession, setAssignedSession] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [notesExpanded, setNotesExpanded] = useState(true)
  const [showAssignModal, setShowAssignModal] = useState(false)
  const [assigningWorker, setAssigningWorker] = useState(false)

  // Reset editing states when navigating to a different task
  useEffect(() => {
    setIsEditingTitle(false)
    setIsEditingDesc(false)
    setIsEditingNotes(false)
    setNotesExpanded(true)
    setShowAssignModal(false)
  }, [id])

  // Sync fields from server (polling)
  useEffect(() => {
    if (task) {
      if (!isEditingTitle) setEditTitle(task.title)
      if (!isEditingDesc) setEditDesc(task.description || '')
      if (!isEditingNotes) setEditNotes(task.notes || '')
      setStatus(task.status)
      setPriority(task.priority)
      setAssignedSession(task.assigned_session_id || '')
    }
  }, [task, isEditingTitle, isEditingDesc, isEditingNotes])

  const assignedWorker = sessions.find(s => s.id === task?.assigned_session_id)
  const isWorkerActive = assignedWorker && assignedWorker.status === 'working'
  const isEditable = !isWorkerActive
  const isSubtask = !!task?.parent_task_id
  const parentTask = isSubtask ? tasks.find(t => t.id === task?.parent_task_id) : null
  const parentAssignedWorker = parentTask ? sessions.find(s => s.id === parentTask.assigned_session_id) : null

  const handleSaveField = async (field: string, value: unknown) => {
    if (!task) return
    await api(`/api/tasks/${task.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ [field]: value })
    })
    refresh()
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

  const handleNotesSave = async () => {
    if (editNotes === (task?.notes || '')) {
      setIsEditingNotes(false)
      return
    }
    await handleSaveField('notes', editNotes || null)
    setIsEditingNotes(false)
  }

  const handleStatusChange = async (newStatus: string) => {
    setStatus(newStatus)
    await handleSaveField('status', newStatus)
  }

  const handlePriorityChange = async (newPriority: string) => {
    setPriority(newPriority)
    await handleSaveField('priority', newPriority)
  }

  const handleAssignChange = async (sessionId: string) => {
    const worker = sessions.find(s => s.id === sessionId)
    setAssigningWorker(!!sessionId)
    try {
      if (sessionId) {
        try {
          await api(`/api/sessions/${sessionId}/prepare-for-task`, { method: 'POST' })
        } catch (err) {
          console.error('Failed to prepare worker:', err)
        }
      }
      setAssignedSession(sessionId)
      await handleSaveField('assigned_session_id', sessionId || null)
      if (sessionId && worker) {
        notify(`Worker ${worker.name} assigned and notified`, 'success')
      }
    } finally {
      setAssigningWorker(false)
    }
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

  return (
    <div className="task-detail-page">
      {/* Header with back button */}
      <div className="tdp-top-bar">
        <nav className="tdp-breadcrumb">
          {project ? (
            <Link to={`/projects/${project.id}`}>{project.name}</Link>
          ) : (
            <span>No Project</span>
          )}
          <span className="breadcrumb-sep">&gt;</span>
          {parentTask && (
            <>
              <Link to={`/tasks/${parentTask.id}`}>{parentTask.task_key}</Link>
              <span className="breadcrumb-sep">&gt;</span>
            </>
          )}
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
                      &#10003;
                    </button>
                    <button
                      className="tdp-action-btn cancel"
                      onClick={() => { setIsEditingTitle(false); setEditTitle(task.title) }}
                      title="Discard"
                    >
                      &#10005;
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

            {/* Description Section */}
            <div className="tdp-desc-section">
              <div className="tdp-desc-header">
                <label>Description</label>
                {isEditingDesc ? (
                  <div className="tdp-inline-actions">
                    <button
                      className="tdp-action-btn save"
                      onClick={handleDescSave}
                      disabled={editDesc === (task.description || '')}
                      title="Save"
                    >
                      &#10003;
                    </button>
                    <button
                      className="tdp-action-btn cancel"
                      onClick={() => { setIsEditingDesc(false); setEditDesc(task.description || '') }}
                      title="Discard"
                    >
                      &#10005;
                    </button>
                  </div>
                ) : isEditable && task.description && (
                  <button className="tdp-edit-btn" onClick={() => setIsEditingDesc(true)}><IconPencil size={12} /> Edit</button>
                )}
              </div>
              {isEditingDesc ? (
                <div className="tdp-desc-edit">
                  <textarea
                    value={editDesc}
                    onChange={e => setEditDesc(e.target.value)}
                    placeholder="Add a description (supports markdown)..."
                    rows={Math.max(3, (task.description || '').split('\n').length)}
                    autoFocus
                    onKeyDown={e => {
                      if (e.key === 'Escape') {
                        setIsEditingDesc(false)
                        setEditDesc(task.description || '')
                      }
                    }}
                  />
                </div>
              ) : task.description ? (
                <div className="tdp-desc-content">
                  <Markdown>{task.description}</Markdown>
                </div>
              ) : (
                <p
                  className={`tdp-desc-empty ${isEditable ? 'editable' : ''}`}
                  onClick={() => isEditable && setIsEditingDesc(true)}
                  title={isEditable ? 'Click to edit' : undefined}
                >
                  Add a description...
                </p>
              )}
            </div>

            {isWorkerActive && (
              <div className="tdp-worker-active">
                Worker <strong>{assignedWorker?.name}</strong> is working on this task — view-only mode
              </div>
            )}

            {/* Notes Section */}
            <div className="tdp-notes-section">
              <div className="tdp-notes-header">
                <button
                  className="tdp-notes-toggle"
                  onClick={() => setNotesExpanded(!notesExpanded)}
                  title={notesExpanded ? 'Collapse' : 'Expand'}
                >
                  <span className={`expand-icon ${notesExpanded ? 'expanded' : ''}`}>&#9654;</span>
                </button>
                <label>Notes</label>
                {isEditingNotes ? (
                  <div className="tdp-inline-actions">
                    <button
                      className="tdp-action-btn save"
                      onClick={handleNotesSave}
                      disabled={editNotes === (task.notes || '')}
                      title="Save"
                    >
                      &#10003;
                    </button>
                    <button
                      className="tdp-action-btn cancel"
                      onClick={() => { setIsEditingNotes(false); setEditNotes(task.notes || '') }}
                      title="Discard"
                    >
                      &#10005;
                    </button>
                  </div>
                ) : isEditable && notesExpanded && (
                  <button className="tdp-edit-btn" onClick={() => setIsEditingNotes(true)}><IconPencil size={12} /> Edit</button>
                )}
                {!notesExpanded && task.notes && (
                  <span className="tdp-notes-preview">{task.notes.split('\n')[0]}</span>
                )}
              </div>
              {notesExpanded && (
                isEditingNotes ? (
                  <div className="tdp-desc-edit">
                    <textarea
                      value={editNotes}
                      onChange={e => setEditNotes(e.target.value)}
                      placeholder="Add notes about this task (supports markdown)..."
                      rows={Math.max(5, (task.notes || '').split('\n').length)}
                      autoFocus
                      onKeyDown={e => {
                        if (e.key === 'Escape') {
                          setIsEditingNotes(false)
                          setEditNotes(task.notes || '')
                        }
                      }}
                    />
                  </div>
                ) : task.notes ? (
                  <div className="tdp-notes-content">
                    <Markdown expandable>{task.notes}</Markdown>
                  </div>
                ) : (
                  <p
                    className={`tdp-notes-empty ${isEditable ? 'editable' : ''}`}
                    onClick={() => isEditable && setIsEditingNotes(true)}
                    title={isEditable ? 'Click to edit' : undefined}
                  >
                    No notes yet...
                  </p>
                )
              )}
            </div>
          </div>

          {/* Worker Preview Card */}
          {assignedWorker && (
            <TaskWorkerPreview worker={assignedWorker} onRefresh={refresh} />
          )}

          {/* Links Card */}
          <TaskLinksCard key={task.id} task={task} isEditable={isEditable} onSaveField={handleSaveField} />

          {/* Subtasks Card (hidden for subtasks to prevent nesting) */}
          {!isSubtask && (
            <TaskSubtasksCard key={`sub-${task.id}`} task={task} isEditable={isEditable} refresh={refresh} />
          )}

          {/* Notifications Card */}
          <TaskNotificationsCard key={`notif-${task.id}`} taskId={task.id} />

        </main>

        {/* Sidebar */}
        <aside className="tdp-sidebar">
          <div className="tdp-sidebar-card">
            <div className="sidebar-section">
              <div className="sidebar-field">
                <label>Status</label>
                <TagDropdown
                  value={status}
                  options={STATUS_OPTIONS}
                  onChange={handleStatusChange}
                  disabled={!isEditable}
                  renderTag={(opt) => (
                    <span className={`sidebar-tag ${opt.className}`}>{opt.label}</span>
                  )}
                />
              </div>

              <div className="sidebar-field">
                <label>Priority</label>
                <TagDropdown
                  value={priority}
                  options={PRIORITY_OPTIONS}
                  onChange={handlePriorityChange}
                  disabled={!isEditable}
                  renderTag={(opt) => (
                    <span className={`sidebar-tag ${opt.className}`}>{opt.label}</span>
                  )}
                />
              </div>
            </div>

            <div className="sidebar-section">
              <div className="sidebar-field">
                <label>Assigned</label>
                <div className="tdp-worker-field">
                  {isSubtask ? (
                    parentAssignedWorker ? (
                      <Link to={`/workers/${parentAssignedWorker.id}`} className={`tdp-worker-link status-${parentAssignedWorker.status}`}>
                        {parentAssignedWorker.name}
                        <ProviderBadge provider={parentAssignedWorker.provider} compact />
                      </Link>
                    ) : (
                      <span className="sidebar-empty">Assign the parent task</span>
                    )
                  ) : (
                    <>
                      {assigningWorker ? (
                        <span className="sidebar-assigning">Assigning...</span>
                      ) : assignedWorker ? (
                        <Link to={`/workers/${assignedWorker.id}`} className={`tdp-worker-link status-${assignedWorker.status}`}>
                          {assignedWorker.name}
                          <ProviderBadge provider={assignedWorker.provider} compact />
                        </Link>
                      ) : (
                        <span className="sidebar-empty">Unassigned</span>
                      )}
                      {isEditable && !assigningWorker && (
                        <button
                          className="tdp-assign-icon-btn"
                          onClick={() => setShowAssignModal(true)}
                          title={assignedWorker ? 'Reassign worker' : 'Assign worker'}
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                            <circle cx="9" cy="7" r="4"/>
                            <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                            <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                          </svg>
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>

              <div className="sidebar-field">
                <label>Project</label>
                {project ? (
                  <Link to={`/projects/${project.id}`} className="sidebar-link">{project.name}</Link>
                ) : (
                  <span className="sidebar-empty">None</span>
                )}
              </div>
            </div>

            {showAssignModal && (
              <WorkerAssignModal
                task={task}
                assignedSession={assignedSession}
                sessions={sessions}
                tasks={tasks}
                onAssign={handleAssignChange}
                onClose={() => setShowAssignModal(false)}
              />
            )}

            <div className="sidebar-section">
              <div className="sidebar-meta-row">
                <div className="sidebar-meta-item">
                  <label>Created</label>
                  <span className="sidebar-date" data-tooltip={parseDate(task.created_at).toLocaleString()}>{timeAgo(task.created_at)}</span>
                </div>
                <div className="sidebar-meta-item">
                  <label>Updated</label>
                  <span className="sidebar-date" data-tooltip={parseDate(task.updated_at).toLocaleString()}>{timeAgo(task.updated_at)}</span>
                </div>
              </div>
            </div>

            {isEditable && (
              <div className="tdp-sidebar-actions">
                <ConfirmPopover
                  message={`Delete "${task.title}"?`}
                  confirmLabel="Delete"
                  onConfirm={handleDelete}
                  variant="danger"
                >
                  {({ onClick }) => (
                    <button className="btn btn-danger" onClick={onClick} disabled={deleting}>
                      <IconTrash size={14} /> {deleting ? 'Deleting...' : 'Delete Task'}
                    </button>
                  )}
                </ConfirmPopover>
              </div>
            )}
          </div>
        </aside>
      </div>

    </div>
  )
}
