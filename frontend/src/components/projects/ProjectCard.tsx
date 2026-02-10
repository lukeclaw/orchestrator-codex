import { Link } from 'react-router-dom'
import type { Project } from '../../api/types'
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

  const done = taskStats?.done ?? 0
  const total = taskStats?.total ?? 0
  const inProgress = taskStats?.in_progress ?? 0
  const blocked = taskStats?.blocked ?? 0
  const working = workerStats?.working ?? 0

  // Determine border color based on activity
  const getBorderClass = () => {
    if (blocked > 0) return 'border-blocked'
    if (working > 0) return 'border-working'
    if (inProgress > 0) return 'border-active'
    return ''
  }

  function handleEditClick(e: React.MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    onEdit?.(project)
  }

  // Build compact stats items
  const statItems: { label: string; className: string }[] = []
  if (taskStats) {
    if (taskStats.in_progress > 0) statItems.push({ label: `${taskStats.in_progress} active`, className: 'active' })
    if (taskStats.blocked > 0) statItems.push({ label: `${taskStats.blocked} blocked`, className: 'blocked' })
  }
  if (workerStats && workerStats.working > 0) {
    statItems.push({ label: `${workerStats.working} working`, className: 'working' })
  }

  return (
    <Link to={`/projects/${project.id}`} className={`project-card ${getBorderClass()}`}>
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
        </div>
      </div>
      
      {project.description && (
        <p className="pc-desc">{project.description}</p>
      )}
      
      {/* Progress bar with inline count */}
      <div className="pc-progress-row">
        <div className="pc-progress-bar">
          <div 
            className="pc-progress-fill" 
            style={{ width: total > 0 ? `${(done / total) * 100}%` : '0%' }}
          />
        </div>
        <span className="pc-progress-count">{done}/{total}</span>
      </div>

      {/* Compact stats row */}
      {statItems.length > 0 && (
        <div className="pc-stats-row">
          {statItems.map((item, i) => (
            <span key={i} className={`pc-stat ${item.className}`}>
              <span className="pc-stat-dot" />
              {item.label}
            </span>
          ))}
        </div>
      )}

      <div className="pc-footer">
        <span className="pc-created" title={`Created ${new Date(project.created_at).toLocaleString()}`}>
          {formatRelativeTime(project.created_at)}
        </span>
        {project.target_date && (
          <span className="pc-date">Due {new Date(project.target_date).toLocaleDateString()}</span>
        )}
      </div>
    </Link>
  )
}
