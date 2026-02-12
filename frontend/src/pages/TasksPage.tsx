import { useState, useMemo } from 'react'
import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import type { Task } from '../api/types'
import { timeAgo } from '../components/common/TimeAgo'
import './TasksPage.css'

type SortKey = 'key' | 'title' | 'project' | 'status' | 'priority' | 'subtasks' | 'assigned' | 'updated'
type SortDir = 'asc' | 'desc'

const STATUS_ORDER: Record<string, number> = { blocked: 3, in_progress: 2, todo: 1, done: 0 }
const PRIORITY_ORDER: Record<string, number> = { H: 3, M: 2, L: 1 }

function formatStatus(status: string) {
  switch (status) {
    case 'todo': return 'To Do'
    case 'in_progress': return 'In Progress'
    case 'done': return 'Done'
    case 'blocked': return 'Blocked'
    default: return status
  }
}

export default function TasksPage() {
  const { tasks, projects, sessions } = useApp()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [sortKey, setSortKey] = useState<SortKey>('updated')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // Filters from URL params
  const statusFilter = searchParams.get('status') || 'all'
  const priorityFilter = searchParams.get('priority') || 'all'
  const projectFilter = searchParams.get('project') || 'all'
  const searchQuery = searchParams.get('q') || ''

  const updateFilter = (key: string, value: string) => {
    const newParams = new URLSearchParams(searchParams)
    if (value === 'all' || value === '') {
      newParams.delete(key)
    } else {
      newParams.set(key, value)
    }
    setSearchParams(newParams)
  }

  // Lookup helpers
  const projectMap = useMemo(() => new Map(projects.map(p => [p.id, p])), [projects])
  const sessionMap = useMemo(() => new Map(sessions.map(s => [s.id, s])), [sessions])

  const getProjectName = (id: string) => projectMap.get(id)?.name || 'Unknown'
  const getWorkerName = (id: string | null) => id ? sessionMap.get(id)?.name || null : null

  // Sort value extractor
  function getSortValue(t: Task, key: SortKey): string | number {
    switch (key) {
      case 'key': return t.task_key || ''
      case 'title': return t.title.toLowerCase()
      case 'project': return getProjectName(t.project_id).toLowerCase()
      case 'status': return STATUS_ORDER[t.status] ?? 0
      case 'priority': return PRIORITY_ORDER[t.priority] ?? 0
      case 'subtasks': return t.subtask_stats?.total ?? 0
      case 'assigned': return getWorkerName(t.assigned_session_id) || ''
      case 'updated': return new Date(t.updated_at || t.created_at).getTime()
      default: return 0
    }
  }

  // Filter + sort
  const filteredTasks = useMemo(() => {
    const filtered = tasks
      .filter(t => !t.parent_task_id)
      .filter(t => statusFilter === 'all' || t.status === statusFilter)
      .filter(t => priorityFilter === 'all' || t.priority === priorityFilter)
      .filter(t => projectFilter === 'all' || t.project_id === projectFilter)
      .filter(t => {
        if (!searchQuery) return true
        const q = searchQuery.toLowerCase()
        return (
          t.title.toLowerCase().includes(q) ||
          t.task_key?.toLowerCase().includes(q) ||
          t.description?.toLowerCase().includes(q)
        )
      })

    return [...filtered].sort((a, b) => {
      const aVal = getSortValue(a, sortKey)
      const bVal = getSortValue(b, sortKey)
      const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [tasks, statusFilter, priorityFilter, projectFilter, searchQuery, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'title' || key === 'key' ? 'asc' : 'desc')
    }
  }

  function SortHeader({ k, children, className }: { k: SortKey; children: React.ReactNode; className?: string }) {
    const active = sortKey === k
    return (
      <th
        className={`pt-th sortable ${active ? 'active' : ''} ${className || ''}`}
        onClick={() => handleSort(k)}
      >
        {children}
        {active && <span className="sort-arrow">{sortDir === 'asc' ? '↑' : '↓'}</span>}
      </th>
    )
  }

  return (
    <div className="tasks-page">
      <div className="page-header">
        <h1>Tasks</h1>
        <div className="tasks-stats">
          <span className="stat">{filteredTasks.length} tasks</span>
          {statusFilter !== 'all' && (
            <span className="stat-filter">filtered by {formatStatus(statusFilter)}</span>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="tasks-filters">
        <input
          type="text"
          className="search-input"
          placeholder="Search tasks..."
          value={searchQuery}
          onChange={e => updateFilter('q', e.target.value)}
        />

        <select
          className="filter-select"
          value={projectFilter}
          onChange={e => updateFilter('project', e.target.value)}
        >
          <option value="all">All Projects</option>
          {projects.map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>

        <select
          className="filter-select"
          value={statusFilter}
          onChange={e => updateFilter('status', e.target.value)}
        >
          <option value="all">All Statuses</option>
          <option value="todo">To Do</option>
          <option value="in_progress">In Progress</option>
          <option value="done">Done</option>
          <option value="blocked">Blocked</option>
        </select>

        <select
          className="filter-select"
          value={priorityFilter}
          onChange={e => updateFilter('priority', e.target.value)}
        >
          <option value="all">All Priorities</option>
          <option value="H">High</option>
          <option value="M">Medium</option>
          <option value="L">Low</option>
        </select>

        {(statusFilter !== 'all' || priorityFilter !== 'all' || projectFilter !== 'all' || searchQuery) && (
          <button
            className="btn btn-sm btn-secondary"
            onClick={() => setSearchParams(new URLSearchParams())}
          >
            Clear Filters
          </button>
        )}
      </div>

      {/* Tasks Table */}
      {filteredTasks.length === 0 ? (
        <div className="empty-state">
          {tasks.length === 0 ? (
            <p>No tasks yet. Create tasks from a project page.</p>
          ) : (
            <p>No tasks match the current filters.</p>
          )}
        </div>
      ) : (
        <div className="tasks-table-wrapper">
          <table className="pt-table">
            <thead>
              <tr>
                <SortHeader k="key">Key</SortHeader>
                <SortHeader k="title">Title</SortHeader>
                <SortHeader k="project">Project</SortHeader>
                <SortHeader k="status">Status</SortHeader>
                <SortHeader k="priority">Priority</SortHeader>
                <SortHeader k="subtasks" className="center">Subtasks</SortHeader>
                <SortHeader k="assigned">Assigned</SortHeader>
                <SortHeader k="updated">Updated</SortHeader>
              </tr>
            </thead>
            <tbody>
              {filteredTasks.map(task => {
                const stats = task.subtask_stats
                const workerName = getWorkerName(task.assigned_session_id)
                return (
                  <tr
                    key={task.id}
                    className="pt-row"
                    onClick={() => navigate(`/tasks/${task.id}`)}
                  >
                    <td className="pt-td task-key">
                      <Link to={`/tasks/${task.id}`} onClick={e => e.stopPropagation()}>
                        {task.task_key || '—'}
                      </Link>
                    </td>
                    <td className="pt-td task-title">
                      <Link to={`/tasks/${task.id}`} onClick={e => e.stopPropagation()}>
                        {task.title}
                      </Link>
                    </td>
                    <td className="pt-td task-project">
                      <Link
                        to={`/projects/${task.project_id}`}
                        className="project-link"
                        onClick={e => e.stopPropagation()}
                      >
                        {getProjectName(task.project_id)}
                      </Link>
                    </td>
                    <td className="pt-td">
                      <span className={`status-badge status-${task.status}`}>
                        {formatStatus(task.status)}
                      </span>
                    </td>
                    <td className="pt-td">
                      <span className={`priority-badge priority-${task.priority}`}>
                        {task.priority === 'H' ? 'High' : task.priority === 'M' ? 'Med' : 'Low'}
                      </span>
                    </td>
                    <td className="pt-td subtasks">
                      {stats && stats.total > 0 ? (
                        <span title={`${stats.done}/${stats.total} done`}>
                          {stats.done}/{stats.total}
                        </span>
                      ) : '—'}
                    </td>
                    <td className="pt-td task-assigned">
                      {workerName ? (
                        <span className="worker-badge">{workerName}</span>
                      ) : '—'}
                    </td>
                    <td className="pt-td date">
                      {timeAgo(task.updated_at || task.created_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
