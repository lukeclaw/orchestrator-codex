import { useEffect, useRef, useState } from 'react'
import { api, openUrl } from '../../api/client'
import type { PrPreviewData } from '../../api/types'
import { timeAgo, parseDate } from '../common/TimeAgo'
import './PrPreviewCard.css'

interface PrPreviewCardProps {
  url: string
  initialData?: PrPreviewData | null
  onDataFetched?: (data: PrPreviewData) => void
}

const STATE_LABELS: Record<string, string> = {
  open: 'Open',
  merged: 'Merged',
  closed: 'Closed',
}

const STATE_CLASSES: Record<string, string> = {
  open: 'state-open',
  merged: 'state-merged',
  closed: 'state-closed',
}

const REVIEW_LABELS: Record<string, string> = {
  approved: 'Approved',
  changes_requested: 'Changes requested',
  commented: 'Commented',
  dismissed: 'Dismissed',
}

const REVIEW_CLASSES: Record<string, string> = {
  approved: 'review-approved',
  changes_requested: 'review-changes',
  commented: 'review-commented',
  dismissed: 'review-dismissed',
}

const FILE_STATUS_LABELS: Record<string, string> = {
  added: 'new',
  removed: 'deleted',
  renamed: 'renamed',
}

function formatDate(dateStr: string): string {
  const d = parseDate(dateStr)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function PrPreviewCard({ url, initialData, onDataFetched }: PrPreviewCardProps) {
  const [data, setData] = useState<PrPreviewData | null>(initialData ?? null)
  const [loading, setLoading] = useState(!initialData)
  const [error, setError] = useState<string | null>(null)
  const [filesExpanded, setFilesExpanded] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const fetchData = async () => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)
    try {
      const result = await api<PrPreviewData>(
        `/api/pr-preview?url=${encodeURIComponent(url)}`,
        { signal: controller.signal }
      )
      if (!controller.signal.aborted) {
        setData(result)
        onDataFetched?.(result)
      }
    } catch (e: any) {
      if (e.name !== 'AbortError' && !controller.signal.aborted) {
        setError(e.message || 'Failed to fetch PR info')
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    if (!initialData) {
      fetchData()
    }
    return () => { abortRef.current?.abort() }
  }, [url])

  if (loading && !data) {
    return (
      <div className="pr-preview-card">
        <div className="pr-preview-skeleton">
          <div className="skel-line skel-w60" />
          <div className="skel-line skel-w40" />
          <div className="skel-line skel-w30" />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="pr-preview-card pr-preview-error">
        <span>{error}</span>
        <button className="pr-preview-retry" onClick={fetchData}>Retry</button>
      </div>
    )
  }

  if (!data) return null

  const stateClass = data.draft ? 'state-draft' : (STATE_CLASSES[data.state] || '')
  const stateLabel = data.draft ? 'Draft' : (STATE_LABELS[data.state] || data.state)

  // CI check categorization
  const passedChecks = data.checks.filter(c => c.conclusion === 'success').length
  const failedChecks = data.checks.filter(c =>
    c.conclusion === 'failure' || c.conclusion === 'timed_out' || c.conclusion === 'cancelled'
  )
  const pendingChecks = data.checks.filter(c => c.status !== 'completed')
  const actionableChecks = [...failedChecks, ...pendingChecks]
  const hasChecks = data.checks.length > 0

  // Subtitle: "opened by @author on Mar 5, 2026" or "merged by @merger on Mar 6, 2026"
  const subtitle = data.state === 'merged' && data.merged_by
    ? `merged by @${data.merged_by} on ${formatDate(data.merged_at!)}`
    : `opened by @${data.author} on ${formatDate(data.created_at)}`

  return (
    <div className="pr-preview-card">
      {/* Header: state + repo + number + refresh */}
      <div className="pr-preview-header">
        <span className={`pr-state-badge ${stateClass}`}>{stateLabel}</span>
        <span className="pr-repo-ref">{data.repo} #{data.number}</span>
        <div className="pr-preview-header-actions">
          {data.fetched_at && (
            <span className="pr-fetched-at">{timeAgo(data.fetched_at)}</span>
          )}
          <button
            className="pr-preview-refresh"
            onClick={fetchData}
            disabled={loading}
            data-tooltip="Refresh"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
          </button>
        </div>
      </div>

      {/* Title (clickable) + subtitle */}
      <div className="pr-preview-title-group">
        <a className="pr-preview-title" href={url} onClick={e => { e.preventDefault(); openUrl(url) }}>
          {data.title}
        </a>
        <span className="pr-preview-subtitle">{subtitle}</span>
      </div>

      {/* Changes section (expandable) */}
      <div className="pr-preview-section">
        <button
          className="pr-section-toggle"
          onClick={() => setFilesExpanded(!filesExpanded)}
        >
          <span className={`expand-chevron ${filesExpanded ? 'expanded' : ''}`}>▶</span>
          <span className="pr-section-title">Changes</span>
          <span className="pr-changes-summary">
            <span className="additions">+{data.additions}</span>
            {' '}
            <span className="deletions">&minus;{data.deletions}</span>
            <span className="pr-changes-files">{data.changed_files} {data.changed_files === 1 ? 'file' : 'files'}</span>
          </span>
        </button>
        {filesExpanded && data.files && data.files.length > 0 && (
          <div className="pr-files-list">
            {data.files.map(f => (
              <div key={f.filename} className="pr-file-item">
                <span className="pr-file-name" data-tooltip={f.filename}>
                  {f.filename.split('/').pop()}
                  {FILE_STATUS_LABELS[f.status] && (
                    <span className={`pr-file-status pr-file-${f.status}`}>
                      {FILE_STATUS_LABELS[f.status]}
                    </span>
                  )}
                </span>
                <span className="pr-file-stats">
                  {f.additions > 0 && <span className="additions">+{f.additions}</span>}
                  {f.deletions > 0 && <span className="deletions">&minus;{f.deletions}</span>}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Reviews with comment counts */}
      {data.reviews.length > 0 && (
        <div className="pr-preview-section">
          <div className="pr-section-title">Reviews</div>
          <div className="pr-reviews">
            {data.reviews.map(r => (
              <div
                key={r.reviewer}
                className={`pr-review-item ${r.html_url ? 'pr-review-clickable' : ''}`}
                onClick={r.html_url ? () => openUrl(r.html_url!) : undefined}
              >
                <span className={`pr-review-dot ${REVIEW_CLASSES[r.state] || ''}`} />
                <span className="pr-review-name">{r.reviewer}</span>
                <span className="pr-review-state">
                  {REVIEW_LABELS[r.state] || r.state}
                </span>
                {r.comments > 0 && (
                  <span className="pr-review-comments">
                    {r.comments} {r.comments === 1 ? 'comment' : 'comments'}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* CI Checks — simplified: count passed, only show failed/pending details */}
      {hasChecks && (
        <div className="pr-preview-section">
          <div className="pr-section-title">
            CI Checks
            <span className="pr-checks-summary">
              {passedChecks > 0 && <span className="checks-ok">{passedChecks} passed</span>}
              {failedChecks.length > 0 && <span className="checks-fail">{failedChecks.length} failed</span>}
              {pendingChecks.length > 0 && <span className="checks-pending">{pendingChecks.length} running</span>}
            </span>
          </div>
          {actionableChecks.length > 0 && (
            <div className="pr-checks">
              {actionableChecks.map(c => (
                <span
                  key={c.name}
                  className={`pr-check-pill ${c.status !== 'completed' ? 'check-running' : 'check-failed'}`}
                >
                  <span className={`pr-check-icon ${c.status !== 'completed' ? 'running' : 'failure'}`} />
                  {c.name}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
