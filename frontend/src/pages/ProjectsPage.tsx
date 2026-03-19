import { useState, useMemo, useRef, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useProjects } from '../hooks/useProjects'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Project } from '../api/types'
import { IconSearch, IconChevronDown } from '../components/common/Icons'
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
  const [searchParams, setSearchParams] = useSearchParams()
  const statusFilter = searchParams.get('status') || 'active'
  const searchQuery = searchParams.get('q') || ''
  const viewMode = (searchParams.get('view') as 'table' | 'cards') || 'cards'

  const updateFilter = (key: string, value: string, opts?: { replace?: boolean }) => {
    const newParams = new URLSearchParams(searchParams)
    if (!value) newParams.delete(key)
    else newParams.set(key, value)
    setSearchParams(newParams, opts)
  }

  const [showForm, setShowForm] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [layoutOpen, setLayoutOpen] = useState(false)
  const layoutRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (layoutRef.current && !layoutRef.current.contains(e.target as Node)) setLayoutOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

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
    if (statusFilter && statusFilter !== 'all') result = result.filter(p => p.status === statusFilter)
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
    <div className="projects-page page-scroll-layout">
      <div className="page-header">
        <h1>Projects</h1>
        <div className="page-header-actions">
          <div className="pp-layout-picker" ref={layoutRef}>
            <button
              type="button"
              className={`pp-layout-trigger${layoutOpen ? ' open' : ''}`}
              onClick={() => setLayoutOpen(o => !o)}
            >
              {/* Inline mini-skeleton icon showing current mode */}
              {viewMode === 'cards' ? (
                <svg className="pp-trigger-icon" width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <rect x="1" y="1" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.5" />
                  <rect x="9" y="1" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.3" />
                  <rect x="1" y="9" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.3" />
                  <rect x="9" y="9" width="6" height="6" rx="1.5" fill="currentColor" opacity="0.5" />
                </svg>
              ) : (
                <svg className="pp-trigger-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <line x1="1" y1="3" x2="15" y2="3" opacity="0.5" />
                  <line x1="1" y1="6.5" x2="12" y2="6.5" opacity="0.35" />
                  <line x1="1" y1="10" x2="14" y2="10" opacity="0.35" />
                  <line x1="1" y1="13.5" x2="10" y2="13.5" opacity="0.35" />
                </svg>
              )}
              <span className="pp-trigger-label">{viewMode === 'cards' ? 'Cards' : 'Table'}</span>
              <IconChevronDown size={11} className={`pp-layout-chevron${layoutOpen ? ' open' : ''}`} />
            </button>
            {layoutOpen && (
              <div className="pp-layout-popover">
                <div className="pp-layout-options">
                  <button
                    type="button"
                    className={`pp-layout-card${viewMode === 'cards' ? ' active' : ''}`}
                    onClick={() => { updateFilter('view', ''); setLayoutOpen(false) }}
                  >
                    {/* Cards skeleton: 2x2 grid of card shapes */}
                    <div className="pp-skel pp-skel-cards">
                      <div className="pp-skel-card"><div className="pp-skel-line w60" /><div className="pp-skel-line w40" /><div className="pp-skel-bar" /></div>
                      <div className="pp-skel-card"><div className="pp-skel-line w50" /><div className="pp-skel-line w70" /><div className="pp-skel-bar" /></div>
                      <div className="pp-skel-card"><div className="pp-skel-line w70" /><div className="pp-skel-line w40" /><div className="pp-skel-bar" /></div>
                      <div className="pp-skel-card"><div className="pp-skel-line w40" /><div className="pp-skel-line w60" /><div className="pp-skel-bar" /></div>
                    </div>
                    <span className="pp-layout-label">Cards</span>
                  </button>
                  <button
                    type="button"
                    className={`pp-layout-card${viewMode === 'table' ? ' active' : ''}`}
                    onClick={() => { updateFilter('view', 'table'); setLayoutOpen(false) }}
                  >
                    {/* Table skeleton: header + rows */}
                    <div className="pp-skel pp-skel-table">
                      <div className="pp-skel-row header"><div className="pp-skel-cell w30" /><div className="pp-skel-cell w20" /><div className="pp-skel-cell w25" /><div className="pp-skel-cell w15" /></div>
                      <div className="pp-skel-row"><div className="pp-skel-cell w35" /><div className="pp-skel-cell w15" /><div className="pp-skel-cell w20" /><div className="pp-skel-cell w20" /></div>
                      <div className="pp-skel-row"><div className="pp-skel-cell w25" /><div className="pp-skel-cell w20" /><div className="pp-skel-cell w30" /><div className="pp-skel-cell w10" /></div>
                      <div className="pp-skel-row"><div className="pp-skel-cell w30" /><div className="pp-skel-cell w15" /><div className="pp-skel-cell w20" /><div className="pp-skel-cell w25" /></div>
                    </div>
                    <span className="pp-layout-label">Table</span>
                  </button>
                </div>
              </div>
            )}
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => setShowForm(true)}>
            + New Project
          </button>
        </div>
      </div>

      {/* Status pills + inline search */}
      {projects.length > 0 && (
        <div className="pp-filter-bar">
          <button
            className={`pp-filter-pill${statusFilter === 'all' ? ' active' : ''}`}
            onClick={() => updateFilter('status', 'all')}
            type="button"
          >
            <span className="pp-filter-dot" style={{ background: 'var(--text-muted)' }} />
            <span className="pp-filter-pill-count">{projects.length}</span>
            <span className="pp-filter-pill-label">All</span>
          </button>
          {STATUSES.filter(s => statusCounts[s]).map(status => (
            <button
              key={status}
              className={`pp-filter-pill${statusFilter === status ? ' active' : ''}`}
              onClick={() => updateFilter('status', statusFilter === status ? 'all' : status)}
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
              onChange={e => updateFilter('q', e.target.value, { replace: true })}
            />
            {searchQuery && (
              <button
                className="pp-search-inline-clear"
                onMouseDown={e => { e.preventDefault(); updateFilter('q', '') }}
                type="button"
              >&times;</button>
            )}
          </div>
        </div>
      )}

      <div className="page-content">
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

      </div>
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
