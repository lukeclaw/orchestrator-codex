import { Link } from 'react-router-dom'
import type { Project } from '../../api/types'
import ProgressBar from '../common/ProgressBar'
import './ProjectCard.css'

interface Props {
  project: Project
  onEdit?: (project: Project) => void
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

export default function ProjectCard({ project, onEdit }: Props) {
  const stats = project.stats
  const taskStats = stats?.tasks
  const workerStats = stats?.workers
  const contextStats = stats?.context

  const done = taskStats?.done ?? 0
  const total = taskStats?.total ?? 0

  function handleEditClick(e: React.MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    onEdit?.(project)
  }

  return (
    <Link to={`/projects/${project.id}`} className="project-card">
      <div className="pc-header">
        <span className="pc-name">{project.name}</span>
        <div className="pc-header-actions">
          {onEdit && (
            <button
              type="button"
              className="pc-edit-btn"
              onClick={handleEditClick}
              title="Edit project"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
          )}
          <span className={`status-badge ${project.status}`}>{project.status}</span>
        </div>
      </div>
      
      {project.description && (
        <p className="pc-desc">{project.description}</p>
      )}
      
      {total > 0 && (
        <div className="pc-progress">
          <ProgressBar done={done} total={total} />
        </div>
      )}

      {stats && (
        <div className="pc-stats">
          <div className="pc-stat-group">
            <span className="pc-stat-label">Tasks</span>
            <div className="pc-stat-items">
              {taskStats && taskStats.in_progress > 0 && (
                <span className="pc-stat-item in-progress" title="In Progress">
                  {taskStats.in_progress} active
                </span>
              )}
              {taskStats && taskStats.blocked > 0 && (
                <span className="pc-stat-item blocked" title="Blocked">
                  {taskStats.blocked} blocked
                </span>
              )}
              {taskStats && taskStats.todo > 0 && (
                <span className="pc-stat-item todo" title="To Do">
                  {taskStats.todo} todo
                </span>
              )}
              <span className="pc-stat-item done" title="Done">
                {done}/{total}
              </span>
            </div>
          </div>

          {workerStats && workerStats.total > 0 && (
            <div className="pc-stat-group">
              <span className="pc-stat-label">Workers</span>
              <div className="pc-stat-items">
                {workerStats.working > 0 && (
                  <span className="pc-stat-item working">{workerStats.working} working</span>
                )}
                {workerStats.waiting > 0 && (
                  <span className="pc-stat-item waiting">{workerStats.waiting} waiting</span>
                )}
                {workerStats.idle > 0 && (
                  <span className="pc-stat-item idle">{workerStats.idle} idle</span>
                )}
              </div>
            </div>
          )}

          {contextStats && contextStats.total > 0 && (
            <div className="pc-stat-group">
              <span className="pc-stat-label">Context</span>
              <div className="pc-stat-items">
                <span className="pc-stat-item">{contextStats.total} doc{contextStats.total !== 1 ? 's' : ''}</span>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="pc-footer">
        <span className="pc-created" title={`Created ${new Date(project.created_at).toLocaleString()}`}>
          Created {formatRelativeTime(project.created_at)}
        </span>
        {project.target_date && (
          <span className="pc-date">Due {new Date(project.target_date).toLocaleDateString()}</span>
        )}
      </div>
    </Link>
  )
}
