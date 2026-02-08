import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import type { Project, Task, PullRequest, Session, ContextItem } from '../api/types'
import { api } from '../api/client'
import TaskBoard from '../components/tasks/TaskBoard'
import TaskTable from '../components/tasks/TaskTable'
import TaskForm from '../components/tasks/TaskForm'
import TaskDetailModal from '../components/tasks/TaskDetailModal'
import ContextModal from '../components/context/ContextModal'
import WorkerCard from '../components/workers/WorkerCard'
import ProgressBar from '../components/common/ProgressBar'
import './ProjectDetailPage.css'

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [project, setProject] = useState<Project | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [prs, setPrs] = useState<PullRequest[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [contextItems, setContextItems] = useState<ContextItem[]>([])
  const [error, setError] = useState('')
  const [showTaskForm, setShowTaskForm] = useState(false)
  const [taskViewMode, setTaskViewMode] = useState<'board' | 'table'>('board')
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [selectedContext, setSelectedContext] = useState<ContextItem | null>(null)
  const [showNewContext, setShowNewContext] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    try {
      const [p, t, pr, s, ctx] = await Promise.all([
        api<Project>(`/api/projects/${id}`),
        api<Task[]>(`/api/tasks?project_id=${id}`).catch(() => []),
        api<PullRequest[]>('/api/prs').catch(() => []),
        api<Session[]>('/api/sessions').catch(() => []),
        api<ContextItem[]>(`/api/context?project_id=${id}`).catch(() => []),
      ])
      setProject(p)
      setTasks(t)
      setPrs(pr)
      setSessions(s)
      setContextItems(ctx)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load project')
    }
  }, [id])

  useEffect(() => { load() }, [load])

  async function createTask(body: { project_id: string; title: string; description?: string; priority?: number }) {
    await api('/api/tasks', { method: 'POST', body: JSON.stringify(body) })
    load()
  }

  async function updateTask(taskId: string, body: Partial<Task>) {
    await api(`/api/tasks/${taskId}`, { method: 'PATCH', body: JSON.stringify(body) })
    load()
  }

  async function deleteTask(taskId: string) {
    await api(`/api/tasks/${taskId}`, { method: 'DELETE' })
    load()
  }

  function handleTaskClick(task: Task) {
    setSelectedTask(task)
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

  return (
    <div className="project-detail">
      <div className="pd-nav">
        <Link to="/projects" className="btn btn-secondary btn-sm">&larr; Projects</Link>
      </div>

      <div className="pd-header">
        <div>
          <h1>{project.name}</h1>
          {project.description && <p className="pd-desc">{project.description}</p>}
        </div>
        <span className={`status-badge ${project.status}`}>{project.status}</span>
      </div>

      <div className="pd-meta">
        <div className="pd-progress">
          <ProgressBar done={doneTasks} total={tasks.length} />
        </div>
        {project.target_date && (
          <span className="pd-date">Target: {new Date(project.target_date).toLocaleDateString()}</span>
        )}
      </div>

      {/* Workers */}
      {assignedSessions.length > 0 && (
        <section className="pd-section">
          <h2>Workers ({assignedSessions.length})</h2>
          <div className="pd-worker-grid">
            {assignedSessions.map(s => (
              <WorkerCard key={s.id} session={s} />
            ))}
          </div>
        </section>
      )}

      {/* Tasks */}
      <section className="pd-section">
        <div className="pd-section-header">
          <h2>Tasks ({parentTasks.length})</h2>
          <div className="pd-section-actions">
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
            <button className="btn btn-primary btn-sm" onClick={() => setShowTaskForm(true)}>
              + Add Task
            </button>
          </div>
        </div>
        {taskViewMode === 'board' ? (
          <TaskBoard tasks={parentTasks} onTaskClick={handleTaskClick} />
        ) : (
          <TaskTable tasks={parentTasks} onTaskClick={handleTaskClick} />
        )}
      </section>

      {/* PRs */}
      {prs.length > 0 && (
        <section className="pd-section">
          <h2>Pull Requests</h2>
          <div className="pd-prs">
            {prs.map(pr => (
              <div key={pr.id} className="pd-pr-item">
                <a href={pr.url} target="_blank" rel="noopener noreferrer">
                  {pr.number ? `#${pr.number} ` : ''}{pr.title || pr.url}
                </a>
                <span className={`status-badge ${pr.status}`}>{pr.status}</span>
              </div>
            ))}
          </div>
        </section>
      )}

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
                onClick={() => setSelectedContext(c)}
              >
                <div className="pd-context-header">
                  {c.category && <span className="cp-category-tag">{c.category}</span>}
                  <strong>{c.title}</strong>
                  <span className="pd-context-time">{c.updated_at ? new Date(c.updated_at).toLocaleDateString() : ''}</span>
                </div>
                <p className="pd-context-preview">{c.content.slice(0, 150)}{c.content.length > 150 ? '...' : ''}</p>
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

      <TaskDetailModal
        task={selectedTask}
        sessions={sessions}
        onClose={() => setSelectedTask(null)}
        onUpdate={updateTask}
        onDelete={deleteTask}
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
          load()
        }}
        onDelete={async (ctxId) => {
          await api(`/api/context/${ctxId}`, { method: 'DELETE' })
          load()
        }}
      />
    </div>
  )
}
