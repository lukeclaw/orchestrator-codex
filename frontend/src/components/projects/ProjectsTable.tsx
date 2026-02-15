import { useState, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { Project } from '../../api/types'
import { timeAgo, parseDate } from '../common/TimeAgo'
import './ProjectsTable.css'

type SortKey = 'name' | 'tasks' | 'subtasks' | 'progress' | 'workers' | 'status' | 'created' | 'updated'
type SortDir = 'asc' | 'desc'

interface Props {
  projects: Project[]
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

function getProjectSortValue(p: Project, key: SortKey): string | number {
  const taskStats = p.stats?.tasks
  const subtaskStats = p.stats?.subtasks
  switch (key) {
    case 'name': return p.name.toLowerCase()
    case 'tasks': return taskStats?.total ?? 0
    case 'subtasks': return subtaskStats?.total ?? 0
    case 'progress': {
      const totalItems = (taskStats?.total ?? 0) + (subtaskStats?.total ?? 0)
      const doneItems = (taskStats?.done ?? 0) + (subtaskStats?.done ?? 0)
      return totalItems > 0 ? doneItems / totalItems : 0
    }
    case 'status': {
      // Sort by status: active > paused > completed
      if (p.status === 'active') return 2
      if (p.status === 'paused') return 1
      return 0
    }
    case 'workers': return p.stats?.workers?.total ?? 0
    case 'created': return parseDate(p.created_at).getTime()
    case 'updated': return parseDate(p.updated_at || p.created_at).getTime()
    default: return 0
  }
}

export default function ProjectsTable({ projects }: Props) {
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<SortKey>('updated')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const sortedProjects = useMemo(() => {
    return [...projects].sort((a, b) => {
      const aVal = getProjectSortValue(a, sortKey)
      const bVal = getProjectSortValue(b, sortKey)
      const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [projects, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'name' ? 'asc' : 'desc')
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

  if (projects.length === 0) {
    return <p className="empty-state">No projects to display.</p>
  }

  return (
    <table className="pt-table">
      <thead>
        <tr>
          <SortHeader k="name">Name</SortHeader>
          <SortHeader k="tasks" className="center">Tasks</SortHeader>
          <SortHeader k="subtasks" className="center">Subtasks</SortHeader>
          <SortHeader k="progress">Progress</SortHeader>
          <SortHeader k="workers">Workers</SortHeader>
          <SortHeader k="status">Status</SortHeader>
          <SortHeader k="created">Created</SortHeader>
          <SortHeader k="updated">Updated</SortHeader>
        </tr>
      </thead>
      <tbody>
        {sortedProjects.map(p => {
          const stats = p.stats
          const taskStats = stats?.tasks
          const subtaskStats = stats?.subtasks
          const workerDetails = stats?.workers?.details ?? []
          const tasksDone = taskStats?.done ?? 0
          const tasksInProgress = taskStats?.in_progress ?? 0
          const tasksBlocked = taskStats?.blocked ?? 0
          const tasksTotal = taskStats?.total ?? 0
          const subtasksDone = subtaskStats?.done ?? 0
          const subtasksTotal = subtaskStats?.total ?? 0
          // Combined progress: tasks + subtasks (subtasks only have done/total at project level)
          const totalItems = tasksTotal + subtasksTotal
          const doneItems = tasksDone + subtasksDone
          const activeItems = tasksInProgress
          const blockedItems = tasksBlocked

          return (
            <tr key={p.id} className="pt-row" onClick={() => navigate(`/projects/${p.id}`)}>
              <td className="pt-td name">
                <span className={`pt-status-dot ${p.status}`} />
                <Link to={`/projects/${p.id}`} className="pt-name">{p.name}</Link>
              </td>
              <td className="pt-td tasks">{tasksTotal}</td>
              <td className="pt-td subtasks">{subtasksTotal}</td>
              <td className="pt-td progress">
                <div className="pt-progress">
                  <div className="pt-progress-bar">
                    {doneItems > 0 && <div className="pt-seg done" style={{ width: `${(doneItems / totalItems) * 100}%` }} />}
                    {activeItems > 0 && <div className="pt-seg active" style={{ width: `${(activeItems / totalItems) * 100}%` }} />}
                    {blockedItems > 0 && <div className="pt-seg blocked" style={{ width: `${(blockedItems / totalItems) * 100}%` }} />}
                  </div>
                  <span className="pt-progress-count">{doneItems}/{totalItems}</span>
                </div>
              </td>
              <td className="pt-td workers">
                <div className="pt-worker-tags">
                  {workerDetails.length === 0 ? (
                    <span className="pt-no-workers">—</span>
                  ) : (
                    workerDetails.map(w => (
                      <span key={w.id} className={`pt-worker-tag ${w.status}`} title={`${w.name} (${w.status})`}>
                        {w.name}
                      </span>
                    ))
                  )}
                </div>
              </td>
              <td className={`pt-td status ${p.status}`}>{p.status}</td>
              <td className="pt-td date" title={parseDate(p.created_at).toLocaleString()}>
                {formatDate(p.created_at)}
              </td>
              <td className="pt-td date" title={parseDate(p.updated_at || p.created_at).toLocaleString()}>
                {formatDate(p.updated_at || p.created_at)}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
