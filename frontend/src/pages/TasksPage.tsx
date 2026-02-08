import { useState, useMemo } from 'react'
import { useTasks } from '../hooks/useTasks'
import { useApp } from '../context/AppContext'
import TaskBoard from '../components/tasks/TaskBoard'
import TaskTable from '../components/tasks/TaskTable'
import TaskForm from '../components/tasks/TaskForm'
import FilterBar from '../components/common/FilterBar'
import './TasksPage.css'

export default function TasksPage() {
  const { tasks, loading, create } = useTasks()
  const { projects } = useApp()
  const [view, setView] = useState<'board' | 'table'>('board')
  const [showForm, setShowForm] = useState(false)
  const [projectFilter, setProjectFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const filtered = useMemo(() => {
    let list = tasks
    if (projectFilter) list = list.filter(t => t.project_id === projectFilter)
    if (statusFilter) list = list.filter(t => t.status === statusFilter)
    return list
  }, [tasks, projectFilter, statusFilter])

  const projectOptions = useMemo(() => [
    { value: '', label: 'All Projects' },
    ...projects.map(p => ({ value: p.id, label: p.name })),
  ], [projects])

  return (
    <div className="tasks-page">
      <div className="page-header">
        <h1>Tasks</h1>
        <div className="tasks-actions">
          <div className="view-toggle">
            <button
              className={`btn btn-sm ${view === 'board' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setView('board')}
            >
              Board
            </button>
            <button
              className={`btn btn-sm ${view === 'table' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setView('table')}
            >
              Table
            </button>
          </div>
          <button className="btn btn-primary" onClick={() => setShowForm(true)}>
            + New Task
          </button>
        </div>
      </div>

      <FilterBar
        filters={[
          {
            key: 'project',
            label: 'Project',
            value: projectFilter,
            options: projectOptions,
          },
          {
            key: 'status',
            label: 'Status',
            value: statusFilter,
            options: [
              { value: '', label: 'All' },
              { value: 'todo', label: 'To Do' },
              { value: 'in_progress', label: 'In Progress' },
              { value: 'done', label: 'Done' },
              { value: 'blocked', label: 'Blocked' },
            ],
          },
        ]}
        onChange={(key, val) => {
          if (key === 'project') setProjectFilter(val)
          if (key === 'status') setStatusFilter(val)
        }}
      />

      {loading ? (
        <p className="empty-state">Loading tasks...</p>
      ) : view === 'board' ? (
        <TaskBoard tasks={filtered} />
      ) : (
        <div className="panel">
          <TaskTable tasks={filtered} />
        </div>
      )}

      <TaskForm
        open={showForm}
        onClose={() => setShowForm(false)}
        onSubmit={create}
        projects={projects}
      />
    </div>
  )
}
