import { useEffect, useRef, useState } from 'react'
import { api, openUrl } from '../../api/client'
import type { PrPreviewData } from '../../api/types'
import ConfirmPopover from '../common/ConfirmPopover'
import { tokenize, renderTokens } from '../common/Markdown'
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

const FILE_STATUS_LABELS: Record<string, string> = {
  added: 'new',
  removed: 'deleted',
  renamed: 'renamed',
}

/** Render markdown text to HTML for comment previews.
 *  If originalLines is provided, injects them as deletion lines in suggestion blocks. */
function renderMd(text: string, originalLines?: string | null): string {
  // Strip HTML tags from GitHub API response before parsing as markdown
  let cleaned = text.replace(/<[^>]+>/g, '')
  // Inject original lines into suggestion blocks so the renderer can show the diff
  if (originalLines) {
    cleaned = cleaned.replace(
      /```suggestion\s*\n/,
      `\`\`\`suggestion\n${originalLines.split('\n').map(l => `-${l}`).join('\n')}\n`,
    )
  }
  return renderTokens(tokenize(cleaned))
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
  const [openReviewPopup, setOpenReviewPopup] = useState<string | null>(null)
  const reviewsRef = useRef<HTMLDivElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Close review popup on click outside
  useEffect(() => {
    if (!openReviewPopup) return
    const handler = (e: MouseEvent) => {
      if (reviewsRef.current && !reviewsRef.current.contains(e.target as Node)) {
        setOpenReviewPopup(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [openReviewPopup])

  const fetchData = async (refresh?: boolean) => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)
    try {
      const qs = `/api/pr-preview?url=${encodeURIComponent(url)}${refresh ? '&refresh=true' : ''}`
      const result = await api<PrPreviewData>(
        qs,
        { signal: controller.signal }
      )
      if (!controller.signal.aborted) {
        setData(result)
        setAutoMerge(result.auto_merge)
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
    const isAuthError = error.includes('401') && error.includes('gh auth login')
    return (
      <div className="pr-preview-card pr-preview-error">
        {isAuthError ? (
          <span>GitHub CLI not authenticated. Run <code>gh auth login</code> in a terminal to fix this.</span>
        ) : (
          <span>{error}</span>
        )}
        <button className="pr-preview-retry" onClick={() => fetchData(true)}>Retry</button>
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
              className={`pr-preview-refresh ${loading ? 'refreshing' : ''}`}
              onClick={() => fetchData(true)}
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
            <div className="pr-preview-section">
              <div className="pr-section-title">Reviews</div>
              {data.reviews.length > 0 ? (
                <div className="pr-reviews" ref={reviewsRef}>
                  {[...data.reviews].sort((a, b) => (a.submitted_at ?? '9999').localeCompare(b.submitted_at ?? '9999')).map(r => (
                    <div key={r.reviewer} className="pr-review-item">
                      <span
                        className={`pr-review-label ${r.html_url ? 'pr-review-clickable' : ''}`}
                        onClick={r.html_url ? () => openUrl(r.html_url!) : undefined}
                      >
                        <span className={`pr-review-icon review-${r.state}`}>
                          {r.state === 'approved' ? '✓' : r.state === 'changes_requested' ? '△' : '●'}
                        </span>
                        <span className="pr-review-name">{r.reviewer}</span>
                      </span>
                      {r.comment_threads.length > 0 && (
                        <>
                          <button
                            className={`pr-review-threads-btn ${openReviewPopup === r.reviewer ? 'active' : ''}`}
                            onClick={e => {
                              e.stopPropagation()
                              setOpenReviewPopup(openReviewPopup === r.reviewer ? null : r.reviewer)
                            }}
                            title="View comments"
                          >
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                              <circle cx="12" cy="12" r="3" />
                            </svg>
                            <span>{r.comment_threads.length}</span>
                          </button>
                          {openReviewPopup === r.reviewer && (
                            <div className="pr-review-popup" onClick={e => e.stopPropagation()}>
                              <div className="pr-review-popup-header">
                                <span className="pr-review-popup-title">
                                  {r.reviewer}
                                  {r.submitted_at && (
                                    <span className="pr-thread-time tooltip-below" data-full-date={formatFullDate(r.submitted_at)}>{timeAgo(r.submitted_at)}</span>
                                  )}
                                </span>
                                <div className="pr-review-popup-actions">
                                  {r.html_url && (
                                    <button className="pr-review-popup-link" onClick={() => openUrl(r.html_url!)} title="Open review in GitHub">
                                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                                        <polyline points="15 3 21 3 21 9" />
                                        <line x1="10" y1="14" x2="21" y2="3" />
                                      </svg>
                                    </button>
                                  )}
                                  <button className="pr-review-popup-close" onClick={() => setOpenReviewPopup(null)}>&times;</button>
                                </div>
                              </div>
                              <div className="pr-review-popup-content">
                                {r.comment_threads.map((t, i) => (
                                  <div
                                    key={i}
                                    className="pr-thread-card"
                                  >
                                    {t.file ? (
                                      <div className="pr-thread-file-label">
                                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                          <polyline points="14 2 14 8 20 8" />
                                        </svg>
                                        {t.file}
                                        {t.created_at && (
                                          <span className="pr-thread-time" data-full-date={formatFullDate(t.created_at)}>{timeAgo(t.created_at)}</span>
                                        )}
                                      </div>
                                    ) : null}
                                    <div className="pr-thread-comment pr-thread-root-comment">
                                      <div className="pr-thread-body markdown-content" dangerouslySetInnerHTML={{ __html: renderMd(t.body, t.original_lines) }} />
                                    </div>
                                    {t.replies.length > 0 && (
                                      <div className="pr-thread-replies">
                                        {t.replies.map((reply, j) => (
                                          <div key={j} className="pr-thread-comment pr-thread-reply">
                                            <div className="pr-thread-reply-header">
                                              <span className="pr-thread-author">{reply.author}</span>
                                              {reply.created_at && (
                                                <span className="pr-thread-time" data-full-date={formatFullDate(reply.created_at)}>{timeAgo(reply.created_at)}</span>
                                              )}
                                            </div>
                                            <div className="pr-thread-body markdown-content" dangerouslySetInnerHTML={{ __html: renderMd(reply.body) }} />
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <span className="pr-no-reviews">No reviews yet</span>
              )}
            </div>
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

    </div>
  )
}
