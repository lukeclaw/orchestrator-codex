import { useState, useEffect, useRef } from 'react'
import { useApp } from '../context/AppContext'
import { useContextItems } from '../hooks/useContextItems'
import type { ContextItem } from '../api/types'
import ConfirmPopover from '../components/common/ConfirmPopover'
import './ContextPage.css'

const CATEGORIES = ['requirement', 'convention', 'reference', 'note']

export default function ContextPage() {
  const { projects } = useApp()
  const [scopeFilter, setScopeFilter] = useState<string>('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [projectFilter, setProjectFilter] = useState<string>('')
  const [searchText, setSearchText] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editingItem, setEditingItem] = useState<ContextItem | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const { items, loading, fetch, create, update, remove } = useContextItems({
    scope: scopeFilter || undefined,
    project_id: projectFilter || undefined,
    category: categoryFilter || undefined,
    search: debouncedSearch || undefined,
  })

  useEffect(() => {
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(searchText)
    }, 300)
    return () => clearTimeout(searchTimer.current)
  }, [searchText])

  // Form state
  const [formTitle, setFormTitle] = useState('')
  const [formContent, setFormContent] = useState('')
  const [formScope, setFormScope] = useState<'global' | 'project'>('global')
  const [formProjectId, setFormProjectId] = useState('')
  const [formCategory, setFormCategory] = useState('')
  const [formSource, setFormSource] = useState('user')

  function resetForm() {
    setFormTitle('')
    setFormContent('')
    setFormScope('global')
    setFormProjectId('')
    setFormCategory('')
    setFormSource('user')
    setEditingItem(null)
    setShowForm(false)
  }

  function openEdit(item: ContextItem) {
    setFormTitle(item.title)
    setFormContent(item.content)
    setFormScope(item.scope as 'global' | 'project')
    setFormProjectId(item.project_id || '')
    setFormCategory(item.category || '')
    setFormSource(item.source || 'user')
    setEditingItem(item)
    setShowForm(true)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!formTitle.trim() || !formContent.trim()) return

    const body = {
      title: formTitle.trim(),
      content: formContent.trim(),
      scope: formScope,
      project_id: formScope === 'project' ? formProjectId || undefined : undefined,
      category: formCategory || undefined,
      source: formSource || undefined,
    }

    if (editingItem) {
      await update(editingItem.id, body)
    } else {
      await create(body)
    }
    resetForm()
  }

  async function handleDelete(id: string) {
    await remove(id)
    if (expandedId === id) setExpandedId(null)
  }

  function formatDate(dateStr: string) {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  function getProjectName(projectId: string | null) {
    if (!projectId) return null
    const p = projects.find(x => x.id === projectId)
    return p?.name || projectId.slice(0, 8)
  }

  return (
    <div className="context-page">
      <div className="cp-header">
        <h1>Context</h1>
        <button className="btn btn-primary btn-sm" onClick={() => { resetForm(); setShowForm(true) }}>
          + Add Context
        </button>
      </div>

      {/* Search + Filters */}
      <div className="cp-filters">
        <input
          className="cp-search"
          type="text"
          placeholder="Search context..."
          value={searchText}
          onChange={e => setSearchText(e.target.value)}
          data-testid="context-search"
        />
        <select value={scopeFilter} onChange={e => setScopeFilter(e.target.value)}>
          <option value="">All Scopes</option>
          <option value="global">Global</option>
          <option value="project">Project</option>
        </select>
        <select value={categoryFilter} onChange={e => setCategoryFilter(e.target.value)}>
          <option value="">All Categories</option>
          {CATEGORIES.map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {scopeFilter === 'project' && (
          <select value={projectFilter} onChange={e => setProjectFilter(e.target.value)}>
            <option value="">All Projects</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        )}
      </div>

      {/* Form */}
      {showForm && (
        <form className="cp-form panel" onSubmit={handleSubmit} data-testid="context-form">
          <h3>{editingItem ? 'Edit Context' : 'New Context'}</h3>
          <input
            type="text"
            placeholder="Title"
            value={formTitle}
            onChange={e => setFormTitle(e.target.value)}
            required
            data-testid="context-title-input"
          />
          <textarea
            placeholder="Content..."
            value={formContent}
            onChange={e => setFormContent(e.target.value)}
            rows={5}
            required
            data-testid="context-content-input"
          />
          <div className="cp-form-row">
            <select value={formScope} onChange={e => setFormScope(e.target.value as 'global' | 'project')}>
              <option value="global">Global</option>
              <option value="project">Project</option>
            </select>
            {formScope === 'project' && (
              <select value={formProjectId} onChange={e => setFormProjectId(e.target.value)} required>
                <option value="">Select project...</option>
                {projects.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            )}
            <select value={formCategory} onChange={e => setFormCategory(e.target.value)}>
              <option value="">No category</option>
              {CATEGORIES.map(c => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <select value={formSource} onChange={e => setFormSource(e.target.value)}>
              <option value="user">user</option>
              <option value="brain">brain</option>
              <option value="worker">worker</option>
            </select>
          </div>
          <div className="cp-form-actions">
            <button type="submit" className="btn btn-primary btn-sm">
              {editingItem ? 'Save' : 'Create'}
            </button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={resetForm}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Items list */}
      {loading ? (
        <p className="empty-state">Loading...</p>
      ) : items.length === 0 ? (
        <p className="empty-state">No context items found.</p>
      ) : (
        <div className="cp-list" data-testid="context-list">
          {items.map(item => (
            <div
              key={item.id}
              className={`cp-card panel ${expandedId === item.id ? 'expanded' : ''}`}
              data-testid="context-card"
            >
              <div className="cp-card-header" onClick={() => setExpandedId(expandedId === item.id ? null : item.id)}>
                <div className="cp-card-title">
                  <span className={`cp-scope-badge ${item.scope}`}>
                    {item.scope === 'global' ? 'Global' : getProjectName(item.project_id) || 'Project'}
                  </span>
                  {item.category && <span className="cp-category-tag">{item.category}</span>}
                  <strong>{item.title}</strong>
                </div>
                <div className="cp-card-meta">
                  {item.source && <span className="cp-source">{item.source}</span>}
                  <span className="cp-time">{formatDate(item.updated_at)}</span>
                </div>
              </div>
              {expandedId === item.id ? (
                <div className="cp-card-body">
                  <pre className="cp-content">{item.content}</pre>
                  <div className="cp-card-actions">
                    <button className="btn btn-secondary btn-sm" onClick={() => openEdit(item)}>Edit</button>
                    <ConfirmPopover
                      message={`Delete "${item.title}"?`}
                      confirmLabel="Delete"
                      onConfirm={() => handleDelete(item.id)}
                      variant="danger"
                    >
                      {({ onClick }) => (
                        <button className="btn btn-danger btn-sm" onClick={onClick}>Delete</button>
                      )}
                    </ConfirmPopover>
                  </div>
                </div>
              ) : (
                <p className="cp-preview">{item.content.slice(0, 200)}{item.content.length > 200 ? '...' : ''}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
