import type { Task } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './TaskCard.css'

interface Props {
  task: Task
  onClick?: () => void
}

const PRIORITY_LABELS: Record<string, { label: string; class: string }> = {
  H: { label: 'High', class: 'high' },
  M: { label: 'Med', class: 'normal' },
  L: { label: 'Low', class: 'low' },
}

export default function TaskCard({ task, onClick }: Props) {
  const priority = PRIORITY_LABELS[task.priority] || PRIORITY_LABELS.M
  const stats = task.subtask_stats

  return (
    <div className="task-card" onClick={onClick}>
      <div className="tc-title">
        {task.task_key && <span className="tc-key">{task.task_key}:</span>}
        {task.title}
      </div>
      {task.description && (
        <p className="tc-desc">{task.description}</p>
      )}
      <div className="tc-footer">
        <span className={`urgency-tag ${priority.class}`}>{priority.label}</span>
        {stats && stats.total > 0 && (
          <span className="tc-subtasks" title={`${stats.done}/${stats.total} done`}>
            <span className="tc-subtask-progress">
              {stats.done}/{stats.total}
            </span>
            subtasks
          </span>
        )}
        <span className="tc-time">{timeAgo(task.created_at)}</span>
      </div>
    </div>
  )
}
