import { useState, useEffect, useCallback, useRef } from 'react'
import { api, openUrl } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import { useSmartPaste } from '../../hooks/useSmartPaste'
import type { Task, TaskLink, PrPreviewData } from '../../api/types'
import {
  IconExternalLink,
  IconPlus,
  IconClipboard,
  IconTrash,
} from '../common/Icons'
import ConfirmPopover from '../common/ConfirmPopover'
import PrPreviewCard from './PrPreviewCard'

const GH_PR_RE = /github\.com\/[^/]+\/([^/]+)\/pull\/(\d+)/

function isPrUrl(url: string): boolean {
  return GH_PR_RE.test(url)
}

function prLinkLabel(url: string): string {
  const m = url.match(GH_PR_RE)
  return m ? `${m[1]} #${m[2]}` : url
}

const APPROVAL_GATE_RE = /approval/i

function getPrStatusChips(data: PrPreviewData): Array<{ label: string; color: string }> {
  const chips: Array<{ label: string; color: string }> = []

  if (data.state === 'merged') {
    chips.push({ label: 'Merged', color: 'purple' })
  } else if (data.state === 'closed') {
    chips.push({ label: 'Closed', color: 'red' })
  } else {
    if (data.draft) {
      chips.push({ label: 'Draft', color: 'gray' })
    } else {
      chips.push({ label: 'Open', color: 'green' })
    }

    if (data.reviews && data.reviews.length > 0) {
      const hasApproval = data.reviews.some(r => r.state === 'approved')
      const hasChangesRequested = data.reviews.some(r => r.state === 'changes_requested')
      if (hasChangesRequested) {
        chips.push({ label: 'Changes requested', color: 'red' })
      } else if (hasApproval) {
        chips.push({ label: 'Approved', color: 'green' })
      }
    }

    if (data.checks && data.checks.length > 0) {
      const approvalGate = data.checks.find(c => APPROVAL_GATE_RE.test(c.name))

      if (approvalGate) {
        if (approvalGate.conclusion === 'success') {
          chips.push({ label: 'Owner approved', color: 'green' })
        } else if (approvalGate.status === 'in_progress' || approvalGate.status === 'queued' || !approvalGate.conclusion) {
          chips.push({ label: 'Owner approval pending', color: 'yellow' })
        }
      }

      // Filter out approval gates and skipped/cancelled/neutral checks (same as PrPreviewCard)
      const skippedConclusions = new Set(['cancelled', 'skipped', 'neutral'])
      const ciChecks = data.checks.filter(c => !APPROVAL_GATE_RE.test(c.name))
      const relevantChecks = ciChecks.filter(c => !skippedConclusions.has(c.conclusion ?? ''))

      if (relevantChecks.length > 0) {
        const anyFailed = relevantChecks.some(c => c.conclusion === 'failure' || c.conclusion === 'timed_out')
        const anyRunning = relevantChecks.some(c => c.status === 'in_progress')
        const passedCount = relevantChecks.filter(c => c.conclusion === 'success').length

        if (anyFailed) {
          chips.push({ label: 'CI failing', color: 'red' })
        } else if (anyRunning) {
          chips.push({ label: 'CI running', color: 'yellow' })
        }
      }
    }
  }

  return chips
}

interface TaskLinksCardProps {
  task: Task
  isEditable: boolean
  onSaveField: (field: string, value: unknown) => Promise<void>
}

export default function TaskLinksCard({ task, isEditable, onSaveField }: TaskLinksCardProps) {
  const notify = useNotify()
  const { readClipboard } = useSmartPaste()

  const [links, setLinks] = useState<TaskLink[]>(task.links || [])
  const [showAddLink, setShowAddLink] = useState(false)
  const [newLinkUrl, setNewLinkUrl] = useState('')
  const [newLinkTag, setNewLinkTag] = useState('')
  const [editingLinkUrl, setEditingLinkUrl] = useState<string | null>(null)
  const [editLinkUrl, setEditLinkUrl] = useState('')
  const [editLinkTag, setEditLinkTag] = useState('')
  const [pasting, setPasting] = useState(false)

  // PR preview state
  const [prPreviewUrl, setPrPreviewUrl] = useState<string | null>(null)
  const [prPreviews, setPrPreviews] = useState<Record<string, PrPreviewData>>({})
  const [prFetchFailed, setPrFetchFailed] = useState<Set<string>>(new Set())
  const prAutoOpenedRef = useRef(false)

  // Sync links from task (server polling) when not editing
  useEffect(() => {
    const isEditingLinks = showAddLink || editingLinkUrl !== null
    if (!isEditingLinks) {
      const incoming = task.links || []
      setLinks(prev =>
        JSON.stringify(prev) === JSON.stringify(incoming) ? prev : incoming
      )
    }
  }, [task.links, showAddLink, editingLinkUrl])

  // Prefetch PR preview data for links tagged "PR"
  useEffect(() => {
    const prLinks = links.filter(l => isPrUrl(l.url))
    if (prLinks.length === 0) return

    // Only fetch PRs we don't already have data for
    const unfetched = prLinks.filter(l => !prPreviews[l.url] && !prFetchFailed.has(l.url))
    if (unfetched.length === 0) return

    const controller = new AbortController()

    unfetched.forEach(async (link) => {
      try {
        const result = await api<PrPreviewData>(
          `/api/pr-preview?url=${encodeURIComponent(link.url)}`,
          { signal: controller.signal }
        )
        if (!controller.signal.aborted) {
          setPrPreviews(prev => ({ ...prev, [link.url]: result }))
        }
      } catch {
        if (!controller.signal.aborted) {
          setPrFetchFailed(prev => new Set(prev).add(link.url))
        }
      }
    })

    return () => controller.abort()
  }, [links]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-open the first PR preview once its data is ready
  useEffect(() => {
    if (prAutoOpenedRef.current) return
    const firstPr = links.find(l => isPrUrl(l.url))
    if (firstPr && prPreviews[firstPr.url]) {
      prAutoOpenedRef.current = true
      setPrPreviewUrl(firstPr.url)
    }
  }, [links, prPreviews])

  const handleAddLink = async () => {
    if (!newLinkUrl.trim() || !task) return
    const trimmedUrl = newLinkUrl.trim()
    if (links.some(l => l.url === trimmedUrl)) {
      notify('This link has already been added', 'warning')
      return
    }
    const newLink: TaskLink = {
      url: trimmedUrl,
      tag: newLinkTag.trim() || undefined,
    }
    const updatedLinks = [...links, newLink]
    setLinks(updatedLinks)
    try {
      await onSaveField('links', updatedLinks)
      setNewLinkUrl('')
      setNewLinkTag('')
      setShowAddLink(false)
    } catch {
      setLinks(links)
      notify('Failed to add link', 'error')
    }
  }

  const handleRemoveLink = async (url: string) => {
    const updatedLinks = links.filter(l => l.url !== url)
    setLinks(updatedLinks)
    await onSaveField('links', updatedLinks)
  }

  const startEditLink = (link: TaskLink) => {
    setEditingLinkUrl(link.url)
    setEditLinkUrl(link.url)
    setEditLinkTag(link.tag || '')
  }

  const cancelEditLink = () => {
    setEditingLinkUrl(null)
    setEditLinkUrl('')
    setEditLinkTag('')
  }

  const handleSaveLink = async () => {
    if (!editLinkUrl.trim() || !editingLinkUrl) return
    const trimmedUrl = editLinkUrl.trim()
    if (trimmedUrl !== editingLinkUrl && links.some(l => l.url === trimmedUrl)) {
      notify('This link has already been added', 'warning')
      return
    }
    const updatedLinks = links.map(l =>
      l.url === editingLinkUrl
        ? { url: trimmedUrl, tag: editLinkTag.trim() || undefined }
        : l
    )
    setLinks(updatedLinks)
    try {
      await onSaveField('links', updatedLinks)
      cancelEditLink()
    } catch {
      setLinks(links)
      notify('Failed to update link', 'error')
    }
  }

  const isLinkChanged = () => {
    if (!editingLinkUrl) return false
    const original = links.find(l => l.url === editingLinkUrl)
    if (!original) return false
    return editLinkUrl !== original.url || editLinkTag !== (original.tag || '')
  }

  const handlePasteToLinks = useCallback(async () => {
    if (!task || pasting) return
    setPasting(true)
    try {
      const result = await readClipboard()
      let newLink: TaskLink

      if (result.type === 'image') {
        const res = await api<{ ok: boolean; url: string; filename: string }>(
          '/api/paste-image',
          { method: 'POST', body: JSON.stringify({ image_data: result.imageData }) },
        )
        if (!res.ok) return
        newLink = { url: `http://localhost:8093${res.url}`, tag: 'Image' }
      } else if (result.type === 'url') {
        newLink = { url: result.text! }
      } else {
        notify('Clipboard does not contain an image or URL', 'warning')
        return
      }

      if ((task.links || []).some(l => l.url === newLink.url)) {
        notify('This link has already been added', 'warning')
        return
      }
      const updatedLinks = [...(task.links || []), newLink]
      setLinks(updatedLinks)
      try {
        await onSaveField('links', updatedLinks)
        notify('Link added from clipboard', 'success')
      } catch {
        setLinks(task.links || [])
        notify('Failed to add link', 'error')
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'NotAllowedError') {
        notify('Clipboard access denied. Please allow clipboard permissions.', 'error')
      } else {
        notify(e instanceof Error ? e.message : 'Failed to paste', 'error')
      }
    } finally {
      setPasting(false)
    }
  }, [task, pasting, readClipboard, notify, onSaveField])

  return (
    <div className="tdp-card">
      <div className="tdp-card-header">
        <h3>Links {links.length > 0 && <span className="count">({links.length})</span>}</h3>
        {isEditable && !showAddLink && !editingLinkUrl && (
          <div className="tdp-header-btn-group">
            <button className="tdp-edit-btn" onClick={() => setShowAddLink(true)}><IconPlus size={12} /> Add</button>
            <button
              className="tdp-edit-btn"
              onClick={handlePasteToLinks}
              disabled={pasting}
              title="Paste image or URL from clipboard"
            >
              <IconClipboard size={12} /> {pasting ? 'Pasting...' : 'Paste'}
            </button>
          </div>
        )}
      </div>
      {showAddLink && (
        <div className="tdp-link-form-inline">
          <input type="text" placeholder="Tag (optional)" value={newLinkTag} onChange={e => setNewLinkTag(e.target.value)} className="tag-input" />
          <input type="url" placeholder="URL" value={newLinkUrl} onChange={e => setNewLinkUrl(e.target.value)} autoFocus />
          <div className="tdp-inline-actions">
            <button className="tdp-action-btn save" onClick={handleAddLink} disabled={!newLinkUrl.trim()} title="Add">✓</button>
            <button className="tdp-action-btn cancel" onClick={() => { setShowAddLink(false); setNewLinkUrl(''); setNewLinkTag('') }} title="Cancel">✕</button>
          </div>
        </div>
      )}
      {links.length === 0 && !showAddLink ? (
        <div className="tdp-links-empty">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
          </svg>
          <span>No links yet</span>
        </div>
      ) : (
        <div className="tdp-links">
          {links.map(link => (
            editingLinkUrl === link.url ? (
              <div key={link.url} className="tdp-link-form-inline">
                <input type="text" placeholder="Tag" value={editLinkTag} onChange={e => setEditLinkTag(e.target.value)} className="tag-input" />
                <input type="url" placeholder="URL" value={editLinkUrl} onChange={e => setEditLinkUrl(e.target.value)} autoFocus />
                <div className="tdp-inline-actions">
                  <button className="tdp-action-btn save" onClick={handleSaveLink} disabled={!editLinkUrl.trim() || !isLinkChanged()} title="Save">✓</button>
                  <button className="tdp-action-btn cancel" onClick={cancelEditLink} title="Cancel">✕</button>
                </div>
              </div>
            ) : (
              <div key={link.url} className="tdp-link-wrapper">
                <div
                  className={`tdp-link ${isPrUrl(link.url) ? 'tdp-link-pr' : ''} ${isPrUrl(link.url) && prPreviewUrl === link.url ? 'tdp-link-pr-active' : ''}`}
                  onClick={isPrUrl(link.url) ? () => setPrPreviewUrl(prPreviewUrl === link.url ? null : link.url) : undefined}
                >
                  <span className={`link-tag ${link.tag ? '' : 'empty'}`}>{link.tag || ''}</span>
                  {isPrUrl(link.url) ? (
                    <>
                      <span className={`pr-expand-indicator ${prPreviewUrl === link.url ? 'expanded' : ''}`}>&#9654;</span>
                      <span className="pr-link-content">
                        <span className="pr-link-label">{prLinkLabel(link.url)}</span>
                        {prPreviews[link.url] ? (
                          <span className="pr-inline-status">
                            {getPrStatusChips(prPreviews[link.url]).map((chip, i) => (
                              <span key={i} className={`pr-status-chip pr-chip-${chip.color}`}>{chip.label}</span>
                            ))}
                          </span>
                        ) : !prFetchFailed.has(link.url) ? (
                          <span className="pr-inline-skeleton">
                            <span className="skel-chip" />
                            <span className="skel-chip skel-chip-short" />
                          </span>
                        ) : null}
                      </span>
                    </>
                  ) : (
                    <a href={link.url}>{link.url}</a>
                  )}
                  {isEditable && (
                    <div className="tdp-link-actions" onClick={e => e.stopPropagation()}>
                      <button className="link-edit" onClick={() => startEditLink(link)} title="Edit">&#9998;</button>
                      <ConfirmPopover
                        message="Remove this link?"
                        confirmLabel="Remove"
                        onConfirm={() => handleRemoveLink(link.url)}
                        variant="danger"
                      >
                        {({ onClick }) => (
                          <button className="link-remove" onClick={onClick} title="Remove"><IconTrash size={12} /></button>
                        )}
                      </ConfirmPopover>
                    </div>
                  )}
                  {isPrUrl(link.url) && (
                    <button className="link-edit pr-open-link" onClick={e => { e.stopPropagation(); openUrl(link.url) }} title="Open in GitHub">
                      <IconExternalLink size={13} />
                    </button>
                  )}
                </div>
                {isPrUrl(link.url) && prPreviewUrl === link.url && (
                  <PrPreviewCard
                    url={link.url}
                    initialData={prPreviews[link.url]}
                    onDataFetched={(d) => setPrPreviews(prev => ({ ...prev, [link.url]: d }))}
                  />
                )}
              </div>
            )
          ))}
        </div>
      )}
    </div>
  )
}
