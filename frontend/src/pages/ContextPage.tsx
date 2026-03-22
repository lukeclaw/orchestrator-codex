import { useState, useEffect, useRef, useMemo } from 'react'
import { useApp } from '../context/AppContext'
import { useContextItems } from '../hooks/useContextItems'
import { useBrainMemory } from '../hooks/useBrainMemory'
import type { ContextItem } from '../api/types'
import { timeAgo, parseDate } from '../components/common/TimeAgo'
import { IconContext, IconFilter, IconSearch } from '../components/common/Icons'
import SlidingTabs from '../components/common/SlidingTabs'
import ContextModal from '../components/context/ContextModal'
import './ContextPage.css'

type SortKey = 'title' | 'scope' | 'category' | 'project' | 'updated'
type SortDir = 'asc' | 'desc'

const SCOPE_ORDER: Record<string, number> = { global: 2, brain: 1, project: 0 }
const SCOPE_COLORS: Record<string, string> = { global: 'var(--status-working)', brain: 'var(--purple)', project: 'var(--status-idle)' }
const SCOPE_LABELS: Record<string, string> = { global: 'Global', brain: 'Brain', project: 'Project' }
const SCOPES = ['global', 'brain', 'project'] as const

export default function ContextPage() {
  const { projects } = useApp()
  const [activeTab, setActiveTab] = useState<'context' | 'brain-memory'>('context')
  const [scopeFilter, setScopeFilter] = useState<string>('')
  const [projectFilter, setProjectFilter] = useState<string>('')
  const [searchText, setSearchText] = useState('')
  const [selectedContext, setSelectedContext] = useState<ContextItem | null>(null)
  const [showNewContext, setShowNewContext] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>('scope')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [showProjectDropdown, setShowProjectDropdown] = useState(false)
  const projectThRef = useRef<HTMLTableCellElement>(null)
  const [memorySearch, setMemorySearch] = useState('')

  const { items, loading, fetch, getItem, create, update, remove } = useContextItems({
    project_id: projectFilter || undefined,
    excludeScopeCategories: [
      { scope: 'brain', category: 'memory' },
      { scope: 'brain', category: 'wisdom' },
    ],
  })

  const { logs, wisdom, loading: memoryLoading, searchLogs } = useBrainMemory()

  useEffect(() => {
    if (!showProjectDropdown) return
    function handleClickOutside(e: MouseEvent) {
      if (projectThRef.current && !projectThRef.current.contains(e.target as Node)) {
        setShowProjectDropdown(false)
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setShowProjectDropdown(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [showProjectDropdown])

  const projectMap = useMemo(() => new Map(projects.map(p => [p.id, p])), [projects])
  const getProjectName = (id: string | null) => id ? projectMap.get(id)?.name || id.slice(0, 8) : null

  // Scope counts computed from all items (before scope/search filter)
  const scopeCounts = useMemo(() => {
    return items.reduce<Record<string, number>>((acc, item) => {
      acc[item.scope] = (acc[item.scope] || 0) + 1
      return acc
    }, {})
  }, [items])

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

  // Apply scope + search filters client-side, then sort
  const sortedItems = useMemo(() => {
    let filtered = items
    if (scopeFilter) filtered = filtered.filter(i => i.scope === scopeFilter)
    if (searchText) {
      const q = searchText.toLowerCase()
      filtered = filtered.filter(i =>
        i.title.toLowerCase().includes(q) ||
        (i.description && i.description.toLowerCase().includes(q))
      )
    }
    return [...filtered].sort((a, b) => {
      const aVal = getSortValue(a, sortKey)
      const bVal = getSortValue(b, sortKey)
      const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [items, scopeFilter, searchText, sortKey, sortDir])

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
        <svg
          className={`sort-chevron${active && sortDir === 'asc' ? ' asc' : ''}`}
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </th>
    )
  }

  // Project header: sort + filter dropdown
  function ProjectHeader() {
    const active = sortKey === 'project'
    const projName = projectFilter ? getProjectName(projectFilter) : null
    return (
      <th
        ref={projectThRef}
        className={`pt-th sortable ${active ? 'active' : ''} ctx-th-project`}
      >
        <span className="ctx-th-project-row" onClick={() => handleSort('project')}>
          {projName || 'Project'}
          <svg
            className={`sort-chevron${active && sortDir === 'asc' ? ' asc' : ''}`}
            width="10"
            height="10"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </span>
        <button
          className={`ctx-th-icon-btn${projectFilter ? ' active' : ''}`}
          onClick={e => { e.stopPropagation(); setShowProjectDropdown(p => !p) }}
          type="button"
          title="Filter by project"
        >
          <IconFilter size={13} />
        </button>
        {showProjectDropdown && (
          <div className="ctx-project-dropdown">
            <button
              className={`ctx-project-option${!projectFilter ? ' selected' : ''}`}
              onClick={() => { setProjectFilter(''); setShowProjectDropdown(false) }}
              type="button"
            >All Projects</button>
            {projects.map(p => (
              <button
                key={p.id}
                className={`ctx-project-option${projectFilter === p.id ? ' selected' : ''}`}
                onClick={() => { setProjectFilter(p.id); setShowProjectDropdown(false) }}
                type="button"
              >{p.name}</button>
            ))}
          </div>
        )}
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

  const hasFilters = scopeFilter || projectFilter || searchText

  function clearAllFilters() {
    setScopeFilter('')
    setProjectFilter('')
    setSearchText('')
  }

  return (
    <div className="context-page page-scroll-layout">
      {/* Header: title + tabs + actions */}
      <div className="page-header">
        <h1>Context</h1>
        <SlidingTabs
          tabs={[
            { value: 'context' as const, label: 'Context' },
            { value: 'brain-memory' as const, label: 'Brain Memory' },
          ]}
          value={activeTab}
          onChange={setActiveTab}
        />
        <div className="page-header-actions">
          {activeTab === 'context' && (
            <button className="btn btn-primary btn-sm" onClick={() => setShowNewContext(true)}>
              + Add Context
            </button>
          )}
        </div>
      </div>

      {/* === Brain Memory Tab === */}
      {activeTab === 'brain-memory' && (
        <>
          <div className="bm-notice">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
            <span>
              The brain's learning journal. It captures learnings during work sessions and curates
              them into a wisdom document injected into its prompt on every start. These entries are
              managed by the brain — you can review what it has learned here.
            </span>
          </div>
          <div className="page-content">
            {memoryLoading ? (
              <p className="empty-state">Loading...</p>
            ) : (
              <>
                {/* Wisdom section */}
                <div className="bm-section">
                  <div className="bm-section-title">Wisdom</div>
                  {wisdom ? (
                    <div className="bm-wisdom-panel">
                      <div className="bm-wisdom-content">{wisdom.content}</div>
                      <div className="bm-wisdom-meta">
                        Last updated {timeAgo(wisdom.updated_at || wisdom.created_at)}
                      </div>
                    </div>
                  ) : (
                    <div className="bm-wisdom-empty">
                      No wisdom document yet. The brain will create one as it accumulates learnings.
                    </div>
                  )}
                </div>

                {/* Learning logs section */}
                <div className="bm-section">
                  <div className="bm-section-header">
                    <div className="bm-section-title">Learning Logs</div>
                    <div className="bm-search">
                      <IconSearch size={13} className="bm-search-icon" />
                      <input
                        type="text"
                        placeholder="Search logs..."
                        value={memorySearch}
                        onChange={e => {
                          setMemorySearch(e.target.value)
                          searchLogs(e.target.value)
                        }}
                      />
                      {memorySearch && (
                        <button
                          className="bm-search-clear"
                          onMouseDown={e => { e.preventDefault(); setMemorySearch(''); searchLogs('') }}
                          type="button"
                        >&times;</button>
                      )}
                    </div>
                  </div>
                  {logs.length === 0 ? (
                    <div className="bm-logs-empty">
                      {memorySearch ? 'No logs match your search.' : 'No learning logs yet. The brain will capture learnings as it works.'}
                    </div>
                  ) : (
                    <div className="bm-log-list">
                      {logs.map(log => (
                        <div key={log.id} className="bm-log-item">
                          {log.title && <div className="bm-log-title">{log.title}</div>}
                          <div className="bm-log-content">{log.content}</div>
                          <div className="bm-log-meta">{timeAgo(log.created_at)}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </>
      )}

      {/* === Context Tab === */}
      {activeTab === 'context' && items.length > 0 && (
        <div className="ctx-scope-bar">
          <button
            className={`ctx-scope-pill${!scopeFilter ? ' active' : ''}`}
            onClick={() => setScopeFilter('')}
            type="button"
          >
            <span className="ctx-scope-dot" style={{ background: 'var(--text-muted)' }} />
            <span className="ctx-scope-pill-count">{items.length}</span>
            <span className="ctx-scope-pill-label">All</span>
          </button>
          {SCOPES.filter(s => scopeCounts[s]).map(scope => (
            <button
              key={scope}
              className={`ctx-scope-pill${scopeFilter === scope ? ' active' : ''}`}
              onClick={() => setScopeFilter(scopeFilter === scope ? '' : scope)}
              type="button"
            >
              <span className="ctx-scope-dot" style={{ background: SCOPE_COLORS[scope] }} />
              <span className="ctx-scope-pill-count">{scopeCounts[scope]}</span>
              <span className="ctx-scope-pill-label">{SCOPE_LABELS[scope]}</span>
            </button>
          ))}
          <div className="ctx-search-inline">
            <IconSearch size={13} className="ctx-search-inline-icon" />
            <input
              className="ctx-search-inline-input"
              type="text"
              placeholder="Filter..."
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              data-testid="context-search"
            />
            {searchText && (
              <button
                className="ctx-search-inline-clear"
                onMouseDown={e => { e.preventDefault(); setSearchText('') }}
                type="button"
              >&times;</button>
            )}
          </div>
        </div>
      )}

      {activeTab === 'context' && (
      <div className="page-content">
      {loading ? (
        <p className="empty-state">Loading...</p>
      ) : sortedItems.length === 0 ? (
        <div className="ctx-empty-state">
          {hasFilters ? (
            <>
              <IconFilter size={32} />
              <p>No context items match your filters.</p>
              <button className="btn btn-secondary" onClick={clearAllFilters}>
                Clear Filters
              </button>
            </>
          ) : (
            <>
              <IconContext size={48} />
              <h3>No context items yet</h3>
              <p>Add context to give your AI workers instructions, references, and project knowledge.</p>
              <button className="btn btn-primary" onClick={() => setShowNewContext(true)}>
                + Add Context
              </button>
            </>
          )}
        </div>
      ) : (
        <div className="ctx-table-wrapper">
          <table className="pt-table">
            <thead>
              <tr>
                <SortHeader k="title">Title</SortHeader>
                <SortHeader k="scope">Scope</SortHeader>
                <SortHeader k="category">Category</SortHeader>
                <ProjectHeader />
                <SortHeader k="updated">Updated</SortHeader>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map(item => {
                const projName = getProjectName(item.project_id)
                return (
                  <tr
                    key={item.id}
                    className={`pt-row ctx-row ctx-scope-${item.scope}`}
                    onClick={() => handleItemClick(item)}
                    data-testid="context-card"
                  >
                    <td className="pt-td ctx-title-cell">
                      <div className="ctx-title-inner">
                        <span className="ctx-title">{item.title}</span>
                        <span className={`ctx-desc${item.description ? '' : ' empty'}`}>
                          {item.description || 'No description'}
                        </span>
                      </div>
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
      </div>
      )}

      <ContextModal
        context={selectedContext}
        projects={projects}
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
