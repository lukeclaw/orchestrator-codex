import type { Task } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './TaskCard.css'

interface Props {
  task: Task
  onClick?: () => void
}

export default function TaskCard({ task, onClick }: Props) {
  const priorityLabel = task.priority <= 1 ? 'low' : task.priority <= 3 ? 'normal' : 'high'

  return (
    <div className="task-card" onClick={onClick}>
      <div className="tc-title">{task.title}</div>
      {task.description && (
        <p className="tc-desc">{task.description}</p>
      )}
      <div className="tc-footer">
        <span className={`urgency-tag ${priorityLabel}`}>P{task.priority}</span>
        {task.assigned_session_id && (
          <span className="tc-worker">Assigned</span>
        )}
        <span className="tc-time">{timeAgo(task.created_at)}</span>
      </div>
    </div>
  )
}
