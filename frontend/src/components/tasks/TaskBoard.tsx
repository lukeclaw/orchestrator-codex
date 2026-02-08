import type { Task } from '../../api/types'
import TaskCard from './TaskCard'
import './TaskBoard.css'

const COLUMNS = [
  { key: 'todo', label: 'To Do' },
  { key: 'in_progress', label: 'In Progress' },
  { key: 'done', label: 'Done' },
  { key: 'blocked', label: 'Blocked' },
]

interface Props {
  tasks: Task[]
  onTaskClick?: (task: Task) => void
}

export default function TaskBoard({ tasks, onTaskClick }: Props) {
  return (
    <div className="task-board">
      {COLUMNS.map(col => {
        const colTasks = tasks.filter(t => t.status === col.key)
        return (
          <div key={col.key} className="tb-column">
            <div className="tb-column-header">
              <span className="tb-column-title">{col.label}</span>
              <span className="tb-column-count">{colTasks.length}</span>
            </div>
            <div className="tb-column-body">
              {colTasks.length > 0
                ? colTasks.map(t => (
                    <TaskCard key={t.id} task={t} onClick={() => onTaskClick?.(t)} />
                  ))
                : <p className="empty-state" style={{ padding: 12 }}>No tasks</p>
              }
            </div>
          </div>
        )
      })}
    </div>
  )
}
