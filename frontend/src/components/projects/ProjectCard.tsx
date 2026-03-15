import { Link } from 'react-router-dom'
import type { Project } from '../../api/types'
import { timeAgo, parseLocalDate } from '../common/TimeAgo'
import StatusDot from '../common/StatusDot'
import './ProjectCard.css'

interface Props {
  project: Project
  onEdit?: (project: Project) => void
}

/** Segmented bar: done (green) | active (blue) | blocked (red) | todo (empty) */
function SegmentedBar({ done, active, blocked, total }: { done: number; active: number; blocked: number; total: number }) {
  if (total === 0) return <div className="pc-bar"><div className="pc-bar-empty" /></div>
  const donePct = (done / total) * 100
  const activePct = (active / total) * 100
  const blockedPct = (blocked / total) * 100
  return (
    <div className="pc-bar" title={`${done} done · ${active} active · ${blocked} blocked · ${total - done - active - blocked} todo`}>
      {donePct > 0 && <div className="pc-seg done" style={{ width: `${donePct}%` }} />}
      {activePct > 0 && <div className="pc-seg active" style={{ width: `${activePct}%` }} />}
      {blockedPct > 0 && <div className="pc-seg blocked" style={{ width: `${blockedPct}%` }} />}
    </div>
  )
}

export default function ProjectCard({ project }: Props) {
  const stats = project.stats
  const taskStats = stats?.tasks
  const subtaskStats = stats?.subtasks
  const workerStats = stats?.workers

  const inProgress = taskStats?.in_progress ?? 0
  const tasksDone = taskStats?.done ?? 0
  const blocked = taskStats?.blocked ?? 0
  const tasksTotal = taskStats?.total ?? 0
  const subtasksTotal = subtaskStats?.total ?? 0
  const subtasksDone = subtaskStats?.done ?? 0

  const workerDetails = workerStats?.details ?? []

  /** Show only the alias part of auto-generated worker names (after last '_') */
  function shortWorkerName(name: string) {
    const idx = name.lastIndexOf('_')
    return idx > 0 ? name.slice(idx + 1) : name
  }

  return (
    <Link to={`/projects/${project.id}`} className="project-card">
      {/* Header */}
      <div className="pc-header">
        <div className="pc-header-left">
          <span className="pc-name">{project.name}</span>
          <span className={`pc-status-pill ${project.status}`}>{project.status}</span>
        </div>
        {project.target_date && (
          <span className="pc-target-date" title="Target date">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
              <line x1="16" y1="2" x2="16" y2="6" />
              <line x1="8" y1="2" x2="8" y2="6" />
              <line x1="3" y1="10" x2="21" y2="10" />
            </svg>
            {parseLocalDate(project.target_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
          </span>
        )}
      </div>

      {project.description && (
        <p className="pc-desc">{project.description}</p>
      )}

      {/* Progress — tasks and subtasks side by side */}
      <div className="pc-progress-grid">
        <div className="pc-progress-col">
          <div className="pc-progress-header">
            <span className="pc-progress-title">Tasks</span>
            <span className="pc-progress-nums">{tasksDone}/{tasksTotal}</span>
          </div>
          <SegmentedBar done={tasksDone} active={inProgress} blocked={blocked} total={tasksTotal} />
        </div>
        <div className="pc-progress-col">
          <div className="pc-progress-header">
            <span className="pc-progress-title">Subtasks</span>
            <span className="pc-progress-nums">{subtasksDone}/{subtasksTotal}</span>
          </div>
          <div className="pc-bar">
            <div className="pc-seg done" style={{ width: subtasksTotal > 0 ? `${(subtasksDone / subtasksTotal) * 100}%` : '0%' }} />
          </div>
        </div>
      </div>

      {/* Footer: workers + timestamp in one line */}
      <div className="pc-footer">
        <div className="pc-footer-workers">
          {workerDetails.length > 0 ? (
            workerDetails.map(w => (
              <span key={w.id} className={`pc-worker ${w.status}`} title={`${w.name} (${w.status})`}>
                <StatusDot status={w.status} size="sm" />
                {shortWorkerName(w.name)}
              </span>
            ))
          ) : (
            <span className="pc-no-workers">No workers</span>
          )}
        </div>
        <span className="pc-time">{timeAgo(project.updated_at || project.created_at)}</span>
      </div>
    </Link>
  )
}
