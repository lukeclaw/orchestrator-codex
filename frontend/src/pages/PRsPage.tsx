import { useState, useEffect, useRef, useMemo, useCallback, Fragment } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, openUrl } from '../api/client'
import type { PrSearchItem, PrSearchResponse, PrBatchResponse, PrPreviewData } from '../api/types'
import { useApp } from '../context/AppContext'
import SlidingTabs from '../components/common/SlidingTabs'
import { IconPullRequest, IconRefresh, IconSearch, IconX } from '../components/common/Icons'
import { timeAgo, parseDate } from '../components/common/TimeAgo'
import { getPrStatusChips } from '../components/tasks/prUtils'
import PrPreviewCard from '../components/tasks/PrPreviewCard'
import './PRsPage.css'

type Tab = 'active' | 'recent'
type SortField = 'pr' | 'status' | 'task' | 'updated'
type SortDir = 'asc' | 'desc'
type SubFilter = 'all' | 'open' | 'draft' | 'merged' | 'closed'

const TABS = [
  { value: 'active' as Tab, label: 'Active' },
  { value: 'recent' as Tab, label: 'Recent' },
]

export default function PRsPage() {
  const { setPrBadgeCount } = useApp()
  const [tab, setTab] = useState<Tab>('active')
  const [days, setDays] = useState(7)
  const [prs, setPrs] = useState<PrSearchItem[]>([])
  const [details, setDetails] = useState<Record<string, PrPreviewData | null>>({})
  const [loading, setLoading] = useState(true)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandedUrl, setExpandedUrl] = useState<string | null>(null)
  const [sortField, setSortField] = useState<SortField>('updated')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [filterText, setFilterText] = useState('')
  const [subFilter, setSubFilter] = useState<SubFilter>('all')

  const searchAbortRef = useRef<AbortController | null>(null)
  const batchAbortRef = useRef<AbortController | null>(null)

  const fetchPrs = useCallback(async (refresh = false) => {
    // Abort in-flight requests
    searchAbortRef.current?.abort()
    batchAbortRef.current?.abort()

    const searchCtrl = new AbortController()
    searchAbortRef.current = searchCtrl

    setLoading(true)
    setError(null)
    setPrBadgeCount(0)

    try {
      const params = new URLSearchParams({ tab })
      if (tab === 'recent') params.set('days', String(days))
      if (refresh) params.set('refresh', 'true')

      const data = await api<PrSearchResponse>(`/api/prs?${params}`, { signal: searchCtrl.signal })
      if (searchCtrl.signal.aborted) return

      setPrs(data.prs)
      setLoading(false)

      // Phase 2: batch fetch details
      if (data.prs.length > 0) {
        const batchCtrl = new AbortController()
        batchAbortRef.current = batchCtrl
        setDetailsLoading(true)

        try {
          const batchData = await api<PrBatchResponse>('/api/pr-preview/batch', {
            method: 'POST',
            body: JSON.stringify({ urls: data.prs.map(p => p.url) }),
            signal: batchCtrl.signal,
          })
          if (batchCtrl.signal.aborted) return

          setDetails(batchData.results)

          // Compute badge count
          let badgeCount = 0
          for (const pr of data.prs) {
            const detail = batchData.results[pr.url]
            if (!detail) continue
            const hasFailure = detail.checks?.some(c =>
              c.conclusion === 'failure' || c.conclusion === 'timed_out'
            )
            const hasChangesRequested = detail.reviews?.some(r =>
              r.state === 'changes_requested'
            )
            if (hasFailure || hasChangesRequested) badgeCount++
          }
          setPrBadgeCount(badgeCount)
        } catch (e) {
          if (e instanceof Error && e.name === 'AbortError') return
        } finally {
          setDetailsLoading(false)
        }
      } else {
        setDetails({})
        setDetailsLoading(false)
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
  }, [tab, days, setPrBadgeCount])

  useEffect(() => {
    setSubFilter('all')
    setFilterText('')
    fetchPrs()
    return () => {
      searchAbortRef.current?.abort()
      batchAbortRef.current?.abort()
    }
  }, [fetchPrs])

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

    // Sub-filter
    if (subFilter === 'open') result = result.filter(p => !p.draft && p.state === 'open')
    else if (subFilter === 'draft') result = result.filter(p => p.draft)
    else if (subFilter === 'merged') result = result.filter(p => p.merged_at != null)
    else if (subFilter === 'closed') result = result.filter(p => p.state === 'closed' && !p.merged_at)

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
        case 'pr':
          cmp = a.repo.localeCompare(b.repo) || a.number - b.number
          break
        case 'status':
          cmp = a.state.localeCompare(b.state)
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
  }, [prs, subFilter, filterText, sortField, sortDir])

  // Sub-filter counts
  const subFilterCounts = useMemo(() => {
    if (tab === 'active') {
      return {
        all: prs.length,
        open: prs.filter(p => !p.draft && p.state === 'open').length,
        draft: prs.filter(p => p.draft).length,
      }
    } else {
      return {
        all: prs.length,
        merged: prs.filter(p => p.merged_at != null).length,
        closed: prs.filter(p => p.state === 'closed' && !p.merged_at).length,
      }
    }
  }, [prs, tab])

  const handleRowClick = (url: string) => {
    setExpandedUrl(prev => prev === url ? null : url)
  }

  const handleDetailFetched = (url: string, data: PrPreviewData) => {
    setDetails(prev => ({ ...prev, [url]: data }))
  }

  const renderSubFilters = () => {
    const pills = tab === 'active'
      ? [
          { key: 'all' as SubFilter, label: 'All', count: subFilterCounts.all },
          { key: 'open' as SubFilter, label: 'Open', count: subFilterCounts.open },
          { key: 'draft' as SubFilter, label: 'Draft', count: subFilterCounts.draft },
        ]
      : [
          { key: 'all' as SubFilter, label: 'All', count: subFilterCounts.all },
          { key: 'merged' as SubFilter, label: 'Merged', count: (subFilterCounts as { merged: number }).merged },
          { key: 'closed' as SubFilter, label: 'Closed', count: (subFilterCounts as { closed: number }).closed },
        ]

    return (
      <div className="prs-filter-bar">
        {pills.map(p => (
          <button
            key={p.key}
            className={`prs-filter-pill ${subFilter === p.key ? 'active' : ''}`}
            onClick={() => setSubFilter(p.key)}
          >
            <span className="prs-filter-pill-count">{p.count}</span> {p.label}
          </button>
        ))}
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
      </div>
    )
  }

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

  return (
    <div className="prs-page">
      <div className="page-header">
        <h1>PRs</h1>
        <SlidingTabs tabs={TABS} value={tab} onChange={setTab} />
        {tab === 'recent' && (
          <select
            className="prs-days-select"
            value={days}
            onChange={e => setDays(Number(e.target.value))}
          >
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={30}>30 days</option>
          </select>
        )}
        <div className="page-header-actions">
          <button
            className={`btn-icon ${loading || detailsLoading ? 'spinning' : ''}`}
            onClick={() => fetchPrs(true)}
            title="Refresh"
          >
            <IconRefresh size={14} />
          </button>
        </div>
      </div>

      {!loading && !error && prs.length > 0 && renderSubFilters()}

      {loading ? (
        <table className="prs-table">
          <thead>
            <tr>
              <th>PR</th>
              <th>Status</th>
              <th>Task</th>
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
              <th onClick={() => handleSort('pr')}>PR {sortIndicator('pr')}</th>
              <th onClick={() => handleSort('status')}>Status {sortIndicator('status')}</th>
              <th onClick={() => handleSort('task')}>Task {sortIndicator('task')}</th>
              <th onClick={() => handleSort('updated')}>Updated {sortIndicator('updated')}</th>
            </tr>
          </thead>
          <tbody>
            {filteredPrs.map(pr => (
              <Fragment key={pr.url}>
                <tr
                  className={expandedUrl === pr.url ? 'expanded' : ''}
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
                      </span>
                      <span className="prs-pr-title" title={pr.title}>{pr.title}</span>
                    </div>
                  </td>
                  <td>
                    <div className="prs-status-cell">
                      {details[pr.url] ? (
                        getPrStatusChips(details[pr.url]!).map((chip, i) => (
                          <span key={i} className={`pr-status-chip pr-chip-${chip.color}`}>{chip.label}</span>
                        ))
                      ) : detailsLoading ? (
                        <>
                          <span className="prs-skel-chip" />
                          <span className="prs-skel-chip prs-skel-chip-short" />
                        </>
                      ) : (
                        <span className={`pr-status-chip pr-chip-${pr.draft ? 'gray' : pr.state === 'closed' ? (pr.merged_at ? 'purple' : 'red') : 'green'}`}>
                          {pr.draft ? 'Draft' : pr.merged_at ? 'Merged' : pr.state === 'closed' ? 'Closed' : 'Open'}
                        </span>
                      )}
                    </div>
                  </td>
                  <td>
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
                        <PrPreviewCard
                          url={pr.url}
                          initialData={details[pr.url] ?? undefined}
                          onDataFetched={(d) => handleDetailFetched(pr.url, d)}
                        />
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
