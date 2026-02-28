import { useState, useMemo } from 'react'
import { useProjects } from '../hooks/useProjects'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Project } from '../api/types'
import { IconSearch } from '../components/common/Icons'
import ProjectCard from '../components/projects/ProjectCard'
import ProjectsTable from '../components/projects/ProjectsTable'
import ProjectForm from '../components/projects/ProjectForm'
import ProjectEditModal from '../components/projects/ProjectEditModal'
import './ProjectsPage.css'

const STATUSES = ['active', 'completed', 'paused'] as const
const STATUS_COLORS: Record<string, string> = { active: '#3fb950', completed: '#58a6ff', paused: '#d29922' }
const STATUS_LABELS: Record<string, string> = { active: 'Active', completed: 'Completed', paused: 'Paused' }

export default function ProjectsPage() {
  const { projects, loading, create, fetch: refreshProjects } = useProjects()
  const { refresh: refreshApp } = useApp()
  const [showForm, setShowForm] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [viewMode, setViewMode] = useState<'table' | 'cards'>('cards')

  // Status counts (ignoring search, so counts stay stable while typing)
  const statusCounts = useMemo(() =>
    projects.reduce<Record<string, number>>((acc, p) => {
      acc[p.status] = (acc[p.status] || 0) + 1
      return acc
    }, {}),
    [projects]
  )

  const filtered = useMemo(() => {
    let result = projects
    if (statusFilter) result = result.filter(p => p.status === statusFilter)
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      result = result.filter(p =>
        p.name.toLowerCase().includes(q) ||
        p.description?.toLowerCase().includes(q)
      )
    }
    return result
  }, [projects, statusFilter, searchQuery])

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

      {/* Status pills + inline search */}
      {projects.length > 0 && (
        <div className="pp-filter-bar">
          {STATUSES.filter(s => statusCounts[s]).map(status => (
            <button
              key={status}
              className={`pp-filter-pill${statusFilter === status ? ' active' : ''}`}
              onClick={() => setStatusFilter(statusFilter === status ? '' : status)}
              type="button"
            >
              <span className="pp-filter-dot" style={{ background: STATUS_COLORS[status] }} />
              <span className="pp-filter-pill-count">{statusCounts[status]}</span>
              <span className="pp-filter-pill-label">{STATUS_LABELS[status]}</span>
            </button>
          ))}
          <div className="pp-search-inline">
            <IconSearch size={13} className="pp-search-inline-icon" />
            <input
              className="pp-search-inline-input"
              type="text"
              placeholder="Filter..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                className="pp-search-inline-clear"
                onMouseDown={e => { e.preventDefault(); setSearchQuery('') }}
                type="button"
              >&times;</button>
            )}
          </div>
        </div>
      )}

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
