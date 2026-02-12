import { Link } from 'react-router-dom'
import type { Project } from '../../api/types'
import './ProjectCard.css'

interface Props {
  project: Project
  onEdit?: (project: Project) => void
}

function timeAgo(dateStr: string): string {
  const diffMs = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(diffMs / 3600000)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(diffMs / 86400000)
  if (days < 7) return `${days}d ago`
  return new Date(dateStr).toLocaleDateString()
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

export default function ProjectCard({ project, onEdit }: Props) {
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

  function handleEditClick(e: React.MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    onEdit?.(project)
  }

  return (
    <Link to={`/projects/${project.id}`} className="project-card">
      {/* Header */}
      <div className="pc-header">
        <div className="pc-header-left">
          <span className="pc-name">{project.name}</span>
          <span className={`pc-status-pill ${project.status}`}>{project.status}</span>
        </div>
        {onEdit && (
          <button type="button" className="pc-edit-btn" onClick={handleEditClick} title="Edit project">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
          </button>
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
                <span className="pc-worker-dot" />
                {w.name}
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
