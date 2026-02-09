import { useState, useEffect, useRef } from 'react'
import { useApp } from '../context/AppContext'
import { useContextItems } from '../hooks/useContextItems'
import type { ContextItem } from '../api/types'
import ContextModal from '../components/context/ContextModal'
import './ContextPage.css'

const CATEGORIES = ['instruction', 'requirement', 'convention', 'reference', 'note']

export default function ContextPage() {
  const { projects } = useApp()
  const [scopeFilter, setScopeFilter] = useState<string>('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [projectFilter, setProjectFilter] = useState<string>('')
  const [searchText, setSearchText] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [selectedContext, setSelectedContext] = useState<ContextItem | null>(null)
  const [showNewContext, setShowNewContext] = useState(false)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const { items, loading, fetch, getItem, create, update, remove } = useContextItems({
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

  function formatDate(dateStr: string) {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }

  function getProjectName(projectId: string | null) {
    if (!projectId) return null
    const p = projects.find(x => x.id === projectId)
    return p?.name || projectId.slice(0, 8)
  }

  async function handleItemClick(item: ContextItem) {
    // Fetch full content if not already loaded
    if (!item.content) {
      const fullItem = await getItem(item.id)
      setSelectedContext(fullItem)
    } else {
      setSelectedContext(item)
    }
  }

  async function handleSave(body: Partial<ContextItem> & { title: string; content: string }) {
    if (body.id) {
      await update(body.id, body)
    } else {
      await create(body as Parameters<typeof create>[0])
    }
    fetch()
  }

  async function handleDelete(id: string) {
    await remove(id)
  }

  return (
    <div className="context-page">
      <div className="cp-header">
        <h1>Context</h1>
        <button className="btn btn-primary btn-sm" onClick={() => setShowNewContext(true)}>
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
        <select className="filter-select" value={scopeFilter} onChange={e => setScopeFilter(e.target.value)}>
          <option value="">All Scopes</option>
          <option value="global">Global</option>
          <option value="brain">Brain only</option>
          <option value="project">Project</option>
        </select>
        <select className="filter-select" value={categoryFilter} onChange={e => setCategoryFilter(e.target.value)}>
          <option value="">All Categories</option>
          {CATEGORIES.map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {(scopeFilter === 'project' || projectFilter) && (
          <select className="filter-select" value={projectFilter} onChange={e => setProjectFilter(e.target.value)}>
            <option value="">All Projects</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        )}
      </div>

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
              className="cp-item clickable"
              onClick={() => handleItemClick(item)}
              data-testid="context-card"
            >
              <div className="cp-item-header">
                <span className={`cp-scope-badge ${item.scope}`}>
                  {item.scope === 'global' ? 'Global' : item.scope === 'brain' ? 'Brain' : getProjectName(item.project_id) || 'Project'}
                </span>
                {item.category && <span className="cp-category-tag">{item.category}</span>}
                <strong className="cp-item-title">{item.title}</strong>
                <span className="cp-item-time">{formatDate(item.updated_at)}</span>
              </div>
              {(item.description || item.content) && (
                <p className="cp-item-desc">
                  {item.description || (item.content?.slice(0, 150) || '') + ((item.content?.length || 0) > 150 ? '...' : '')}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      <ContextModal
        context={selectedContext}
        isNew={showNewContext}
        onClose={() => {
          setSelectedContext(null)
          setShowNewContext(false)
        }}
        onSave={handleSave}
        onDelete={handleDelete}
      />
    </div>
  )
}
