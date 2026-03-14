import { useState, useEffect, useRef, useMemo, useCallback, Fragment } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, openUrl } from '../api/client'
import type { PrSearchItem, PrSearchResponse } from '../api/types'
import { useApp } from '../context/AppContext'
import SlidingTabs from '../components/common/SlidingTabs'
import { IconPullRequest, IconRefresh, IconSearch, IconX } from '../components/common/Icons'
import { timeAgo, parseDate } from '../components/common/TimeAgo'
import PrPreviewCard from '../components/tasks/PrPreviewCard'
import './PRsPage.css'

type Tab = 'active' | 'recent'
type SortField = 'attention' | 'pr' | 'status' | 'task' | 'updated'
type SortDir = 'asc' | 'desc'
type SubFilter = 'all' | 'merged' | 'closed'
type AttentionFilter = 1 | 2 | 3 | 4 | null

const TABS = [
  { value: 'active' as Tab, label: 'Active' },
  { value: 'recent' as Tab, label: 'Recent' },
]

const CACHE_TTL = 10 * 60 * 1000 // 10 minutes

const ATTENTION_PILLS: { level: 1 | 2 | 3 | 4; label: string; dotClass: string }[] = [
  { level: 1, label: 'Needs action', dotClass: 'prs-dot-red' },
  { level: 2, label: 'Ready to ship', dotClass: 'prs-dot-green' },
  { level: 3, label: 'In review', dotClass: 'prs-dot-accent' },
  { level: 4, label: 'Draft', dotClass: 'prs-dot-gray' },
]

const SUB_FILTER_COLORS: Record<SubFilter, string> = {
  all: 'var(--text-muted)',
  merged: 'var(--purple)',
  closed: 'var(--red)',
}

interface TabCache {
  prs: PrSearchItem[]
  fetchedAt: number
}

function renderReviewChip(pr: PrSearchItem) {
  if (pr.draft) return <span className="pr-status-chip pr-chip-gray">Draft</span>
  if (pr.state !== 'open') return null
  switch (pr.review_decision) {
    case 'approved':
      if (pr.attention_level === 2) {
        return <span className="pr-status-chip pr-chip-green">Approved</span>
      }
      return <span className="pr-status-chip pr-chip-gray">Reviewed</span>
    case 'changes_requested':
      return <span className="pr-status-chip pr-chip-red">Changes requested</span>
    case 'review_required':
      return <span className="pr-status-chip pr-chip-yellow">Pending review</span>
    default:
      if (pr.review_requests.length > 0) {
        return <span className="pr-status-chip pr-chip-yellow">Pending review</span>
      }
      return <span className="pr-status-chip pr-chip-gray">No reviewers</span>
  }
}

function renderCIChip(pr: PrSearchItem) {
  if (pr.draft || pr.state !== 'open') return null
  switch (pr.ci_state) {
    case 'success':
      return <span className="pr-status-chip pr-chip-green">CI passing</span>
    case 'failure':
      return <span className="pr-status-chip pr-chip-red">CI failing</span>
    case 'pending':
      return <span className="pr-status-chip pr-chip-yellow">CI running</span>
    default:
      return null
  }
}

export default function PRsPage() {
  const { setPrBadgeCount } = useApp()
  const [tab, setTab] = useState<Tab>('active')
  const [days, setDays] = useState(7)
  const [prs, setPrs] = useState<PrSearchItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedUrl, setExpandedUrl] = useState<string | null>(null)
  const [sortField, setSortField] = useState<SortField>('attention')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [filterText, setFilterText] = useState('')
  const [attentionFilter, setAttentionFilter] = useState<AttentionFilter>(null)
  const [subFilter, setSubFilter] = useState<SubFilter>('all')
  const [fetchedAt, setFetchedAt] = useState<number | null>(null)
  const [, setTick] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const cacheRef = useRef<Record<string, TabCache>>({})

  const cacheKey = tab === 'active' ? 'active' : `recent:${days}`

  const fetchPrs = useCallback(async (refresh = false) => {
    abortRef.current?.abort()

    // Check frontend cache (skip on manual refresh)
    if (!refresh) {
      const cached = cacheRef.current[cacheKey]
      if (cached && Date.now() - cached.fetchedAt < CACHE_TTL) {
        setPrs(cached.prs)
        setFetchedAt(cached.fetchedAt)
        setLoading(false)
        setError(null)
        setPrBadgeCount(cached.prs.filter(p => p.attention_level === 1).length)
        return
      }
    }

    const ctrl = new AbortController()
    abortRef.current = ctrl

    setLoading(true)
    setError(null)
    setPrBadgeCount(0)

    try {
      const params = new URLSearchParams({ tab })
      if (tab === 'recent') params.set('days', String(days))
      if (refresh) params.set('refresh', 'true')

      const data = await api<PrSearchResponse>(`/api/prs?${params}`, { signal: ctrl.signal })
      if (ctrl.signal.aborted) return

      const now = Date.now()
      setPrs(data.prs)
      setFetchedAt(now)
      setLoading(false)
      setPrBadgeCount(data.prs.filter(p => p.attention_level === 1).length)

      cacheRef.current[cacheKey] = {
        prs: data.prs,
        fetchedAt: now,
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return
      if (e instanceof ApiError && e.status === 401) {
        setError('auth')
      } else {
        setError(e instanceof Error ? e.message : 'Failed to fetch PRs')
      }
      setLoading(false)
    }
  }, [tab, days, cacheKey, setPrBadgeCount])

  const handleTabChange = useCallback((newTab: Tab) => {
    setTab(newTab)
    setAttentionFilter(null)
    setSubFilter('all')
    setFilterText('')
    setSortField(newTab === 'active' ? 'attention' : 'updated')
    setSortDir(newTab === 'active' ? 'asc' : 'desc')
  }, [])

  useEffect(() => {
    fetchPrs()
    return () => { abortRef.current?.abort() }
  }, [fetchPrs])

  // Tick every 30s to keep "fetched X ago" fresh
  useEffect(() => {
    if (fetchedAt === null) return
    const id = setInterval(() => setTick(t => t + 1), 30_000)
    return () => clearInterval(id)
  }, [fetchedAt])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDir(field === 'updated' ? 'desc' : 'asc')
    }
  }

  const sortIndicator = (field: SortField) => {
    if (sortField !== field) return <span className="sort-indicator">&uarr;&darr;</span>
    return <span className="sort-indicator active">{sortDir === 'asc' ? '\u2191' : '\u2193'}</span>
  }

  // Filter + sort
  const filteredPrs = useMemo(() => {
    let result = [...prs]

    // Attention filter (active tab)
    if (tab === 'active' && attentionFilter !== null) {
      result = result.filter(p => p.attention_level === attentionFilter)
    }

    // Sub-filter (recent tab)
    if (tab === 'recent') {
      if (subFilter === 'merged') result = result.filter(p => p.merged_at != null)
      else if (subFilter === 'closed') result = result.filter(p => p.state === 'closed' && !p.merged_at)
    }

    // Text filter
    if (filterText) {
      const q = filterText.toLowerCase()
      result = result.filter(p =>
        p.repo.toLowerCase().includes(q) ||
        p.title.toLowerCase().includes(q) ||
        `#${p.number}`.includes(q)
      )
    }

    // Sort
    result.sort((a, b) => {
      let cmp = 0
      switch (sortField) {
        case 'attention':
          cmp = a.attention_level - b.attention_level
          if (cmp === 0) cmp = parseDate(b.updated_at).getTime() - parseDate(a.updated_at).getTime()
          break
        case 'pr':
          cmp = a.repo.localeCompare(b.repo) || a.number - b.number
          break
        case 'status':
          cmp = a.attention_level - b.attention_level
          break
        case 'task':
          cmp = (a.linked_task ? 0 : 1) - (b.linked_task ? 0 : 1) ||
            (a.linked_task?.task_key || '').localeCompare(b.linked_task?.task_key || '')
          break
        case 'updated':
          cmp = parseDate(a.updated_at).getTime() - parseDate(b.updated_at).getTime()
          break
      }
      return sortDir === 'asc' ? cmp : -cmp
    })

    return result
  }, [prs, tab, attentionFilter, subFilter, filterText, sortField, sortDir])

  // Attention counts for active tab
  const attentionCounts = useMemo(() => {
    const counts = { 1: 0, 2: 0, 3: 0, 4: 0 } as Record<1 | 2 | 3 | 4, number>
    for (const p of prs) {
      counts[p.attention_level]++
    }
    return counts
  }, [prs])

  // Sub-filter counts for recent tab
  const subFilterCounts = useMemo(() => ({
    all: prs.length,
    merged: prs.filter(p => p.merged_at != null).length,
    closed: prs.filter(p => p.state === 'closed' && !p.merged_at).length,
  }), [prs])

  const handleRowClick = (url: string) => {
    setExpandedUrl(prev => prev === url ? null : url)
  }

  const renderFilterBar = () => {
    if (tab === 'active') {
      return (
        <div className="prs-filter-bar">
          {ATTENTION_PILLS.map(p => (
            <button
              key={p.level}
              className={`prs-filter-pill${attentionFilter === p.level ? ' active' : ''}`}
              onClick={() => setAttentionFilter(prev => prev === p.level ? null : p.level)}
              type="button"
            >
              <span className={`prs-filter-dot ${p.dotClass}`} />
              <span className="prs-filter-pill-count">{attentionCounts[p.level]}</span>
              <span className="prs-filter-pill-label">{p.label}</span>
            </button>
          ))}
          {renderSearchInput()}
        </div>
      )
    }

    // Recent tab
    const pills: { key: SubFilter; label: string; count: number }[] = [
      { key: 'all', label: 'All', count: subFilterCounts.all },
      { key: 'merged', label: 'Merged', count: subFilterCounts.merged },
      { key: 'closed', label: 'Closed', count: subFilterCounts.closed },
    ]

    return (
      <div className="prs-filter-bar">
        {pills.map(p => (
          <button
            key={p.key}
            className={`prs-filter-pill${subFilter === p.key ? ' active' : ''}`}
            onClick={() => setSubFilter(p.key)}
            type="button"
          >
            <span className="prs-filter-dot" style={{ background: SUB_FILTER_COLORS[p.key] }} />
            <span className="prs-filter-pill-count">{p.count}</span>
            <span className="prs-filter-pill-label">{p.label}</span>
          </button>
        ))}
        {renderSearchInput()}
      </div>
    )
  }

  const renderSearchInput = () => (
    <div className="prs-search-inline">
      <span className="prs-search-inline-icon"><IconSearch size={12} /></span>
      <input
        className="prs-search-inline-input"
        placeholder="Filter..."
        value={filterText}
        onChange={e => setFilterText(e.target.value)}
      />
      {filterText && (
        <button className="prs-search-inline-clear" onClick={() => setFilterText('')}>
          <IconX size={10} />
        </button>
      )}
    </div>
  )

  const renderSkeletonRows = () => (
    <>
      {[...Array(5)].map((_, i) => (
        <tr key={i} className="prs-skel-row">
          <td><div className="prs-skel-bar prs-skel-bar-medium" /><div className="prs-skel-bar prs-skel-bar-short" style={{ marginTop: 4 }} /></td>
          <td><div className="prs-skel-bar prs-skel-bar-narrow" /></td>
          <td><div className="prs-skel-bar prs-skel-bar-narrow" /></td>
          <td><div className="prs-skel-bar prs-skel-bar-narrow" /></td>
        </tr>
      ))}
    </>
  )

  const renderEmpty = () => {
    if (error === 'auth') {
      return (
        <div className="prs-empty">
          <IconPullRequest size={32} className="prs-empty-icon" />
          <div className="prs-empty-text">GitHub CLI not authenticated</div>
          <div className="prs-empty-hint">
            Run <code>gh auth login</code> in a terminal to connect your GitHub account.
          </div>
        </div>
      )
    }
    if (error) {
      return (
        <div className="prs-empty">
          <IconPullRequest size={32} className="prs-empty-icon" />
          <div className="prs-empty-text">{error}</div>
        </div>
      )
    }
    return (
      <div className="prs-empty">
        <IconPullRequest size={32} className="prs-empty-icon" />
        <div className="prs-empty-text">
          {tab === 'active' ? 'No open PRs' : 'No recently closed PRs'}
        </div>
      </div>
    )
  }

  const repoShort = (repo: string) => {
    const parts = repo.split('/')
    return parts.length > 1 ? parts[1] : repo
  }

  const renderStatusCell = (pr: PrSearchItem) => {
    // Closed/merged PRs — show merged/closed state + "by @username"
    if (pr.state === 'closed') {
      if (pr.merged_at) {
        return (
          <div className="prs-status-cell">
            <span className="pr-status-chip pr-chip-purple">Merged</span>
            {pr.merged_by && <span className="prs-merged-by">by @{pr.merged_by}</span>}
          </div>
        )
      }
      return (
        <div className="prs-status-cell">
          <span className="pr-status-chip pr-chip-red">Closed</span>
        </div>
      )
    }

    // Open PRs — review chip + CI chip + optional auto-merge
    const review = renderReviewChip(pr)
    const ci = renderCIChip(pr)
    return (
      <div className="prs-status-cell">
        {review}
        {ci}
        {pr.auto_merge && <span className="pr-status-chip pr-chip-accent">Auto-merge</span>}
      </div>
    )
  }

  return (
    <div className="prs-page">
      <div className="page-header">
        <h1>PRs</h1>
        <SlidingTabs tabs={TABS} value={tab} onChange={handleTabChange} />
        {tab === 'recent' && (
          <div className="form-group" style={{ margin: 0 }}>
            <select
              value={days}
              onChange={e => setDays(Number(e.target.value))}
            >
              <option value={7}>7 days</option>
              <option value={14}>14 days</option>
              <option value={30}>30 days</option>
            </select>
          </div>
        )}
        <div className="page-header-actions">
          {fetchedAt && !loading && (
            <span className="prs-fetched-at" title={new Date(fetchedAt).toLocaleString()}>
              {timeAgo(new Date(fetchedAt).toISOString())}
            </span>
          )}
          <button
            className={`btn-icon ${loading ? 'spinning' : ''}`}
            onClick={() => fetchPrs(true)}
            title="Refresh"
          >
            <IconRefresh size={14} />
          </button>
        </div>
      </div>

      {!loading && !error && prs.length > 0 && renderFilterBar()}

      {loading ? (
        <table className="prs-table">
          <thead>
            <tr>
              <th>PR</th>
              <th>Status</th>
              <th>Task / Worker</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>{renderSkeletonRows()}</tbody>
        </table>
      ) : error || prs.length === 0 ? (
        renderEmpty()
      ) : (
        <table className="prs-table">
          <thead>
            <tr>
              <th onClick={() => handleSort(tab === 'active' ? 'attention' : 'pr')}>
                PR {sortIndicator(tab === 'active' ? 'attention' : 'pr')}
              </th>
              <th onClick={() => handleSort('status')}>Status {sortIndicator('status')}</th>
              <th onClick={() => handleSort('task')}>Task / Worker {sortIndicator('task')}</th>
              <th onClick={() => handleSort('updated')}>Updated {sortIndicator('updated')}</th>
            </tr>
          </thead>
          <tbody>
            {filteredPrs.map(pr => (
              <Fragment key={pr.url}>
                <tr
                  className={`attention-${pr.attention_level}${expandedUrl === pr.url ? ' expanded' : ''}`}
                  onClick={() => handleRowClick(pr.url)}
                >
                  <td>
                    <div className="prs-pr-cell">
                      <span className="prs-pr-repo">
                        <a
                          href="#"
                          onClick={e => { e.preventDefault(); e.stopPropagation(); openUrl(pr.url) }}
                        >
                          {repoShort(pr.repo)} #{pr.number}
                        </a>
                        <span className="prs-pr-size">
                          <span className="prs-additions">+{pr.additions}</span>
                          <span className="prs-deletions">-{pr.deletions}</span>
                        </span>
                      </span>
                      <span className="prs-pr-title" title={pr.title}>{pr.title}</span>
                    </div>
                  </td>
                  <td>{renderStatusCell(pr)}</td>
                  <td>
                    <div className="prs-taskworker-cell">
                      <div>
                        {pr.linked_task ? (
                          <Link
                            to={`/tasks/${pr.linked_task.id}`}
                            className="prs-task-pill"
                            onClick={e => e.stopPropagation()}
                            title={pr.linked_task.title}
                          >
                            {pr.linked_task.task_key || pr.linked_task.title}
                          </Link>
                        ) : (
                          <span className="prs-no-task">&mdash;</span>
                        )}
                      </div>
                      {pr.linked_worker && (
                        <Link
                          to={`/workers/${pr.linked_worker.id}`}
                          className="prs-worker-link"
                          onClick={e => e.stopPropagation()}
                        >
                          {pr.linked_worker.name}
                        </Link>
                      )}
                    </div>
                  </td>
                  <td>
                    <span className="prs-updated-cell" title={parseDate(pr.updated_at).toLocaleString()}>
                      {timeAgo(pr.updated_at)}
                    </span>
                  </td>
                </tr>
                {expandedUrl === pr.url && (
                  <tr className="prs-expanded-row">
                    <td colSpan={4}>
                      <div className="prs-expanded-content">
                        <PrPreviewCard url={pr.url} />
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
