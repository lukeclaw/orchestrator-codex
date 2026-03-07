import { useEffect, useRef, useState } from 'react'
import { api, openUrl } from '../../api/client'
import type { PrPreviewData } from '../../api/types'
import ConfirmPopover from '../common/ConfirmPopover'
import { parseDate } from '../common/TimeAgo'
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

function formatFullDate(dateStr: string): string {
  const d = parseDate(dateStr)
  return d.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit',
  })
}

export default function PrPreviewCard({ url, initialData, onDataFetched }: PrPreviewCardProps) {
  const [data, setData] = useState<PrPreviewData | null>(initialData ?? null)
  const [loading, setLoading] = useState(!initialData)
  const [error, setError] = useState<string | null>(null)
  const [autoMerge, setAutoMerge] = useState<boolean | null>(initialData?.auto_merge ?? null)
  const [autoMergeLoading, setAutoMergeLoading] = useState(false)
  const [markingReady, setMarkingReady] = useState(false)
  const [copied, setCopied] = useState(false)
  const [expandedReviewer, setExpandedReviewer] = useState<string | null>(null)
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
        setAutoMerge(result.auto_merge)
        setExpandedReviewer(null)
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

  const isDraft = data.draft && data.state === 'open'
  const stateClass = isDraft ? 'state-draft' : (STATE_CLASSES[data.state] || '')
  const stateLabel = isDraft ? 'Draft' : (STATE_LABELS[data.state] || data.state)

  // Separate approval-gate checks (e.g. "Owner Approval") from real CI
  const APPROVAL_GATE_RE = /approval/i
  const ciChecks = data.checks.filter(c => !APPROVAL_GATE_RE.test(c.name))
  const gateChecks = data.checks.filter(c => APPROVAL_GATE_RE.test(c.name))

  const skippedConclusions = new Set(['cancelled', 'skipped', 'neutral'])
  const relevantChecks = ciChecks.filter(c => !skippedConclusions.has(c.conclusion ?? ''))
  const passedChecks = relevantChecks.filter(c => c.conclusion === 'success').length
  const failedChecks = relevantChecks.filter(c =>
    c.conclusion === 'failure' || c.conclusion === 'timed_out'
  )
  const pendingChecks = relevantChecks.filter(c => c.status === 'in_progress')
  const actionableChecks = [...failedChecks, ...pendingChecks]
  const hasChecks = relevantChecks.length > 0
  const pendingGates = gateChecks.filter(c => c.status !== 'completed')

  // Subtitle varies by state
  const dateStr = data.state === 'merged' && data.merged_at
    ? data.merged_at
    : data.state === 'closed' && data.closed_at
      ? data.closed_at
      : data.created_at
  const dateBadge = <span className="pr-date-hover" data-full-date={formatFullDate(dateStr)}>{formatDate(dateStr)}</span>
  const subtitle = data.state === 'merged' && data.merged_by
    ? <>merged by @{data.merged_by} on {dateBadge}</>
    : data.state === 'closed'
      ? <>closed {data.closed_by ? <>by @{data.closed_by}</> : ''} on {dateBadge}</>
      : <>opened by @{data.author} on {dateBadge}</>

  return (
    <div className="pr-preview-card">
      {/* Header block: title row + subtitle row */}
      <div className="pr-preview-title-group">
        <div className="pr-preview-header">
          <a className="pr-preview-title" href={url} onClick={e => { e.preventDefault(); openUrl(url) }}>
            {data.title}
          </a>
          <span className="pr-number">#{data.number}</span>
          <button
            className={`pr-copy-link ${copied ? 'copied' : ''}`}
            onClick={() => {
              navigator.clipboard.writeText(url)
              setCopied(true)
              setTimeout(() => setCopied(false), 2000)
            }}
            data-copy-tooltip={copied ? 'Copied!' : 'Copy link'}
          >
            {copied ? (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
            )}
          </button>
          <div className="pr-preview-header-actions">
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
        <div className="pr-preview-subtitle-row">
          <span className="pr-preview-subtitle">
            <span className={`pr-state-badge ${stateClass}`}>{stateLabel}</span>
            {subtitle}
          </span>
          <span className="pr-changes-label">
            <span className="pr-changes-summary">
              <span className="additions">+{data.additions}</span>
              {' '}
              <span className="deletions">&minus;{data.deletions}</span>
              <span className="pr-changes-files">{data.changed_files} {data.changed_files === 1 ? 'file' : 'files'}</span>
            </span>
            {data.files && data.files.length > 0 && (
              <div className="pr-files-popup">
                {data.files.map(f => (
                  <div key={f.filename} className="pr-file-item" title={f.filename}>
                    <span className="pr-file-name">
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
          </span>
        </div>
      </div>

      {/* Two-column layout: reviews (left) | CI + gates (right) */}
      {(data.reviews.length > 0 || (data.state !== 'closed' && (hasChecks || pendingGates.length > 0))) && (
        <div className="pr-preview-columns">
          {/* Left column — Reviews */}
          <div className="pr-preview-col">
            {data.reviews.length > 0 && (
              <div className="pr-preview-section">
                <div className="pr-section-title">Reviews</div>
                <div className="pr-reviews">
                  {data.reviews.map(r => (
                    <div
                      key={r.reviewer}
                      className={`pr-review-item ${r.comment_threads.length > 0 ? 'pr-review-expandable' : ''} ${expandedReviewer === r.reviewer ? 'pr-review-expanded' : ''} ${r.html_url && r.comment_threads.length === 0 ? 'pr-review-clickable' : ''}`}
                      onClick={() => {
                        if (r.comment_threads.length > 0) {
                          setExpandedReviewer(expandedReviewer === r.reviewer ? null : r.reviewer)
                        } else if (r.html_url) {
                          openUrl(r.html_url!)
                        }
                      }}
                    >
                      <span className={`pr-review-icon review-${r.state}`}>
                        {r.state === 'approved' ? '✓' : r.state === 'changes_requested' ? '△' : '●'}
                      </span>
                      <span className="pr-review-name">{r.reviewer}</span>
                      {r.comments > 0 && (
                        <span className="pr-review-comments">
                          {r.comments}
                        </span>
                      )}
                      {r.comment_threads.length > 0 && (
                        <span className={`pr-review-chevron ${expandedReviewer === r.reviewer ? 'expanded' : ''}`}>›</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Right column — CI Checks + Approval Gates (open PRs only) */}
          <div className="pr-preview-col">
            {data.state !== 'closed' && hasChecks && (
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

            {data.state === 'open' && pendingGates.length > 0 && (
              <div className="pr-preview-section">
                <div className="pr-section-title">
                  {pendingGates.map(c => c.name).join(', ')}
                  <span className="pr-checks-summary">
                    <span className="checks-pending">{pendingGates.length} awaiting</span>
                  </span>
                </div>
                {data.requested_reviewers.length > 0 && (
                  <div className="pr-reviews">
                    {data.requested_reviewers.map(name => (
                      <div key={name} className="pr-review-item">
                        <span className="pr-review-icon review-pending">●</span>
                        <span className="pr-review-name">{name}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {data.state === 'merged' && gateChecks.length > 0 && (() => {
              const approvers = data.reviews.filter(r => r.state === 'approved').map(r => r.reviewer)
              return (
                <div className="pr-section-title">
                  {gateChecks.map(c => c.name).join(', ')}
                  <span className="pr-checks-summary">
                    <span className="checks-ok">
                      {approvers.length > 0 ? `approved by ${approvers.join(', ')}` : 'approved'}
                    </span>
                  </span>
                </div>
              )
            })()}

            {isDraft && (
              <div className="pr-auto-merge-row">
                <ConfirmPopover
                  message="Mark this PR as ready for review?"
                  confirmLabel="Mark ready"
                  variant="default"
                  onConfirm={async () => {
                    setMarkingReady(true)
                    try {
                      await api(`/api/pr-ready?url=${encodeURIComponent(url)}`, { method: 'POST' })
                      fetchData()
                    } catch {
                      // stay as-is on failure
                    } finally {
                      setMarkingReady(false)
                    }
                  }}
                >
                  {({ onClick }) => (
                    <button className="pr-ready-btn" onClick={onClick} disabled={markingReady}>
                      {markingReady ? 'Marking…' : 'Ready for review'}
                    </button>
                  )}
                </ConfirmPopover>
              </div>
            )}

            {data.state === 'open' && !isDraft && autoMerge !== null && (
              <div className="pr-auto-merge-row">
                <span className="pr-section-title">
                  Auto-merge
                  {autoMergeLoading && <span className="pr-auto-merge-spinner" />}
                </span>
                <label className="pr-auto-merge-toggle">
                  <input
                    type="checkbox"
                    checked={autoMerge}
                    disabled={autoMergeLoading}
                    onChange={async () => {
                      const next = !autoMerge
                      setAutoMergeLoading(true)
                      try {
                        await api(`/api/pr-auto-merge?url=${encodeURIComponent(url)}&enable=${next}`, { method: 'POST' })
                        setAutoMerge(next)
                      } catch {
                        // revert on failure — state stays as-is
                      } finally {
                        setAutoMergeLoading(false)
                      }
                    }}
                  />
                  <span className="pr-toggle-switch">
                    <span className="pr-toggle-knob" />
                  </span>
                </label>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Expanded review threads panel */}
      {expandedReviewer && data.reviews.find(r => r.reviewer === expandedReviewer)?.comment_threads.length! > 0 && (() => {
        const reviewer = data.reviews.find(r => r.reviewer === expandedReviewer)!
        return (
          <div className="pr-thread-panel">
            <div className="pr-thread-panel-header">
              <span className={`pr-review-icon review-${reviewer.state}`}>
                {reviewer.state === 'approved' ? '✓' : reviewer.state === 'changes_requested' ? '△' : '●'}
              </span>
              <span className="pr-thread-panel-name">{reviewer.reviewer}</span>
              <span className={`pr-thread-panel-state ${REVIEW_CLASSES[reviewer.state] || ''}`}>
                {REVIEW_LABELS[reviewer.state] || reviewer.state}
              </span>
              <span className="pr-thread-panel-count">{reviewer.comment_threads.length} {reviewer.comment_threads.length === 1 ? 'thread' : 'threads'}</span>
              <button className="pr-thread-panel-close" onClick={() => setExpandedReviewer(null)}>×</button>
            </div>
            <div className="pr-thread-panel-body">
              {reviewer.comment_threads.map((t, i) => (
                <div
                  key={i}
                  className={`pr-thread-card ${t.html_url ? 'pr-thread-clickable' : ''}`}
                  onClick={t.html_url ? () => openUrl(t.html_url!) : undefined}
                >
                  {t.file && (
                    <div className="pr-thread-file-label">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <polyline points="14 2 14 8 20 8" />
                      </svg>
                      {t.file}
                    </div>
                  )}
                  <div className="pr-thread-comment pr-thread-root-comment">
                    <p className="pr-thread-body">{t.body}</p>
                  </div>
                  {t.replies.length > 0 && (
                    <div className="pr-thread-replies">
                      {t.replies.map((reply, j) => (
                        <div key={j} className="pr-thread-comment pr-thread-reply">
                          <span className="pr-thread-author">{reply.author}</span>
                          <p className="pr-thread-body">{reply.body}</p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )
      })()}
    </div>
  )
}
