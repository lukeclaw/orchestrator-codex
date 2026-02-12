import { useState } from 'react'
import { useProjects } from '../hooks/useProjects'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Project } from '../api/types'
import ProjectCard from '../components/projects/ProjectCard'
import ProjectsTable from '../components/projects/ProjectsTable'
import ProjectForm from '../components/projects/ProjectForm'
import ProjectEditModal from '../components/projects/ProjectEditModal'
import FilterBar from '../components/common/FilterBar'
import './ProjectsPage.css'

export default function ProjectsPage() {
  const { projects, loading, create, fetch: refreshProjects } = useProjects()
  const { refresh: refreshApp } = useApp()
  const [showForm, setShowForm] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [viewMode, setViewMode] = useState<'table' | 'cards'>('cards')

  const filtered = statusFilter
    ? projects.filter(p => p.status === statusFilter)
    : projects

  async function handleUpdate(projectId: string, data: { name?: string; description?: string; status?: string; target_date?: string }) {
    await api(`/api/projects/${projectId}`, { method: 'PATCH', body: JSON.stringify(data) })
    refreshProjects()
    refreshApp()
  }

  async function handleDelete(projectId: string) {
    await api(`/api/projects/${projectId}`, { method: 'DELETE' })
    refreshProjects()
    refreshApp()
  }

  return (
    <div className="projects-page">
      <div className="page-header">
        <h1>Projects</h1>
        <div className="page-header-actions">
          <div className="toggle-group toggle-sm">
            <button
              type="button"
              className={`toggle-btn${viewMode === 'table' ? ' active' : ''}`}
              onClick={() => setViewMode('table')}
            >
              Table
            </button>
            <button
              type="button"
              className={`toggle-btn${viewMode === 'cards' ? ' active' : ''}`}
              onClick={() => setViewMode('cards')}
            >
              Cards
            </button>
          </div>
          <button className="btn btn-primary" onClick={() => setShowForm(true)}>
            + New Project
          </button>
        </div>
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
      ) : viewMode === 'table' ? (
        <div className="panel">
          <ProjectsTable projects={filtered} />
        </div>
      ) : (
        <div className="projects-grid">
          {filtered.map(p => (
            <ProjectCard key={p.id} project={p} onEdit={setEditingProject} />
          ))}
        </div>
      )}

      <ProjectForm
        open={showForm}
        onClose={() => setShowForm(false)}
        onSubmit={async (body) => { const p = await create(body); refreshApp(); return p }}
      />

      <ProjectEditModal
        project={editingProject}
        onClose={() => setEditingProject(null)}
        onUpdate={handleUpdate}
        onDelete={handleDelete}
      />
    </div>
  )
}
