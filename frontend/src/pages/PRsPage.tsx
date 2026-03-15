import { useState, useEffect, useMemo, useCallback, Fragment } from 'react'
import { Link } from 'react-router-dom'
import { openUrl } from '../api/client'
import type { PrSearchItem } from '../api/types'
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

const ATTENTION_PILLS: { level: 1 | 2 | 3 | 4; label: string; dotClass: string }[] = [
  { level: 2, label: 'Ready to ship', dotClass: 'prs-dot-green' },
  { level: 1, label: 'Needs action', dotClass: 'prs-dot-red' },
  { level: 3, label: 'In review', dotClass: 'prs-dot-accent' },
  { level: 4, label: 'Draft', dotClass: 'prs-dot-gray' },
]

// Sort priority: ready to ship first, then needs action, in review, draft
const ATTENTION_SORT_ORDER: Record<number, number> = { 2: 0, 1: 1, 3: 2, 4: 3 }

const SUB_FILTER_COLORS: Record<SubFilter, string> = {
  all: 'var(--text-muted)',
  merged: 'var(--purple)',
  closed: 'var(--red)',
}

function renderReviewChip(pr: PrSearchItem) {
  if (pr.draft) return <span className="pr-status-chip pr-chip-gray">Draft</span>
  if (pr.state !== 'open') return null
  switch (pr.review_decision) {
    case 'approved':
      if (pr.attention_level === 2) {
        return <span className="pr-status-chip pr-chip-green">Approved</span>
      }
      return <span className="pr-status-chip pr-chip-yellow">Pending ACL</span>
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

function renderCIIcon(pr: PrSearchItem) {
  if (pr.state !== 'open') return null
  switch (pr.ci_state) {
    case 'success':
      return (
        <span className="prs-icon-cell prs-icon-green" title="CI passing">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
        </span>
      )
    case 'failure':
      return (
        <span className="prs-icon-cell prs-icon-red" title="CI failing">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
        </span>
      )
    case 'pending':
      return (
        <span className="prs-icon-cell prs-icon-yellow" title="CI running">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><polyline points="12 6 12 12 16 14" /></svg>
        </span>
      )
    default:
      return null
  }
}

function renderAutoMergeIcon(pr: PrSearchItem) {
  if (!pr.auto_merge || pr.state !== 'open') return null
  return (
    <span className="prs-icon-cell prs-icon-accent" title="Auto-merge enabled">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="18" cy="18" r="3" /><circle cx="6" cy="6" r="3" /><path d="M6 21V9a9 9 0 0 0 9 9" /></svg>
    </span>
  )
}

export default function PRsPage() {
  const { prCache, prRefreshing, prErrors, fetchPrs } = useApp()
  const [tab, setTab] = useState<Tab>('active')
  const [days, setDays] = useState(7)
  const [expandedUrl, setExpandedUrl] = useState<string | null>(null)
  const [sortField, setSortField] = useState<SortField>('attention')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [filterText, setFilterText] = useState('')
  const [attentionFilter, setAttentionFilter] = useState<AttentionFilter>(null)
  const [subFilter, setSubFilter] = useState<SubFilter>('all')
  const [, setTick] = useState(0)

  const cacheKey = tab === 'active' ? 'active' : `recent:${days}`
  const cached = prCache[cacheKey]
  const prs = cached?.prs ?? []
  const fetchedAt = cached?.fetchedAt ?? null
  const prError = prErrors[cacheKey] ?? null
  const initialLoading = !cached && (prRefreshing || !prError)

  // Trigger fetch for current tab on mount or tab/days change
  useEffect(() => {
    fetchPrs(tab, days)
  }, [tab, days, fetchPrs])

  // Tick every 30s to keep "fetched X ago" fresh
  useEffect(() => {
    if (fetchedAt === null) return
    const id = setInterval(() => setTick(t => t + 1), 30_000)
    return () => clearInterval(id)
  }, [fetchedAt])

  const handleTabChange = useCallback((newTab: Tab) => {
    setTab(newTab)
    setAttentionFilter(null)
    setSubFilter('all')
    setFilterText('')
    setSortField(newTab === 'active' ? 'attention' : 'updated')
    setSortDir(newTab === 'active' ? 'asc' : 'desc')
  }, [])

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
          cmp = ATTENTION_SORT_ORDER[a.attention_level] - ATTENTION_SORT_ORDER[b.attention_level]
          if (cmp === 0) cmp = parseDate(b.updated_at).getTime() - parseDate(a.updated_at).getTime()
          break
        case 'pr':
          cmp = a.repo.localeCompare(b.repo) || a.number - b.number
          break
        case 'status':
          cmp = ATTENTION_SORT_ORDER[a.attention_level] - ATTENTION_SORT_ORDER[b.attention_level]
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
          <button
            className={`prs-filter-pill${attentionFilter === null ? ' active' : ''}`}
            onClick={() => setAttentionFilter(null)}
            type="button"
          >
            <span className="prs-filter-dot prs-dot-gray" />
            <span className="prs-filter-pill-count">{prs.length}</span>
            <span className="prs-filter-pill-label">All</span>
          </button>
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

  const isActive = tab === 'active'
  const colCount = isActive ? 6 : 4

  const renderSkeletonRows = () => (
    <>
      {[...Array(5)].map((_, i) => (
        <tr key={i} className="prs-skel-row">
          <td className="pt-td"><div className="prs-skel-bar prs-skel-bar-medium" /><div className="prs-skel-bar prs-skel-bar-short" style={{ marginTop: 4 }} /></td>
          <td className="pt-td"><div className="prs-skel-bar prs-skel-bar-narrow" /></td>
          {isActive && <td className="pt-td" />}
          {isActive && <td className="pt-td" />}
          <td className="pt-td"><div className="prs-skel-bar prs-skel-bar-narrow" /></td>
          <td className="pt-td"><div className="prs-skel-bar prs-skel-bar-narrow" /></td>
        </tr>
      ))}
    </>
  )

  const renderEmpty = () => {
    if (prError === 'auth') {
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
    if (prError) {
      return (
        <div className="prs-empty">
          <IconPullRequest size={32} className="prs-empty-icon" />
          <div className="prs-empty-text">{prError}</div>
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

    // Open PRs — conflict takes priority, otherwise show review chip
    if (pr.mergeable === 'conflicting') {
      return (
        <div className="prs-status-cell">
          <span className="pr-status-chip pr-chip-red">Merge conflicts</span>
        </div>
      )
    }
    return (
      <div className="prs-status-cell">
        {renderReviewChip(pr)}
      </div>
    )
  }

  // Show skeleton only on initial load (no cached data yet)
  const showSkeleton = initialLoading && prs.length === 0
  // Show empty/error only when not loading and no data
  const showEmpty = !initialLoading && (prError || prs.length === 0)

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
          {fetchedAt && !prRefreshing && (
            <span className="prs-fetched-at" title={new Date(fetchedAt).toLocaleString()}>
              {timeAgo(new Date(fetchedAt).toISOString())}
            </span>
          )}
          <button
            className={`btn-icon ${prRefreshing ? 'spinning' : ''}`}
            onClick={() => fetchPrs(tab, days, true)}
            title="Refresh"
          >
            <IconRefresh size={14} />
          </button>
        </div>
      </div>

      {!showSkeleton && !showEmpty && prs.length > 0 && renderFilterBar()}

      {showSkeleton ? (
        <div className="prs-table-wrapper">
        <table className="pt-table">
          <thead>
            <tr>
              <th className="pt-th">PR</th>
              <th className="pt-th">Status</th>
              {isActive && <th className="pt-th prs-col-icon">CI</th>}
              {isActive && <th className="pt-th prs-col-icon">AM</th>}
              <th className="pt-th">Task / Worker</th>
              <th className="pt-th">Updated</th>
            </tr>
          </thead>
          <tbody>{renderSkeletonRows()}</tbody>
        </table>
        </div>
      ) : showEmpty ? (
        renderEmpty()
      ) : (
        <div className="prs-table-wrapper">
        <table className="pt-table">
          <thead>
            <tr>
              <th className="pt-th sortable" onClick={() => handleSort(isActive ? 'attention' : 'pr')}>
                PR {sortIndicator(isActive ? 'attention' : 'pr')}
              </th>
              <th className="pt-th sortable" onClick={() => handleSort('status')}>Status {sortIndicator('status')}</th>
              {isActive && <th className="pt-th prs-col-icon">CI</th>}
              {isActive && <th className="pt-th prs-col-icon">AM</th>}
              <th className="pt-th sortable" onClick={() => handleSort('task')}>Task / Worker {sortIndicator('task')}</th>
              <th className="pt-th sortable" onClick={() => handleSort('updated')}>Updated {sortIndicator('updated')}</th>
            </tr>
          </thead>
          <tbody>
            {filteredPrs.map(pr => (
              <Fragment key={pr.url}>
                <tr
                  className={`pt-row attention-${pr.attention_level}${expandedUrl === pr.url ? ' expanded' : ''}`}
                  onClick={() => handleRowClick(pr.url)}
                >
                  <td className="pt-td">
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
                  <td className="pt-td">{renderStatusCell(pr)}</td>
                  {isActive && <td className="pt-td prs-col-icon">{renderCIIcon(pr)}</td>}
                  {isActive && <td className="pt-td prs-col-icon">{renderAutoMergeIcon(pr)}</td>}
                  <td className="pt-td">
                    <div className="prs-taskworker-cell">
                      <div>
                        {pr.linked_task ? (
                          <Link
                            to={`/tasks/${pr.linked_task.id}`}
                            className={`prs-task-pill status-${pr.linked_task.status}`}
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
                          className={`prs-worker-link ${pr.linked_worker.status}`}
                          onClick={e => e.stopPropagation()}
                        >
                          {pr.linked_worker.name}
                        </Link>
                      )}
                    </div>
                  </td>
                  <td className="pt-td date">
                    <span className="prs-updated-cell" title={parseDate(pr.updated_at).toLocaleString()}>
                      {timeAgo(pr.updated_at)}
                    </span>
                  </td>
                </tr>
                {expandedUrl === pr.url && (
                  <tr className="prs-expanded-row">
                    <td className="pt-td" colSpan={colCount}>
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
        </div>
      )}
    </div>
  )
}
