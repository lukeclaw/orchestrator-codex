import { useState, useEffect, useRef, useMemo } from 'react'
import { useApp } from '../context/AppContext'
import { useContextItems } from '../hooks/useContextItems'
import type { ContextItem } from '../api/types'
import { timeAgo, parseDate } from '../components/common/TimeAgo'
import ContextModal from '../components/context/ContextModal'
import './ContextPage.css'

type SortKey = 'title' | 'scope' | 'category' | 'project' | 'updated'
type SortDir = 'asc' | 'desc'

const CATEGORIES = ['instruction', 'requirement', 'convention', 'reference', 'note']
const SCOPE_ORDER: Record<string, number> = { global: 2, brain: 1, project: 0 }

export default function ContextPage() {
  const { projects } = useApp()
  const [scopeFilter, setScopeFilter] = useState<string>('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [projectFilter, setProjectFilter] = useState<string>('')
  const [searchText, setSearchText] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [selectedContext, setSelectedContext] = useState<ContextItem | null>(null)
  const [showNewContext, setShowNewContext] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('scope')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
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

  const projectMap = useMemo(() => new Map(projects.map(p => [p.id, p])), [projects])
  const getProjectName = (id: string | null) => id ? projectMap.get(id)?.name || id.slice(0, 8) : null

  function getSortValue(item: ContextItem, key: SortKey): string | number {
    switch (key) {
      case 'title': return item.title.toLowerCase()
      case 'scope': return SCOPE_ORDER[item.scope] ?? 0
      case 'category': return item.category || ''
      case 'project': return getProjectName(item.project_id)?.toLowerCase() || ''
      case 'updated': return parseDate(item.updated_at || item.created_at).getTime()
      default: return 0
    }
  }

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      const aVal = getSortValue(a, sortKey)
      const bVal = getSortValue(b, sortKey)
      const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [items, sortKey, sortDir])

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'title' ? 'asc' : 'desc')
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

  async function handleItemClick(item: ContextItem) {
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

  const hasFilters = scopeFilter || categoryFilter || projectFilter || searchText

  return (
    <div className="context-page">
      <div className="page-header">
        <h1>Context</h1>
        <div className="page-header-actions">
          <span className="ctx-count">{sortedItems.length} items</span>
          <button className="btn btn-primary btn-sm" onClick={() => setShowNewContext(true)}>
            + Add Context
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="ctx-filters">
        <input
          className="search-input"
          type="text"
          placeholder="Search context..."
          value={searchText}
          onChange={e => setSearchText(e.target.value)}
          data-testid="context-search"
        />
        <select className="filter-select" value={scopeFilter} onChange={e => setScopeFilter(e.target.value)}>
          <option value="">All Scopes</option>
          <option value="global">Global</option>
          <option value="brain">Brain</option>
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
        {hasFilters && (
          <button
            className="btn btn-sm btn-secondary"
            onClick={() => { setScopeFilter(''); setCategoryFilter(''); setProjectFilter(''); setSearchText('') }}
          >
            Clear Filters
          </button>
        )}
      </div>

      {/* Table */}
      {loading ? (
        <p className="empty-state">Loading...</p>
      ) : sortedItems.length === 0 ? (
        <p className="empty-state">No context items found.</p>
      ) : (
        <div className="ctx-table-wrapper">
          <table className="pt-table">
            <thead>
              <tr>
                <SortHeader k="title">Title</SortHeader>
                <SortHeader k="scope">Scope</SortHeader>
                <SortHeader k="category">Category</SortHeader>
                <SortHeader k="project">Project</SortHeader>
                <SortHeader k="updated">Updated</SortHeader>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map(item => {
                const projName = getProjectName(item.project_id)
                return (
                  <tr
                    key={item.id}
                    className="pt-row"
                    onClick={() => handleItemClick(item)}
                    data-testid="context-card"
                  >
                    <td className="pt-td ctx-title-cell">
                      <span className="ctx-title">{item.title}</span>
                      {item.description && (
                        <span className="ctx-desc">{item.description}</span>
                      )}
                    </td>
                    <td className="pt-td">
                      <span className={`ctx-scope-badge ${item.scope}`}>
                        {item.scope === 'global' ? 'Global' : item.scope === 'brain' ? 'Brain' : 'Project'}
                      </span>
                    </td>
                    <td className="pt-td">
                      {item.category ? (
                        <span className={`cm-badge cm-cat-${item.category}`}>{item.category}</span>
                      ) : '—'}
                    </td>
                    <td className="pt-td ctx-project">
                      {projName || '—'}
                    </td>
                    <td className="pt-td date">
                      {timeAgo(item.updated_at || item.created_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
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
