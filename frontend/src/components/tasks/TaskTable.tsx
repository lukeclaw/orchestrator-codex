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
            <th>Key</th>
            <th>Title</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Subtasks</th>
            <th>Links</th>
            <th>Assigned</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map(t => {
            const stats = t.subtask_stats
            return (
              <tr key={t.id} className="tt-row" onClick={() => onTaskClick?.(t)}>
                <td className="tt-key">{t.task_key || '—'}</td>
                <td className="tt-title">{t.title}</td>
                <td><span className={`status-badge status-${t.status}`}>{t.status.replace('_', ' ')}</span></td>
                <td><span className={`priority-badge priority-${t.priority}`}>{t.priority}</span></td>
                <td className="tt-subtasks">
                  {stats && stats.total > 0 ? (
                    <span title={`${stats.done}/${stats.total} done`}>
                      {stats.done}/{stats.total}
                    </span>
                  ) : '—'}
                </td>
                <td className="tt-links">{t.links?.length || '—'}</td>
                <td>{t.assigned_session_id ? 'Yes' : '—'}</td>
                <td className="tt-time">{timeAgo(t.created_at)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
