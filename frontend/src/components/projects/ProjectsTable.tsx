import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import type { Project } from '../../api/types'
import './ProjectsTable.css'

type SortKey = 'name' | 'tasks' | 'subtasks' | 'progress' | 'status' | 'created' | 'updated'
type SortDir = 'asc' | 'desc'

interface Props {
  projects: Project[]
  onEdit?: (project: Project) => void
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
    case 'created': return new Date(p.created_at).getTime()
    case 'updated': return new Date(p.updated_at || p.created_at).getTime()
    default: return 0
  }
}

export default function ProjectsTable({ projects, onEdit }: Props) {
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

  function handleEditClick(e: React.MouseEvent, project: Project) {
    e.preventDefault()
    e.stopPropagation()
    onEdit?.(project)
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
          <SortHeader k="status">Status</SortHeader>
          <SortHeader k="created">Created</SortHeader>
          <SortHeader k="updated">Updated</SortHeader>
          {onEdit && <th className="pt-th actions"></th>}
        </tr>
      </thead>
      <tbody>
        {sortedProjects.map(p => {
          const stats = p.stats
          const taskStats = stats?.tasks
          const subtaskStats = stats?.subtasks
          const tasksDone = taskStats?.done ?? 0
          const tasksTotal = taskStats?.total ?? 0
          const subtasksDone = subtaskStats?.done ?? 0
          const subtasksTotal = subtaskStats?.total ?? 0
          // Combined progress: tasks + subtasks
          const totalItems = tasksTotal + subtasksTotal
          const doneItems = tasksDone + subtasksDone

          return (
            <tr key={p.id} className="pt-row" onClick={() => window.location.href = `/projects/${p.id}`}>
              <td className="pt-td name">
                <span className={`pt-status-dot ${p.status}`} />
                <Link to={`/projects/${p.id}`} className="pt-name">{p.name}</Link>
              </td>
              <td className="pt-td tasks">{tasksTotal}</td>
              <td className="pt-td subtasks">{subtasksTotal}</td>
              <td className="pt-td progress">
                <div className="pt-progress">
                  <div className="pt-progress-bar">
                    <div 
                      className="pt-progress-fill" 
                      style={{ width: totalItems > 0 ? `${(doneItems / totalItems) * 100}%` : '0%' }}
                    />
                  </div>
                  <span className="pt-progress-count">{doneItems}/{totalItems}</span>
                </div>
              </td>
              <td className={`pt-td status ${p.status}`}>{p.status}</td>
              <td className="pt-td date" title={new Date(p.created_at).toLocaleString()}>
                {formatDate(p.created_at)}
              </td>
              <td className="pt-td date" title={new Date(p.updated_at || p.created_at).toLocaleString()}>
                {formatDate(p.updated_at || p.created_at)}
              </td>
              {onEdit && (
                <td className="pt-td actions">
                  <button
                    type="button"
                    className="pt-edit-btn"
                    onClick={(e) => handleEditClick(e, p)}
                    title="Edit project"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                    </svg>
                  </button>
                </td>
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
