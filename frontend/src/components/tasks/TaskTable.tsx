import type { Task } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './TaskTable.css'

interface Props {
  tasks: Task[]
  onTaskClick?: (task: Task) => void
}

export default function TaskTable({ tasks, onTaskClick }: Props) {
  if (!tasks.length) {
    return <p className="empty-state">No tasks found</p>
  }

  return (
    <div className="task-table-wrapper">
      <table className="task-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Assigned</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map(t => (
            <tr key={t.id} className="tt-row" onClick={() => onTaskClick?.(t)}>
              <td className="tt-title">{t.title}</td>
              <td><span className={`status-badge ${t.status}`}>{t.status}</span></td>
              <td>P{t.priority}</td>
              <td>{t.assigned_session_id ? 'Yes' : '\u2014'}</td>
              <td className="tt-time">{timeAgo(t.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
