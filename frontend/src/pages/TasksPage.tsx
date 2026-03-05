import { useState, useEffect, useRef, useMemo } from 'react'
import { Link, useSearchParams, useNavigate } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Task } from '../api/types'
import { timeAgo, parseDate } from '../components/common/TimeAgo'
import { IconTasks, IconFilter, IconSearch } from '../components/common/Icons'
import TaskForm from '../components/tasks/TaskForm'
import './TasksPage.css'

type SortKey = 'key' | 'title' | 'project' | 'status' | 'priority' | 'subtasks' | 'assigned' | 'updated'
type SortDir = 'asc' | 'desc'

const STATUS_ORDER: Record<string, number> = { blocked: 3, in_progress: 2, todo: 1, done: 0 }
const PRIORITY_ORDER: Record<string, number> = { H: 3, M: 2, L: 1 }

const STATUSES = ['todo', 'in_progress', 'done', 'blocked'] as const
const STATUS_COLORS: Record<string, string> = { todo: '#6e7681', in_progress: '#58a6ff', done: '#3fb950', blocked: '#f85149' }
const STATUS_LABELS: Record<string, string> = { todo: 'To Do', in_progress: 'In Progress', done: 'Done', blocked: 'Blocked' }

function formatStatus(status: string) {
  return STATUS_LABELS[status] || status
}

export default function TasksPage() {
  const { tasks, projects, sessions, refresh } = useApp()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [sortKey, setSortKey] = useState<SortKey>('updated')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [showAddTask, setShowAddTask] = useState(false)
  const [showProjectDropdown, setShowProjectDropdown] = useState(false)
  const projectThRef = useRef<HTMLTableCellElement>(null)

  // Filters from URL params
  const statusFilter = searchParams.get('status') || ''
  const priorityFilter = searchParams.get('priority') || ''
  const projectFilter = searchParams.get('project') || ''
  const searchQuery = searchParams.get('q') || ''

  const updateFilter = (key: string, value: string) => {
    const newParams = new URLSearchParams(searchParams)
    if (!value) {
      newParams.delete(key)
    } else {
      newParams.set(key, value)
    }
    setSearchParams(newParams)
  }

  // Click-outside for project dropdown
  useEffect(() => {
    if (!showProjectDropdown) return
    function handleClickOutside(e: MouseEvent) {
      if (projectThRef.current && !projectThRef.current.contains(e.target as Node)) {
        setShowProjectDropdown(false)
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setShowProjectDropdown(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [showProjectDropdown])

  // Lookup helpers
  const projectMap = useMemo(() => new Map(projects.map(p => [p.id, p])), [projects])
  const sessionMap = useMemo(() => new Map(sessions.map(s => [s.id, s])), [sessions])
  const getProjectName = (id: string) => projectMap.get(id)?.name || 'Unknown'
  const getWorker = (id: string | null) => id ? sessionMap.get(id) || null : null

  // Top-level tasks only
  const topLevelTasks = useMemo(() => tasks.filter(t => !t.parent_task_id), [tasks])

  // Status counts — computed from tasks with project/priority/search applied but NOT status
  const statusCounts = useMemo(() => {
    let filtered = topLevelTasks
    if (projectFilter) filtered = filtered.filter(t => t.project_id === projectFilter)
    if (priorityFilter) filtered = filtered.filter(t => t.priority === priorityFilter)
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter(t =>
        t.title.toLowerCase().includes(q) ||
        t.task_key?.toLowerCase().includes(q) ||
        t.description?.toLowerCase().includes(q)
      )
    }
    return filtered.reduce<Record<string, number>>((acc, t) => {
      acc[t.status] = (acc[t.status] || 0) + 1
      return acc
    }, {})
  }, [topLevelTasks, projectFilter, priorityFilter, searchQuery])

  // Sort value extractor
  function getSortValue(t: Task, key: SortKey): string | number {
    switch (key) {
      case 'key': return t.task_key || ''
      case 'title': return t.title.toLowerCase()
      case 'project': return getProjectName(t.project_id).toLowerCase()
      case 'status': return STATUS_ORDER[t.status] ?? 0
      case 'priority': return PRIORITY_ORDER[t.priority] ?? 0
      case 'subtasks': return t.subtask_stats?.total ?? 0
      case 'assigned': return getWorker(t.assigned_session_id)?.name || ''
      case 'updated': return parseDate(t.updated_at || t.created_at).getTime()
      default: return 0
    }
  }

  // Filter + sort
  const filteredTasks = useMemo(() => {
    let filtered = topLevelTasks
    if (statusFilter) filtered = filtered.filter(t => t.status === statusFilter)
    if (priorityFilter) filtered = filtered.filter(t => t.priority === priorityFilter)
    if (projectFilter) filtered = filtered.filter(t => t.project_id === projectFilter)
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter(t =>
        t.title.toLowerCase().includes(q) ||
        t.task_key?.toLowerCase().includes(q) ||
        t.description?.toLowerCase().includes(q)
      )
    }
    return [...filtered].sort((a, b) => {
      const aVal = getSortValue(a, sortKey)
      const bVal = getSortValue(b, sortKey)
      const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [topLevelTasks, statusFilter, priorityFilter, projectFilter, searchQuery, sortKey, sortDir])

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
        <svg
          className={`sort-chevron${active && sortDir === 'asc' ? ' asc' : ''}`}
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </th>
    )
  }

  // Project header with filter dropdown
  function ProjectHeader() {
    const active = sortKey === 'project'
    const projName = projectFilter ? getProjectName(projectFilter) : null
    return (
      <th
        ref={projectThRef}
        className={`pt-th sortable ${active ? 'active' : ''} tk-th-filterable`}
      >
        <span className="tk-th-label" onClick={() => handleSort('project')}>
          {projName || 'Project'}
          <svg
            className={`sort-chevron${active && sortDir === 'asc' ? ' asc' : ''}`}
            width="10" height="10" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
          ><polyline points="6 9 12 15 18 9" /></svg>
        </span>
        <button
          className={`tk-th-icon-btn${projectFilter ? ' active' : ''}`}
          onClick={e => { e.stopPropagation(); setShowProjectDropdown(p => !p) }}
          type="button"
          title="Filter by project"
        >
          <IconFilter size={13} />
        </button>
        {showProjectDropdown && (
          <div className="tk-dropdown">
            <button
              className={`tk-dropdown-option${!projectFilter ? ' selected' : ''}`}
              onClick={() => { updateFilter('project', ''); setShowProjectDropdown(false) }}
              type="button"
            >All Projects</button>
            {projects.map(p => (
              <button
                key={p.id}
                className={`tk-dropdown-option${projectFilter === p.id ? ' selected' : ''}`}
                onClick={() => { updateFilter('project', p.id); setShowProjectDropdown(false) }}
                type="button"
              >{p.name}</button>
            ))}
          </div>
        )}
      </th>
    )
  }

  const hasFilters = statusFilter || priorityFilter || projectFilter || searchQuery

  return (
    <div className="tasks-page">
      {/* Header */}
      <div className="page-header">
        <h1>Tasks</h1>
        <div className="page-header-actions">
          <span className="tasks-count">{filteredTasks.length} tasks</span>
          <button className="btn btn-primary btn-sm" onClick={() => setShowAddTask(true)}>
            + Add Task
          </button>
        </div>
      </div>

      {/* Status pills + inline search */}
      {topLevelTasks.length > 0 && (
        <div className="tk-status-bar">
          <button
            className={`tk-status-pill${!statusFilter ? ' active' : ''}`}
            onClick={() => updateFilter('status', '')}
            type="button"
          >
            <span className="tk-status-pill-count">{Object.values(statusCounts).reduce((a, b) => a + b, 0)}</span>
            <span className="tk-status-pill-label">All</span>
          </button>
          {STATUSES.filter(s => statusCounts[s]).map(status => (
            <button
              key={status}
              className={`tk-status-pill${statusFilter === status ? ' active' : ''}`}
              onClick={() => updateFilter('status', statusFilter === status ? '' : status)}
              type="button"
            >
              <span className="tk-status-dot" style={{ background: STATUS_COLORS[status] }} />
              <span className="tk-status-pill-count">{statusCounts[status]}</span>
              <span className="tk-status-pill-label">{STATUS_LABELS[status]}</span>
            </button>
          ))}
          <div className="tk-search-inline">
            <IconSearch size={13} className="tk-search-inline-icon" />
            <input
              className="tk-search-inline-input"
              type="text"
              placeholder="Filter..."
              value={searchQuery}
              onChange={e => updateFilter('q', e.target.value)}
            />
            {searchQuery && (
              <button
                className="tk-search-inline-clear"
                onMouseDown={e => { e.preventDefault(); updateFilter('q', '') }}
                type="button"
              >&times;</button>
            )}
          </div>
        </div>
      )}

      {/* Table */}
      {filteredTasks.length === 0 ? (
        <div className="tk-empty-state">
          {hasFilters ? (
            <>
              <IconFilter size={32} />
              <p>No tasks match your filters.</p>
              <button className="btn btn-secondary" onClick={() => setSearchParams(new URLSearchParams())}>
                Clear Filters
              </button>
            </>
          ) : (
            <>
              <IconTasks size={48} />
              <h3>No tasks yet</h3>
              <p>Create tasks from a project page or use the button above.</p>
              <button className="btn btn-primary" onClick={() => setShowAddTask(true)}>
                + Add Task
              </button>
            </>
          )}
        </div>
      ) : (
        <div className="tasks-table-wrapper">
          <table className="pt-table">
            <thead>
              <tr>
                <SortHeader k="key">Key</SortHeader>
                <SortHeader k="title">Title</SortHeader>
                <ProjectHeader />
                <SortHeader k="status">Status</SortHeader>
                <SortHeader k="priority" className="tk-col-priority">Priority</SortHeader>
                <SortHeader k="subtasks" className="center">Subtasks</SortHeader>
                <SortHeader k="assigned">Assigned</SortHeader>
                <SortHeader k="updated">Updated</SortHeader>
              </tr>
            </thead>
            <tbody>
              {filteredTasks.map(task => {
                const stats = task.subtask_stats
                const worker = getWorker(task.assigned_session_id)
                return (
                  <tr
                    key={task.id}
                    className={`pt-row tk-row tk-status-${task.status}`}
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
                      {worker ? (
                        <span className={`pt-worker-tag ${worker.status}`} title={`${worker.name} (${worker.status})`}>
                          {worker.name}
                        </span>
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
      <TaskForm
        open={showAddTask}
        onClose={() => setShowAddTask(false)}
        onSubmit={async (body) => {
          const result = await api('/api/tasks', { method: 'POST', body: JSON.stringify(body) })
          refresh()
          return result
        }}
        projects={projects}
        defaultProjectId={projectFilter || undefined}
      />
    </div>
  )
}
