import { useState, useEffect, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Task, Project } from '../api/types'
import { timeAgo } from '../components/common/TimeAgo'
import FilterBar from '../components/common/FilterBar'
import './TasksPage.css'

const STATUS_OPTIONS = ['all', 'todo', 'in_progress', 'done', 'blocked']
const PRIORITY_OPTIONS = ['all', 'H', 'M', 'L']

export default function TasksPage() {
  const { tasks, projects, sessions, refresh } = useApp()
  const [searchParams, setSearchParams] = useSearchParams()
  
  // Filters from URL params
  const statusFilter = searchParams.get('status') || 'all'
  const priorityFilter = searchParams.get('priority') || 'all'
  const projectFilter = searchParams.get('project') || 'all'
  const searchQuery = searchParams.get('q') || ''

  // Update URL params
  const updateFilter = (key: string, value: string) => {
    const newParams = new URLSearchParams(searchParams)
    if (value === 'all' || value === '') {
      newParams.delete(key)
    } else {
      newParams.set(key, value)
    }
    setSearchParams(newParams)
  }

  // Filter tasks (only parent tasks, not subtasks)
  const filteredTasks = useMemo(() => {
    return tasks
      .filter(t => !t.parent_task_id) // Only parent tasks
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
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  }, [tasks, statusFilter, priorityFilter, projectFilter, searchQuery])

  // Get project name helper
  const getProjectName = (projectId: string) => {
    const project = projects.find(p => p.id === projectId)
    return project?.name || 'Unknown'
  }

  // Get worker name helper
  const getWorkerName = (sessionId: string | null) => {
    if (!sessionId) return null
    const session = sessions.find(s => s.id === sessionId)
    return session?.name || null
  }

  const formatStatus = (status: string) => {
    switch (status) {
      case 'todo': return 'To Do'
      case 'in_progress': return 'In Progress'
      case 'done': return 'Done'
      case 'blocked': return 'Blocked'
      default: return status
    }
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
          {STATUS_OPTIONS.filter(s => s !== 'all').map(s => (
            <option key={s} value={s}>{formatStatus(s)}</option>
          ))}
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
          <table className="tasks-table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Title</th>
                <th>Project</th>
                <th>Status</th>
                <th>Priority</th>
                <th>Subtasks</th>
                <th>Assigned</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {filteredTasks.map(task => {
                const stats = task.subtask_stats
                const workerName = getWorkerName(task.assigned_session_id)
                return (
                  <tr key={task.id} className="task-row">
                    <td className="task-key">
                      <Link to={`/tasks/${task.id}`}>{task.task_key || '—'}</Link>
                    </td>
                    <td className="task-title">
                      <Link to={`/tasks/${task.id}`}>{task.title}</Link>
                    </td>
                    <td className="task-project">
                      <Link to={`/projects/${task.project_id}`} className="project-link">
                        {getProjectName(task.project_id)}
                      </Link>
                    </td>
                    <td>
                      <span className={`status-badge status-${task.status}`}>
                        {formatStatus(task.status)}
                      </span>
                    </td>
                    <td>
                      <span className={`priority-badge priority-${task.priority}`}>
                        {task.priority === 'H' ? 'High' : task.priority === 'M' ? 'Medium' : 'Low'}
                      </span>
                    </td>
                    <td className="task-subtasks">
                      {stats && stats.total > 0 ? (
                        <span title={`${stats.done}/${stats.total} done`}>
                          {stats.done}/{stats.total}
                        </span>
                      ) : '—'}
                    </td>
                    <td className="task-assigned">
                      {workerName ? (
                        <span className="worker-badge">{workerName}</span>
                      ) : '—'}
                    </td>
                    <td className="task-time">{timeAgo(task.created_at)}</td>
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
