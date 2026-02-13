import { useEffect, useState, useCallback } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import type { ContextItem, Task } from '../api/types'
import { api } from '../api/client'
import { useApp } from '../context/AppContext'
import TaskBoard from '../components/tasks/TaskBoard'
import TaskTable from '../components/tasks/TaskTable'
import TaskForm from '../components/tasks/TaskForm'
import ContextModal from '../components/context/ContextModal'
import ProjectEditModal from '../components/projects/ProjectEditModal'
import WorkerCard from '../components/workers/WorkerCard'
import { IconArrowLeft } from '../components/common/Icons'
import { parseDate } from '../components/common/TimeAgo'
import './ProjectDetailPage.css'

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  
  // Use shared state from AppContext for sessions, tasks, projects
  const { sessions, tasks: allTasks, projects, refresh, removeSession } = useApp()
  
  // Derive data from shared state
  const project = projects.find(p => p.id === id) || null
  const tasks = allTasks.filter(t => t.project_id === id)
  
  // Local state for page-specific data
  const [contextItems, setContextItems] = useState<ContextItem[]>([])
  const [error, setError] = useState('')
  const [showTaskForm, setShowTaskForm] = useState(false)
  const [taskViewMode, setTaskViewMode] = useState<'board' | 'table'>('board')
  const [selectedContext, setSelectedContext] = useState<ContextItem | null>(null)
  const [showNewContext, setShowNewContext] = useState(false)
  const [showEditProject, setShowEditProject] = useState(false)

  // Load context items (not in shared state)
  const loadContext = useCallback(async () => {
    if (!id) return
    try {
      const ctx = await api<ContextItem[]>(`/api/context?project_id=${id}`).catch(() => [])
      setContextItems(ctx)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load context')
    }
  }, [id])

  useEffect(() => { loadContext() }, [loadContext])

  async function createTask(body: { project_id: string; title: string; description?: string; priority?: string }) {
    await api('/api/tasks', { method: 'POST', body: JSON.stringify(body) })
    refresh()
  }

  async function updateTask(taskId: string, body: Partial<Task>) {
    await api(`/api/tasks/${taskId}`, { method: 'PATCH', body: JSON.stringify(body) })
    refresh()
  }

  async function deleteTask(taskId: string) {
    await api(`/api/tasks/${taskId}`, { method: 'DELETE' })
    refresh()
  }

  function handleTaskClick(task: Task) {
    navigate(`/tasks/${task.id}`)
  }

  async function handleProjectUpdate(projectId: string, data: { name?: string; description?: string; status?: string; target_date?: string }) {
    await api(`/api/projects/${projectId}`, { method: 'PATCH', body: JSON.stringify(data) })
    refresh()
  }

  async function handleProjectDelete(projectId: string) {
    await api(`/api/projects/${projectId}`, { method: 'DELETE' })
    navigate('/projects')
  }

  if (error) {
    return (
      <div className="error-page">
        <p>{error}</p>
        <Link to="/projects" className="btn btn-secondary">Back to Projects</Link>
      </div>
    )
  }

  if (!project) {
    return <p className="empty-state">Loading project...</p>
  }

  // Filter out subtasks - only show parent tasks on project page
  const parentTasks = tasks.filter(t => !t.parent_task_id)
  const doneTasks = parentTasks.filter(t => t.status === 'done').length
  const assignedSessions = sessions.filter(s =>
    parentTasks.some(t => t.assigned_session_id === s.id)
  )

  function handleWorkerRemove(sessionId: string) {
    removeSession(sessionId)
  }

  return (
    <div className="project-detail">
      <div className="pd-header">
        <div className="pd-title-row">
          <button className="pd-back-btn" onClick={() => navigate(-1)} title="Go back">
            <IconArrowLeft size={16} />
          </button>
          <h1>{project.name}</h1>
          <button
            type="button"
            className="pd-edit-btn"
            onClick={() => setShowEditProject(true)}
            title="Edit project"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
          </button>
          <span className={`status-badge ${project.status}`}>{project.status}</span>
        </div>
        {project.description && <p className="pd-desc">{project.description}</p>}
      </div>

      {project.target_date && (
        <div className="pd-meta">
          <span className="pd-date">Target: {new Date(project.target_date).toLocaleDateString()}</span>
        </div>
      )}

      {/* Tasks */}
      <section className="pd-section">
        <div className="pd-section-header">
          <div className="pd-section-title-row">
            <h2>Tasks</h2>
            <span className="pd-progress-text">
              {doneTasks}/{parentTasks.length} ({parentTasks.length > 0 ? Math.round((doneTasks / parentTasks.length) * 100) : 0}%)
            </span>
            <div className="toggle-group toggle-sm">
              <button
                type="button"
                className={`toggle-btn${taskViewMode === 'board' ? ' active' : ''}`}
                onClick={() => setTaskViewMode('board')}
              >
                Board
              </button>
              <button
                type="button"
                className={`toggle-btn${taskViewMode === 'table' ? ' active' : ''}`}
                onClick={() => setTaskViewMode('table')}
              >
                Table
              </button>
            </div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => setShowTaskForm(true)}>
            + Add Task
          </button>
        </div>
        {taskViewMode === 'board' ? (
          <TaskBoard tasks={parentTasks} onTaskClick={handleTaskClick} />
        ) : (
          <TaskTable tasks={parentTasks} onTaskClick={handleTaskClick} />
        )}
      </section>

      {/* Workers */}
      <section className="pd-section">
        <h2>Workers ({assignedSessions.length})</h2>
        {assignedSessions.length > 0 ? (
          <div className="pd-worker-grid">
            {assignedSessions.map(s => (
              <WorkerCard
                key={s.id}
                session={s}
                assignedTask={parentTasks.find(t => t.assigned_session_id === s.id) || null}
                onRemove={handleWorkerRemove}
              />
            ))}
          </div>
        ) : (
          <p className="empty-state">No workers assigned to tasks in this project</p>
        )}
      </section>

      {/* Context */}
      <section className="pd-section">
        <div className="pd-section-header">
          <h2>Context ({contextItems.length})</h2>
          <button className="btn btn-primary btn-sm" onClick={() => setShowNewContext(true)}>
            + Add Context
          </button>
        </div>
        {contextItems.length === 0 ? (
          <p className="pd-empty">No context items for this project.</p>
        ) : (
          <div className="pd-context-list">
            {contextItems.map(c => (
              <div
                key={c.id}
                className="pd-context-item clickable"
                onClick={async () => {
                  // Fetch full content if not loaded
                  if (!c.content) {
                    const full = await api<ContextItem>(`/api/context/${c.id}`)
                    setSelectedContext(full)
                  } else {
                    setSelectedContext(c)
                  }
                }}
              >
                <div className="pd-context-header">
                  {c.category && <span className={`cm-badge cm-cat-${c.category}`}>{c.category}</span>}
                  <strong>{c.title}</strong>
                  <span className="pd-context-time">{c.updated_at ? parseDate(c.updated_at).toLocaleDateString() : ''}</span>
                </div>
                <p className="pd-context-preview">{c.description || (c.content?.slice(0, 150) || '') + ((c.content?.length || 0) > 150 ? '...' : '')}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      <TaskForm
        open={showTaskForm}
        onClose={() => setShowTaskForm(false)}
        onSubmit={createTask}
        projects={project ? [project] : []}
        defaultProjectId={id}
      />

      <ContextModal
        context={selectedContext}
        projectId={id}
        isNew={showNewContext}
        onClose={() => {
          setSelectedContext(null)
          setShowNewContext(false)
        }}
        onSave={async (body) => {
          if (body.id) {
            await api(`/api/context/${body.id}`, {
              method: 'PATCH',
              body: JSON.stringify(body),
            })
          } else {
            await api('/api/context', {
              method: 'POST',
              body: JSON.stringify(body),
            })
          }
          loadContext()
        }}
        onDelete={async (ctxId) => {
          await api(`/api/context/${ctxId}`, { method: 'DELETE' })
          loadContext()
        }}
      />

      <ProjectEditModal
        project={showEditProject ? project : null}
        onClose={() => setShowEditProject(false)}
        onUpdate={handleProjectUpdate}
        onDelete={handleProjectDelete}
      />
    </div>
  )
}
