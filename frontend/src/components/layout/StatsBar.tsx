import { Link } from 'react-router-dom'
import { useApp } from '../../context/AppContext'
import './StatsBar.css'

export default function StatsBar() {
  const { workers, projects, tasks } = useApp()

  const activeSessions = workers.length
  const activeProjects = projects.filter(p => p.status === 'active').length
  const inProgressTasks = tasks.filter(t => t.status === 'in_progress').length

  // Worker status breakdown
  const workingCount = workers.filter(w => w.status === 'working').length
  const waitingCount = workers.filter(w => w.status === 'waiting').length
  const blockedCount = workers.filter(w => w.status === 'blocked').length
  const errorCount = workers.filter(w => w.status === 'disconnected').length

  const workerParts: string[] = []
  if (workingCount > 0) workerParts.push(`${workingCount} working`)
  if (blockedCount > 0) workerParts.push(`${blockedCount} blocked`)
  if (waitingCount > 0) workerParts.push(`${waitingCount} waiting`)
  if (errorCount > 0) workerParts.push(`${errorCount} offline`)
  const workerSub = workerParts.join(' · ') || 'none'

  // Project breakdown
  const completedProjects = projects.filter(p => p.status === 'completed').length
  const projectSub = `${activeProjects} active · ${completedProjects} completed`

  // Task breakdown
  const todoTasks = tasks.filter(t => t.status === 'todo').length
  const doneTasks = tasks.filter(t => t.status === 'done' || t.status === 'completed').length
  const taskSub = `${doneTasks} done · ${todoTasks} todo`

  // Highlight workers stat if any need attention
  const needsAttention = blockedCount > 0 || errorCount > 0

  return (
    <section className="stats-bar" data-testid="stats-bar">
      <Link to="/workers" className={`stat stat-workers${needsAttention ? ' stat-warning' : ''}`} data-testid="stat-sessions">
        <div className="stat-value" id="stat-sessions-val">{activeSessions}</div>
        <div className="stat-text">
          <div className="stat-label">Workers</div>
          <div className="stat-sub">{workerSub}</div>
        </div>
      </Link>
      <Link to="/projects" className="stat stat-projects" data-testid="stat-projects">
        <div className="stat-value">{activeProjects}</div>
        <div className="stat-text">
          <div className="stat-label">Projects</div>
          <div className="stat-sub">{projectSub}</div>
        </div>
      </Link>
      <Link to="/tasks" className="stat stat-tasks" data-testid="stat-tasks">
        <div className="stat-value">{inProgressTasks}</div>
        <div className="stat-text">
          <div className="stat-label">In-Progress Tasks</div>
          <div className="stat-sub">{taskSub}</div>
        </div>
      </Link>
    </section>
  )
}
