import { useState, useCallback } from 'react'
import { useProjects } from '../hooks/useProjects'
import { useTasks } from '../hooks/useTasks'
import { useApp } from '../context/AppContext'
import ProjectCard from '../components/projects/ProjectCard'
import ProjectForm from '../components/projects/ProjectForm'
import FilterBar from '../components/common/FilterBar'
import './ProjectsPage.css'

export default function ProjectsPage() {
  const { projects, loading, create } = useProjects()
  const { tasks, refresh: refreshApp } = useApp()
  const [showForm, setShowForm] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

  const filtered = statusFilter
    ? projects.filter(p => p.status === statusFilter)
    : projects

  return (
    <div className="projects-page">
      <div className="page-header">
        <h1>Projects</h1>
        <button className="btn btn-primary" onClick={() => setShowForm(true)}>
          + New Project
        </button>
      </div>

      <FilterBar
        filters={[{
          key: 'status',
          label: 'Status',
          value: statusFilter,
          options: [
            { value: '', label: 'All' },
            { value: 'active', label: 'Active' },
            { value: 'completed', label: 'Completed' },
            { value: 'paused', label: 'Paused' },
          ],
        }]}
        onChange={(_, v) => setStatusFilter(v)}
      />

      {loading ? (
        <p className="empty-state">Loading projects...</p>
      ) : filtered.length === 0 ? (
        <p className="empty-state">
          {projects.length === 0
            ? 'No projects yet. Create one to get started.'
            : 'No projects match the current filter.'}
        </p>
      ) : (
        <div className="projects-grid">
          {filtered.map(p => (
            <ProjectCard
              key={p.id}
              project={p}
              tasks={tasks.filter(t => t.project_id === p.id)}
            />
          ))}
        </div>
      )}

      <ProjectForm
        open={showForm}
        onClose={() => setShowForm(false)}
        onSubmit={async (body) => { const p = await create(body); refreshApp(); return p }}
      />
    </div>
  )
}
