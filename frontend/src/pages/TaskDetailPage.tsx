import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Task, TaskLink, Notification } from '../api/types'
import { IconArrowLeft, IconPause, IconPlay, IconStop, IconRefresh } from '../components/common/Icons'
import { timeAgo, parseDate } from '../components/common/TimeAgo'
import ConfirmPopover from '../components/common/ConfirmPopover'
import TagDropdown from '../components/common/TagDropdown'
import Markdown from '../components/common/Markdown'
import './TaskDetailPage.css'

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
  const [notesExpanded, setNotesExpanded] = useState(true)
  const [showAddSubtask, setShowAddSubtask] = useState(false)
  const [newSubtaskTitle, setNewSubtaskTitle] = useState('')
  const [creatingSubtask, setCreatingSubtask] = useState(false)
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [notificationsExpanded, setNotificationsExpanded] = useState(true)
  const [showAssignModal, setShowAssignModal] = useState(false)
  const [workerPreview, setWorkerPreview] = useState('')
  const [workerActionPending, setWorkerActionPending] = useState(false)

  // Reset all editing states when navigating to a different task
  useEffect(() => {
    setIsEditingTitle(false)
    setIsEditingDesc(false)
    setIsEditingNotes(false)
    setShowAddLink(false)
    setEditingLinkUrl(null)
    setShowAddSubtask(false)
    setNewSubtaskTitle('')
    setNotesExpanded(true)
    setSubtasksExpanded(true)
    setShowAssignModal(false)
  }, [id])

  useEffect(() => {
    if (task) {
      // Only sync fields from server when NOT actively editing them
      if (!isEditingTitle) setEditTitle(task.title)
      if (!isEditingDesc) setEditDesc(task.description || '')
      if (!isEditingNotes) setEditNotes(task.notes || '')
      setStatus(task.status)
      setPriority(task.priority)
      setAssignedSession(task.assigned_session_id || '')
      setLinks(task.links || [])
      
      api<Task[]>(`/api/tasks?parent_task_id=${task.id}&include_subtask_stats=false`)
        .then(setSubtasks)
        .catch(() => setSubtasks([]))
      
      // Fetch notifications for this task
      api<Notification[]>(`/api/notifications?task_id=${task.id}&dismissed=false`)
        .then(setNotifications)
        .catch(() => setNotifications([]))
    }
  }, [task, isEditingTitle, isEditingDesc, isEditingNotes])

  const assignedWorker = sessions.find(s => s.id === task?.assigned_session_id)

  // Fetch worker terminal preview
  useEffect(() => {
    if (!assignedWorker) {
      setWorkerPreview('')
      return
    }
    
    async function fetchPreview() {
      try {
        const data = await api<{ content: string }>(`/api/sessions/${assignedWorker!.id}/preview`)
        setWorkerPreview(data.content || '')
      } catch {
        setWorkerPreview('')
      }
    }
    
    fetchPreview()
    const interval = setInterval(fetchPreview, 5000)
    return () => clearInterval(interval)
  }, [assignedWorker?.id])
  const isWorkerActive = assignedWorker && assignedWorker.status === 'working'
  const isEditable = !isWorkerActive
  const isSubtask = !!task?.parent_task_id
  const parentTask = isSubtask ? tasks.find(t => t.id === task?.parent_task_id) : null
  // For subtasks, get the parent task's assigned worker (read-only display)
  const parentAssignedWorker = parentTask ? sessions.find(s => s.id === parentTask.assigned_session_id) : null

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
    // If assigning to a worker (not unassigning), prepare it first
    if (sessionId) {
      try {
        await api(`/api/sessions/${sessionId}/prepare-for-task`, { method: 'POST' })
      } catch (err) {
        console.error('Failed to prepare worker:', err)
        // Continue with assignment even if prepare fails
      }
    }
    setAssignedSession(sessionId)
    await handleSaveField('assigned_session_id', sessionId || null)
  }

  const handleWorkerReconnectInModal = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api(`/api/sessions/${sessionId}/reconnect`, { method: 'POST' })
      refresh()
    } catch (err) {
      console.error('Failed to reconnect worker:', err)
    }
  }

  // Helper to check if a worker is connected
  const isWorkerConnected = (status: string) => {
    const disconnectedStatuses = ['disconnected', 'screen_detached', 'error', 'connecting']
    return !disconnectedStatuses.includes(status)
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

  const handleCreateSubtask = async () => {
    if (!task || !newSubtaskTitle.trim()) return
    setCreatingSubtask(true)
    try {
      await api('/api/tasks', {
        method: 'POST',
        body: JSON.stringify({
          project_id: task.project_id,
          parent_task_id: task.id,
          title: newSubtaskTitle.trim(),
          status: 'todo',
          priority: 'M'
        })
      })
      setNewSubtaskTitle('')
      setShowAddSubtask(false)
      // Refresh subtasks
      const updated = await api<Task[]>(`/api/tasks?parent_task_id=${task.id}&include_subtask_stats=false`)
      setSubtasks(updated)
      refresh()
    } catch (err) {
      console.error('Failed to create subtask:', err)
    } finally {
      setCreatingSubtask(false)
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

  const handleDismissNotification = async (notificationId: string) => {
    try {
      await api(`/api/notifications/${notificationId}/dismiss`, { method: 'POST' })
      setNotifications(prev => prev.filter(n => n.id !== notificationId))
    } catch (err) {
      console.error('Failed to dismiss notification:', err)
    }
  }

  async function handleWorkerPauseOrContinue(e: React.MouseEvent) {
    e.stopPropagation()
    if (!assignedWorker || workerActionPending) return
    setWorkerActionPending(true)
    try {
      const endpoint = assignedWorker.status === 'paused' ? 'continue' : 'pause'
      await api(`/api/sessions/${assignedWorker.id}/${endpoint}`, { method: 'POST' })
      refresh()
    } finally {
      setWorkerActionPending(false)
    }
  }

  async function handleWorkerStop() {
    if (!assignedWorker || workerActionPending) return
    setWorkerActionPending(true)
    try {
      await api(`/api/sessions/${assignedWorker.id}/stop`, { method: 'POST' })
      refresh()
    } finally {
      setWorkerActionPending(false)
    }
  }

  async function handleWorkerReconnect(e: React.MouseEvent) {
    e.stopPropagation()
    if (!assignedWorker || workerActionPending) return
    setWorkerActionPending(true)
    try {
      await api(`/api/sessions/${assignedWorker.id}/reconnect`, { method: 'POST' })
      refresh()
    } finally {
      setWorkerActionPending(false)
    }
  }

  const formatNotificationTime = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  const getNotificationTypeIcon = (type: string) => {
    switch (type) {
      case 'pr_comment': return '💬'
      case 'warning': return '⚠️'
      default: return 'ℹ️'
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
  const activeSubtasks = subtasks.filter(st => st.status === 'in_progress').length
  const blockedSubtasks = subtasks.filter(st => st.status === 'blocked').length
  const totalSubtasks = subtasks.length

  return (
    <div className="task-detail-page">
      {/* Header with back button */}
      <div className="tdp-top-bar">
        <button className="tdp-back-btn" onClick={() => navigate(-1)} title="Go back">
          <IconArrowLeft size={16} />
        </button>
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

            {/* Notes Section */}
            <div className="tdp-notes-section">
              <div className="tdp-notes-header">
                <button 
                  className="tdp-notes-toggle"
                  onClick={() => setNotesExpanded(!notesExpanded)}
                  title={notesExpanded ? 'Collapse' : 'Expand'}
                >
                  <span className={`expand-icon ${notesExpanded ? 'expanded' : ''}`}>▶</span>
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
                      ✓
                    </button>
                    <button
                      className="tdp-action-btn cancel"
                      onClick={() => { setIsEditingNotes(false); setEditNotes(task.notes || '') }}
                      title="Discard"
                    >
                      ✕
                    </button>
                  </div>
                ) : isEditable && notesExpanded && (
                  <button className="tdp-edit-btn" onClick={() => setIsEditingNotes(true)}>Edit</button>
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
                    <Markdown>{task.notes}</Markdown>
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

          {/* Worker Preview Card - only show when worker is assigned */}
          {assignedWorker && (
            <div
              className={`tdp-card tdp-worker-preview-card status-${assignedWorker.status}`}
              onClick={() => navigate(`/workers/${assignedWorker.id}`)}
            >
              <div className="tdp-worker-preview-header">
                <div className="tdp-worker-preview-left">
                  <span className={`status-indicator ${assignedWorker.status}`} />
                  <span className="tdp-worker-preview-name">
                    {assignedWorker.name}
                  </span>
                  {assignedWorker.host.includes('/') && <span className="wc-type-tag rdev">rdev</span>}
                  <span className={`status-badge small ${assignedWorker.status}`}>{assignedWorker.status}</span>
                </div>
                <div className="tdp-worker-preview-actions">
                  {(assignedWorker.status === 'disconnected' || assignedWorker.status === 'screen_detached' || assignedWorker.status === 'error') ? (
                    <button
                      className="wc-action-btn reconnect"
                      onClick={handleWorkerReconnect}
                      disabled={workerActionPending}
                      title="Reconnect"
                    >
                      <IconRefresh size={14} />
                    </button>
                  ) : (
                    <>
                      <button
                        className={`wc-action-btn ${assignedWorker.status === 'paused' ? 'continue' : 'pause'}`}
                        onClick={handleWorkerPauseOrContinue}
                        disabled={workerActionPending || assignedWorker.status === 'idle'}
                        title={assignedWorker.status === 'paused' ? 'Continue' : 'Pause'}
                      >
                        {assignedWorker.status === 'paused' ? <IconPlay size={14} /> : <IconPause size={14} />}
                      </button>
                      <ConfirmPopover
                        message={`Stop worker "${assignedWorker.name}" and clear context?`}
                        confirmLabel="Stop"
                        onConfirm={handleWorkerStop}
                        variant="danger"
                      >
                        {({ onClick }) => (
                          <button
                            className="wc-action-btn stop"
                            onClick={(e) => { e.stopPropagation(); onClick(e); }}
                            disabled={workerActionPending || assignedWorker.status === 'idle'}
                            title="Stop and clear"
                          >
                            <IconStop size={14} />
                          </button>
                        )}
                      </ConfirmPopover>
                    </>
                  )}
                </div>
              </div>
              <div className="tdp-worker-preview-terminal">
                <pre>{workerPreview ? workerPreview.split('\n').slice(-15).join('\n') : 'No terminal output yet...'}</pre>
              </div>
              <div className="tdp-worker-preview-footer">
                <span className="tdp-worker-preview-activity">
                  {assignedWorker.last_status_changed_at ? timeAgo(assignedWorker.last_status_changed_at) : 'just now'}
                </span>
              </div>
            </div>
          )}

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
                          <ConfirmPopover
                            message="Remove this link?"
                            confirmLabel="Remove"
                            onConfirm={() => handleRemoveLink(link.url)}
                            variant="danger"
                          >
                            {({ onClick }) => (
                              <button className="link-remove" onClick={onClick} title="Remove">×</button>
                            )}
                          </ConfirmPopover>
                        </div>
                      )}
                    </div>
                  )
                ))}
              </div>
            )}
          </div>

          {/* Subtasks Card */}
          <div className="tdp-card tdp-subtasks-card">
            <div className="tdp-card-header">
              <h3 className="clickable" onClick={() => setSubtasksExpanded(!subtasksExpanded)}>
                <span className={`expand-icon ${subtasksExpanded ? 'expanded' : ''}`}>▶</span>
                Subtasks
                {subtasks.length > 0 && (
                  <>
                    <span className="count">({doneSubtasks}/{totalSubtasks})</span>
                    <div className="tdp-progress-inline">
                      {doneSubtasks > 0 && <div className="seg done" style={{ width: `${(doneSubtasks / totalSubtasks) * 100}%` }} />}
                      {activeSubtasks > 0 && <div className="seg active" style={{ width: `${(activeSubtasks / totalSubtasks) * 100}%` }} />}
                      {blockedSubtasks > 0 && <div className="seg blocked" style={{ width: `${(blockedSubtasks / totalSubtasks) * 100}%` }} />}
                    </div>
                  </>
                )}
              </h3>
              {isEditable && !showAddSubtask && (
                <button className="tdp-edit-btn" onClick={() => setShowAddSubtask(true)}>+ Add</button>
              )}
            </div>
            {showAddSubtask && (
              <div className="tdp-subtask-form">
                <input
                  type="text"
                  placeholder="Subtask title..."
                  value={newSubtaskTitle}
                  onChange={e => setNewSubtaskTitle(e.target.value)}
                  autoFocus
                  onKeyDown={e => {
                    if (e.key === 'Enter' && newSubtaskTitle.trim()) handleCreateSubtask()
                    if (e.key === 'Escape') { setShowAddSubtask(false); setNewSubtaskTitle('') }
                  }}
                />
                <div className="tdp-inline-actions">
                  <button 
                    className="tdp-action-btn save" 
                    onClick={handleCreateSubtask} 
                    disabled={!newSubtaskTitle.trim() || creatingSubtask}
                    title="Create"
                  >
                    ✓
                  </button>
                  <button 
                    className="tdp-action-btn cancel" 
                    onClick={() => { setShowAddSubtask(false); setNewSubtaskTitle('') }}
                    title="Cancel"
                  >
                    ✕
                  </button>
                </div>
              </div>
            )}
            {subtasksExpanded && subtasks.length > 0 && (
              <div className="tdp-subtasks-list">
                {subtasks.map(st => (
                  <div key={st.id} className="tdp-subtask-row">
                    <Link to={`/tasks/${st.id}`} className="tdp-subtask-item">
                      <span className={`subtask-status status-${st.status}`} />
                      <span className="subtask-key">{st.task_key}</span>
                      <span className="subtask-title">{st.title}</span>
                    </Link>
                    {st.links && st.links.length > 0 && (
                      <a
                        href={st.links[0].url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="subtask-link-btn"
                        onClick={e => e.stopPropagation()}
                        title={st.links.length > 1 ? `${st.links[0].url} (+${st.links.length - 1} more)` : st.links[0].url}
                      >
                        ↗{st.links.length > 1 && <span className="link-more">...</span>}
                      </a>
                    )}
                  </div>
                ))}
              </div>
            )}
            {subtasksExpanded && subtasks.length === 0 && !showAddSubtask && (
              <p className="tdp-empty-text">No subtasks yet</p>
            )}
          </div>

          {/* Notifications Card */}
          {notifications.length > 0 && (
            <div className="tdp-card tdp-notifications-card">
              <div className="tdp-card-header">
                <h3 className="clickable" onClick={() => setNotificationsExpanded(!notificationsExpanded)}>
                  <span className={`expand-icon ${notificationsExpanded ? 'expanded' : ''}`}>▶</span>
                  Notifications
                  <span className="count notification-count">({notifications.length})</span>
                </h3>
              </div>
              {notificationsExpanded && (
                <div className="tdp-notifications-list">
                  {notifications.map(n => (
                    <div key={n.id} className={`tdp-notification-item ${n.notification_type}`}>
                      <span className="notification-icon">{getNotificationTypeIcon(n.notification_type)}</span>
                      <div className="notification-content">
                        <div className="notification-header">
                          <span className={`notification-type ${n.notification_type}`}>{n.notification_type}</span>
                          <span className="notification-time">{formatNotificationTime(n.created_at)}</span>
                        </div>
                        <p className="notification-message">{n.message}</p>
                        <div className="notification-actions">
                          {n.link_url && (
                            <a href={n.link_url} target="_blank" rel="noopener noreferrer" className="btn btn-link btn-sm">
                              Open Link ↗
                            </a>
                          )}
                          <button className="btn btn-secondary btn-sm" onClick={() => handleDismissNotification(n.id)}>
                            Dismiss
                          </button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

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
              <TagDropdown
                value={priority}
                options={PRIORITY_OPTIONS}
                onChange={handlePriorityChange}
                disabled={!isEditable}
                renderTag={(opt) => (
                  <span className={`priority-badge ${opt.className}`}>{opt.label}</span>
                )}
              />
            </div>

            <div className="sidebar-field">
              <label>Assigned</label>
              <div className="tdp-worker-field">
                {isSubtask ? (
                  // Subtasks show parent's worker (read-only)
                  parentAssignedWorker ? (
                    <Link to={`/workers/${parentAssignedWorker.id}`} className={`tdp-worker-link status-${parentAssignedWorker.status}`}>
                      {parentAssignedWorker.name}
                    </Link>
                  ) : (
                    <span className="sidebar-empty">Unassigned</span>
                  )
                ) : (
                  // Regular tasks show their own worker with edit button
                  <>
                    {assignedWorker ? (
                      <Link to={`/workers/${assignedWorker.id}`} className={`tdp-worker-link status-${assignedWorker.status}`}>
                        {assignedWorker.name}
                      </Link>
                    ) : (
                      <span className="sidebar-empty">Unassigned</span>
                    )}
                    {isEditable && (
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

            {/* Worker Assignment Modal */}
            {showAssignModal && (
              <div className="tdp-modal-overlay" onClick={() => setShowAssignModal(false)}>
                <div className="tdp-modal" onClick={e => e.stopPropagation()}>
                  <div className="tdp-modal-header">
                    <h3>{assignedWorker ? 'Reassign Worker' : 'Assign Worker'}</h3>
                    <button className="tdp-modal-close" onClick={() => setShowAssignModal(false)}>×</button>
                  </div>
                  <div className="tdp-modal-hint">
                    ⚡ After assignment, the worker will immediately start working on this task.
                  </div>
                  <div className="tdp-modal-body">
                    <div className="tdp-worker-list">
                      {assignedSession && (
                        <button
                          className="tdp-worker-option unassign"
                          onClick={async () => {
                            await handleAssignChange('')
                            setShowAssignModal(false)
                          }}
                        >
                          <span className="worker-status-dot" />
                          <span className="worker-name">Unassign</span>
                        </button>
                      )}
                      {/* Connected workers - can be assigned */}
                      {sessions.filter(s => {
                        if (s.session_type !== 'worker') return false
                        if (!isWorkerConnected(s.status)) return false
                        // Allow current task's assigned worker
                        if (s.id === assignedSession) return true
                        // Exclude workers assigned to other tasks
                        const assignedToOther = tasks.some(t => t.id !== task?.id && t.assigned_session_id === s.id)
                        return !assignedToOther
                      }).map(s => (
                        <button
                          key={s.id}
                          className={`tdp-worker-option ${s.id === assignedSession ? 'selected' : ''}`}
                          onClick={async () => {
                            if (s.id !== assignedSession) {
                              await handleAssignChange(s.id)
                            }
                            setShowAssignModal(false)
                          }}
                        >
                          <span className={`worker-status-dot status-${s.status}`} />
                          <span className="worker-name">{s.name}</span>
                          <span className={`worker-status-label status-${s.status}`}>{s.status}</span>
                          {s.id === assignedSession && <span className="worker-current">Current</span>}
                        </button>
                      ))}
                      {/* Disconnected workers - show with reconnect button */}
                      {sessions.filter(s => {
                        if (s.session_type !== 'worker') return false
                        if (isWorkerConnected(s.status)) return false
                        // Show disconnected workers that are either unassigned or assigned to this task
                        const assignedToOther = tasks.some(t => t.id !== task?.id && t.assigned_session_id === s.id)
                        return !assignedToOther
                      }).length > 0 && (
                        <div className="tdp-worker-section-divider">
                          <span>Disconnected</span>
                        </div>
                      )}
                      {sessions.filter(s => {
                        if (s.session_type !== 'worker') return false
                        if (isWorkerConnected(s.status)) return false
                        const assignedToOther = tasks.some(t => t.id !== task?.id && t.assigned_session_id === s.id)
                        return !assignedToOther
                      }).map(s => (
                        <div
                          key={s.id}
                          className="tdp-worker-option disabled"
                        >
                          <span className={`worker-status-dot status-${s.status}`} />
                          <span className="worker-name">{s.name}</span>
                          <span className={`worker-status-label status-${s.status}`}>{s.status}</span>
                          <button
                            className="tdp-worker-reconnect-btn"
                            onClick={(e) => handleWorkerReconnectInModal(s.id, e)}
                            title="Reconnect worker"
                          >
                            <IconRefresh size={12} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
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
              <span className="sidebar-date">{parseDate(task.created_at).toLocaleString()}</span>
            </div>

            <div className="sidebar-field">
              <label>Updated</label>
              <span className="sidebar-date">{parseDate(task.updated_at).toLocaleString()}</span>
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
                      {deleting ? 'Deleting...' : 'Delete Task'}
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
