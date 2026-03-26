import { Link } from 'react-router-dom'
import { useMemo } from 'react'
import type { Project } from '../../api/types'
import { useApp } from '../../context/AppContext'
import ProviderBadge from '../common/ProviderBadge'
import { timeAgo } from '../common/TimeAgo'
import StatusDot from '../common/StatusDot'
import './ProjectCard.css'

interface Props {
  project: Project
  onEdit?: (project: Project) => void
  onToggleStar?: (projectId: string, starred: boolean) => void
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

export default function ProjectCard({ project, onToggleStar }: Props) {
  const { sessions } = useApp()
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
  const sessionById = useMemo(() => new Map(sessions.map(s => [s.id, s])), [sessions])

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
        <button
          className={`pc-star-btn${project.starred ? ' starred' : ''}`}
          title={project.starred ? 'Unstar project' : 'Star project'}
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            onToggleStar?.(project.id, !project.starred)
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill={project.starred ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        </button>
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
              <span
                key={w.id}
                className={`pc-worker ${w.status}`}
                title={`${w.name} (${w.status})`}
              >
                <StatusDot status={w.status} size="sm" />
                {shortWorkerName(w.name)}
                <ProviderBadge provider={sessionById.get(w.id)?.provider} compact />
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
