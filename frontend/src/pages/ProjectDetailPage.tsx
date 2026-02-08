import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import type { Project, Task, PullRequest, Session } from '../api/types'
import { api } from '../api/client'
import TaskBoard from '../components/tasks/TaskBoard'
import TaskForm from '../components/tasks/TaskForm'
import ProgressBar from '../components/common/ProgressBar'
import './ProjectDetailPage.css'

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [project, setProject] = useState<Project | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [prs, setPrs] = useState<PullRequest[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [error, setError] = useState('')
  const [showTaskForm, setShowTaskForm] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    try {
      const [p, t, pr, s] = await Promise.all([
        api<Project>(`/api/projects/${id}`),
        api<Task[]>(`/api/tasks?project_id=${id}`).catch(() => []),
        api<PullRequest[]>('/api/prs').catch(() => []),
        api<Session[]>('/api/sessions').catch(() => []),
      ])
      setProject(p)
      setTasks(t)
      setPrs(pr)
      setSessions(s)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load project')
    }
  }, [id])

  useEffect(() => { load() }, [load])

  async function createTask(body: { project_id: string; title: string; description?: string; priority?: number }) {
    await api('/api/tasks', { method: 'POST', body: JSON.stringify(body) })
    load()
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

  const doneTasks = tasks.filter(t => t.status === 'done').length
  const assignedSessions = sessions.filter(s =>
    tasks.some(t => t.assigned_session_id === s.id)
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
          <div className="pd-workers">
            {assignedSessions.map(s => (
              <Link key={s.id} to={`/sessions/${s.id}`} className="pd-worker">
                <span className={`status-indicator ${s.status}`} />
                <span>{s.name}</span>
                <span className={`status-badge ${s.status}`}>{s.status}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* Task Board */}
      <section className="pd-section">
        <div className="pd-section-header">
          <h2>Tasks ({tasks.length})</h2>
          <button className="btn btn-primary btn-sm" onClick={() => setShowTaskForm(true)}>
            + Add Task
          </button>
        </div>
        <TaskBoard tasks={tasks} />
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

      <TaskForm
        open={showTaskForm}
        onClose={() => setShowTaskForm(false)}
        onSubmit={createTask}
        projects={project ? [project] : []}
        defaultProjectId={id}
      />
    </div>
  )
}
