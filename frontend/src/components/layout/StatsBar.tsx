import { Link } from 'react-router-dom'
import { useApp } from '../../context/AppContext'
import './StatsBar.css'

export default function StatsBar() {
  const { workers, decisions, projects, tasks } = useApp()

  const activeSessions = workers.filter(s => s.status !== 'disconnected').length
  const waitingSessions = workers.filter(s => s.status === 'waiting').length
  const activeProjects = projects.filter(p => p.status === 'active').length
  const inProgressTasks = tasks.filter(t => t.status === 'in_progress').length

  return (
    <section className="stats-bar" data-testid="stats-bar">
      <Link to="/workers" className="stat" data-testid="stat-sessions">
        <div className="stat-value" id="stat-sessions-val">{activeSessions}</div>
        <div className="stat-label">Active Sessions</div>
      </Link>
      {waitingSessions > 0 && (
        <Link to="/decisions" className="stat stat-warning" data-testid="stat-waiting">
          <div className="stat-value">{waitingSessions}</div>
          <div className="stat-label">Waiting</div>
        </Link>
      )}
      <Link to="/projects" className="stat" data-testid="stat-projects">
        <div className="stat-value">{activeProjects}</div>
        <div className="stat-label">Projects</div>
      </Link>
      <Link to="/tasks" className="stat" data-testid="stat-tasks">
        <div className="stat-value">{inProgressTasks}</div>
        <div className="stat-label">In-Progress Tasks</div>
      </Link>
      <Link to="/decisions" className="stat" data-testid="stat-decisions">
        <div className="stat-value" id="stat-decisions-val">{decisions.length}</div>
        <div className="stat-label">Pending Decisions</div>
      </Link>
    </section>
  )
}
