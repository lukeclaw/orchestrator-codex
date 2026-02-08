import type { Task } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './TaskCard.css'

interface Props {
  task: Task
  onClick?: () => void
}

export default function TaskCard({ task, onClick }: Props) {
  const priorityLabel = task.priority <= 1 ? 'low' : task.priority <= 3 ? 'normal' : 'high'
  const stats = task.subtask_stats

  return (
    <div className="task-card" onClick={onClick}>
      <div className="tc-title">{task.title}</div>
      {task.description && (
        <p className="tc-desc">{task.description}</p>
      )}
      <div className="tc-footer">
        <span className={`urgency-tag ${priorityLabel}`}>P{task.priority}</span>
        {stats && stats.total > 0 && (
          <span className="tc-subtasks" title={`${stats.done}/${stats.total} done`}>
            <span className="tc-subtask-progress">
              {stats.done}/{stats.total}
            </span>
            subtasks
          </span>
        )}
        {task.links && task.links.length > 0 && (
          <span className="tc-links">{task.links.length} link{task.links.length !== 1 ? 's' : ''}</span>
        )}
        {task.assigned_session_id && (
          <span className="tc-worker">Assigned</span>
        )}
        <span className="tc-time">{timeAgo(task.created_at)}</span>
      </div>
    </div>
  )
}
