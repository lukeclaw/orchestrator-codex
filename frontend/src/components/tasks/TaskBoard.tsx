import { useRef, useEffect, useCallback, useState } from 'react'
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

function ColumnBody({ tasks, onTaskClick }: { tasks: Task[]; onTaskClick?: (task: Task) => void }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [hasOverflow, setHasOverflow] = useState(false)

  const checkOverflow = useCallback(() => {
    const el = scrollRef.current
    if (el) setHasOverflow(el.scrollHeight > el.clientHeight)
  }, [])

  useEffect(() => {
    checkOverflow()
    const ro = new ResizeObserver(checkOverflow)
    if (scrollRef.current) ro.observe(scrollRef.current)
    return () => ro.disconnect()
  }, [checkOverflow, tasks.length])

  return (
    <div className={`tb-column-body-wrapper${hasOverflow ? ' has-overflow' : ''}`}>
      <div className="tb-column-body" ref={scrollRef}>
        {tasks.length > 0
          ? tasks.map(t => (
              <TaskCard key={t.id} task={t} onClick={() => onTaskClick?.(t)} />
            ))
          : <p className="empty-state" style={{ padding: 12 }}>No tasks</p>
        }
      </div>
    </div>
  )
}

export default function TaskBoard({ tasks, onTaskClick }: Props) {
  return (
    <div className="task-board">
      {COLUMNS.map(col => {
        const colTasks = tasks.filter(t => t.status === col.key)
        return (
          <div key={col.key} className={`tb-column status-${col.key}`}>
            <div className="tb-column-header">
              <span className="tb-column-title">{col.label}</span>
              <span className={`tb-column-count status-${col.key}`}>{colTasks.length}</span>
            </div>
            <ColumnBody tasks={colTasks} onTaskClick={onTaskClick} />
          </div>
        )
      })}
    </div>
  )
}
