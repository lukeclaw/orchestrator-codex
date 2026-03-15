import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, openUrl } from '../api/client'
import { useApp } from '../context/AppContext'
import type { Notification } from '../api/types'
import {
  IconCheck,
  IconExternalLink,
  IconTrash,
  IconChat,
  IconInfo,
  IconAlertTriangle,
  IconChevronRight,
  IconBell,
  IconFolder,
  IconUndo,
} from '../components/common/Icons'
import ConfirmPopover from '../components/common/ConfirmPopover'
import { parseDate } from '../components/common/TimeAgo'
import { linkifyText } from '../components/common/linkify'
import SlidingTabs from '../components/common/SlidingTabs'
import './NotificationsPage.css'

type DateGroup = { label: string; notifications: Notification[] }
type TypeFilter = 'all' | 'info' | 'pr_comment' | 'warning'

function groupByDate(notifications: Notification[]): DateGroup[] {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)
  const weekAgo = new Date(today)
  weekAgo.setDate(weekAgo.getDate() - 6)

  const groups: Record<string, Notification[]> = {
    Today: [],
    Yesterday: [],
    'This Week': [],
    Older: [],
  }

  for (const n of notifications) {
    const d = parseDate(n.created_at)
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate())
    if (day >= today) {
      groups['Today'].push(n)
    } else if (day >= yesterday) {
      groups['Yesterday'].push(n)
    } else if (day >= weekAgo) {
      groups['This Week'].push(n)
    } else {
      groups['Older'].push(n)
    }
  }

  return ['Today', 'Yesterday', 'This Week', 'Older']
    .filter(label => groups[label].length > 0)
    .map(label => ({ label, notifications: groups[label] }))
}

export default function NotificationsPage() {
  const navigate = useNavigate()
  const { refreshNotificationCount } = useApp()
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [activeCount, setActiveCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'active' | 'archived'>('active')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [dismissing, setDismissing] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [overflowIds, setOverflowIds] = useState<Set<string>>(new Set())
  const [focusIndex, setFocusIndex] = useState(-1)
  const listRef = useRef<HTMLDivElement>(null)
  const expandedRef = useRef<Set<string>>(new Set())

  // Keep expandedRef in sync
  useEffect(() => {
    expandedRef.current = expanded
  }, [expanded])

  // 7 days ago for default time filter
  const sevenDaysAgo = new Date()
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7)

  useEffect(() => {
    fetchNotifications()
    setTypeFilter('all')
  }, [filter])

  // Overflow detection: measure which messages are clamped
  const checkOverflows = useCallback(() => {
    if (!listRef.current) return
    const cards = listRef.current.querySelectorAll<HTMLElement>('[data-notification-id]')
    const newOverflowIds = new Set<string>()
    cards.forEach(card => {
      const id = card.getAttribute('data-notification-id')!
      // Skip cards that are currently expanded — unclamped text reads as no overflow
      if (expandedRef.current.has(id)) {
        // Preserve their overflow status if they had it before expanding
        if (overflowIds.has(id)) newOverflowIds.add(id)
        return
      }
      const msgEl = card.querySelector<HTMLElement>('.np-message')
      if (msgEl && msgEl.scrollHeight > msgEl.clientHeight) {
        newOverflowIds.add(id)
      }
    })
    setOverflowIds(newOverflowIds)
  }, [overflowIds])

  useEffect(() => {
    checkOverflows()
  }, [notifications])

  // ResizeObserver to re-check on container resize
  useEffect(() => {
    if (!listRef.current) return
    const observer = new ResizeObserver(() => checkOverflows())
    observer.observe(listRef.current)
    return () => observer.disconnect()
  }, [checkOverflows])

  async function fetchNotifications() {
    setLoading(true)
    try {
      // Active: non-dismissed from past 7 days
      // Archived: dismissed only
      const [data, countData] = await Promise.all([
        api<Notification[]>(
          filter === 'archived'
            ? '/api/notifications?dismissed=true'
            : '/api/notifications?dismissed=false'
        ),
        api<{ count: number }>('/api/notifications/count'),
      ])

      // For active tab, filter to past 7 days only
      const filtered = filter === 'active'
        ? data.filter(n => parseDate(n.created_at) >= sevenDaysAgo)
        : data

      setNotifications(filtered)
      setActiveCount(countData.count)
    } catch (err) {
      console.error('Failed to fetch notifications:', err)
    } finally {
      setLoading(false)
    }
  }

  async function handleDismiss(id: string) {
    try {
      await api(`/api/notifications/${id}/dismiss`, { method: 'POST' })
      refreshNotificationCount()
      // Start dismiss animation
      setDismissing(prev => new Set(prev).add(id))
      // Remove from list after animation completes
      setTimeout(() => {
        setNotifications(prev => prev.filter(n => n.id !== id))
        setDismissing(prev => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
      }, 300)
    } catch (err) {
      console.error('Failed to dismiss notification:', err)
    }
  }

  async function handleDelete(id: string) {
    try {
      await api(`/api/notifications/${id}`, { method: 'DELETE' })
      setNotifications(prev => prev.filter(n => n.id !== id))
    } catch (err) {
      console.error('Failed to delete notification:', err)
    }
  }

  async function handleDeleteGroup(groupNotifications: Notification[]) {
    const ids = groupNotifications.map(n => n.id)
    try {
      await api('/api/notifications/batch', {
        method: 'DELETE',
        body: JSON.stringify({ ids })
      })
      const idSet = new Set(ids)
      setNotifications(prev => prev.filter(n => !idSet.has(n.id)))
    } catch (err) {
      console.error('Failed to delete group:', err)
    }
  }

  async function handleRestore(id: string) {
    try {
      await api(`/api/notifications/${id}/undismiss`, { method: 'POST' })
      refreshNotificationCount()
      setNotifications(prev => prev.filter(n => n.id !== id))
    } catch (err) {
      console.error('Failed to restore notification:', err)
    }
  }

  const formatTime = (dateStr: string) => {
    const d = parseDate(dateStr)
    const diffMs = Date.now() - d.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    const diffDays = Math.floor(diffMs / 86400000)

    if (diffMins < 1) return 'Just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffHours < 24) return `${diffHours}h ago`
    if (diffDays < 7) return `${diffDays}d ago`
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  }

  const getTypeConfig = (type: string) => {
    switch (type) {
      case 'pr_comment':
        return { icon: <IconChat size={18} />, label: 'PR Comment', color: 'purple' }
      case 'warning':
        return { icon: <IconAlertTriangle size={18} />, label: 'Warning', color: 'amber' }
      default:
        return { icon: <IconInfo size={18} />, label: 'Info', color: 'blue' }
    }
  }

  // Apply type filter
  const filteredNotifications = typeFilter === 'all'
    ? notifications
    : typeFilter === 'info'
      ? notifications.filter(n => n.notification_type !== 'pr_comment' && n.notification_type !== 'warning')
      : notifications.filter(n => n.notification_type === typeFilter)

  const dateGroups = groupByDate(filteredNotifications)

  // Flat ordered list matching render order (date-grouped), not API order
  const flatNotifications = dateGroups.flatMap(g => g.notifications)

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (flatNotifications.length === 0) return

    switch (e.key) {
      case 'ArrowDown': {
        e.preventDefault()
        setFocusIndex(prev => {
          const next = Math.min(prev + 1, flatNotifications.length - 1)
          scrollCardIntoView(next)
          return next
        })
        break
      }
      case 'ArrowUp': {
        e.preventDefault()
        setFocusIndex(prev => {
          const next = Math.max(prev - 1, 0)
          scrollCardIntoView(next)
          return next
        })
        break
      }
      case 'Enter': {
        e.preventDefault()
        if (focusIndex >= 0 && focusIndex < flatNotifications.length) {
          const n = flatNotifications[focusIndex]
          const isPrComment = n.notification_type === 'pr_comment'
          const isExpandable = isPrComment || overflowIds.has(n.id)
          if (isExpandable) {
            setExpanded(prev => {
              const next = new Set(prev)
              if (next.has(n.id)) next.delete(n.id); else next.add(n.id)
              return next
            })
          }
        }
        break
      }
      case 'd': {
        if (focusIndex >= 0 && focusIndex < flatNotifications.length) {
          const n = flatNotifications[focusIndex]
          if (n && !n.dismissed) {
            handleDismiss(n.id)
          }
        }
        break
      }
      case 'Escape': {
        setExpanded(new Set())
        break
      }
    }
  }, [flatNotifications, focusIndex, overflowIds])

  function scrollCardIntoView(index: number) {
    if (!listRef.current) return
    const cards = listRef.current.querySelectorAll('.np-card')
    if (cards[index]) {
      cards[index].scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }

  function renderCard(n: Notification, flatIndex: number) {
    const typeConfig = getTypeConfig(n.notification_type)
    const isFocused = focusIndex === flatIndex
    const isPrComment = n.notification_type === 'pr_comment'
    const isExpandable = isPrComment || overflowIds.has(n.id)

    const toggleExpand = isExpandable ? () => setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(n.id)) next.delete(n.id); else next.add(n.id)
      return next
    }) : undefined

    return (
      <article
        key={n.id}
        data-notification-id={n.id}
        className={`np-card ${n.dismissed ? 'dismissed' : ''} ${dismissing.has(n.id) ? 'dismissing' : ''} ${expanded.has(n.id) ? 'expanded' : ''} ${isFocused ? 'focused' : ''} ${typeConfig.color} ${!isExpandable ? 'non-expandable' : ''}`}
        onClick={toggleExpand}
        style={{ cursor: isExpandable ? 'pointer' : 'default' }}
      >
        <div className="np-card-indicator" />
        <div className="np-card-icon-col">
          <div className={`np-card-icon-wrap ${typeConfig.color}`}>{typeConfig.icon}</div>
          {isExpandable && (
            <div className={`np-card-chevron ${expanded.has(n.id) ? 'expanded' : ''}`}>
              <IconChevronRight size={12} />
            </div>
          )}
        </div>
        <div className="np-card-body">
          <div className="np-card-top">
            <div className="np-card-header">
              <span className={`np-badge ${typeConfig.color}`}>
                {typeConfig.label}
              </span>
              <time className="np-time">{formatTime(n.created_at)}</time>
              {n.metadata?.pr_title && (
                <span className="np-pr-title">{n.metadata.pr_title}</span>
              )}
            </div>
            <div className="np-card-actions" onClick={e => e.stopPropagation()}>
              {n.link_url && (
                <button
                  className="np-link-btn"
                  onClick={() => openUrl(n.link_url!)}
                >
                  <IconExternalLink size={12} />
                  Link
                </button>
              )}
              {n.task_id && (
                <button
                  className="np-link-btn"
                  onClick={() => navigate(`/tasks/${n.task_id}`)}
                >
                  View Task
                </button>
              )}
              {!n.dismissed && (
                <button
                  className="np-action-btn"
                  onClick={() => handleDismiss(n.id)}
                  title="Dismiss"
                >
                  <IconCheck size={14} />
                </button>
              )}
              {n.dismissed && (
                <>
                  <button
                    className="np-action-btn restore"
                    onClick={() => handleRestore(n.id)}
                    title="Restore"
                  >
                    <IconUndo size={14} />
                  </button>
                  <button
                    className="np-action-btn delete"
                    onClick={() => handleDelete(n.id)}
                    title="Delete"
                  >
                    <IconTrash size={14} />
                  </button>
                </>
              )}
            </div>
          </div>
          <div className="np-card-content">
            {expanded.has(n.id) && n.notification_type === 'pr_comment' && n.metadata ? (
              <div className="np-pr-thread">
                {n.metadata.pr_title && n.link_url && (
                  <a href={n.link_url} className="np-pr-thread-title" onClick={e => { e.stopPropagation() }}>
                    {n.metadata.pr_title}
                  </a>
                )}
                {n.metadata.reviewer_comment && (
                  <div className="np-comment-bubble reviewer">
                    <div className="np-comment-author reviewer">
                      <span>{n.metadata.reviewer_name || 'Reviewer'}</span>
                      {n.metadata.reviewer_commented_at && (
                        <time className="np-comment-time">{formatTime(n.metadata.reviewer_commented_at)}</time>
                      )}
                    </div>
                    <div className="np-comment-body">{n.metadata.reviewer_comment}</div>
                  </div>
                )}
                {n.metadata.reply && (
                  <div className="np-comment-bubble reply">
                    <div className="np-comment-author reply">
                      <span>{n.metadata.reply_author || 'Your Reply'}</span>
                      {n.metadata.reply_commented_at && (
                        <time className="np-comment-time">{formatTime(n.metadata.reply_commented_at)}</time>
                      )}
                    </div>
                    <div className="np-comment-body">{n.metadata.reply}</div>
                  </div>
                )}
                {!n.metadata.reviewer_comment && !n.metadata.reply && (
                  <p className="np-message" style={{ whiteSpace: 'pre-wrap' }}>{linkifyText(n.message, openUrl)}</p>
                )}
              </div>
            ) : (
              <p className="np-message" title={expanded.has(n.id) ? undefined : (n.metadata?.reply || n.message)}>
                {linkifyText(!expanded.has(n.id) && n.metadata?.reply ? n.metadata.reply : n.message, openUrl)}
              </p>
            )}
          </div>
        </div>
      </article>
    )
  }

  // Build flat index map for keyboard navigation across date groups
  let flatIndex = 0

  const typeCounts = {
    pr_comment: notifications.filter(n => n.notification_type === 'pr_comment').length,
    warning: notifications.filter(n => n.notification_type === 'warning').length,
    info: notifications.filter(n => n.notification_type !== 'pr_comment' && n.notification_type !== 'warning').length,
  }

  const typeChips: { value: 'pr_comment' | 'warning' | 'info'; label: string; dotColor: string }[] = [
    { value: 'pr_comment', label: 'PR Comment', dotColor: 'purple' },
    { value: 'warning', label: 'Warning', dotColor: 'amber' },
    { value: 'info', label: 'Info', dotColor: 'blue' },
  ]

  return (
    <div className="notifications-page page-scroll-layout">
      {/* Header with tabs */}
      <div className="page-header">
        <h1>Notifications</h1>
        <SlidingTabs
          tabs={[
            { value: 'active', label: 'Active' },
            { value: 'archived', label: 'Archived' },
          ]}
          value={filter}
          onChange={setFilter}
        />
      </div>

      {/* Type filter chips */}
      <div className="np-type-filters">
        <button
          className={`np-type-chip ${typeFilter === 'all' ? 'active' : ''}`}
          onClick={() => setTypeFilter('all')}
        >
          <span className="np-type-chip-dot" style={{ background: 'var(--text-muted)' }} />
          <span className="np-type-chip-count">{notifications.length}</span>
          <span className="np-type-chip-label">All</span>
        </button>
        {typeChips.map(chip => (
          <button
            key={chip.value}
            className={`np-type-chip ${typeFilter === chip.value ? 'active' : ''}`}
            onClick={() => setTypeFilter(typeFilter === chip.value ? 'all' : chip.value)}
          >
            <span className={`np-type-chip-dot ${chip.dotColor}`} />
            <span className="np-type-chip-count">{typeCounts[chip.value]}</span>
            <span className="np-type-chip-label">{chip.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="np-content page-content">
        {loading ? (
          <div className="np-empty">
            <div className="np-empty-icon">
              <IconBell size={32} />
            </div>
            <p>Loading notifications...</p>
          </div>
        ) : filteredNotifications.length === 0 ? (
          <div className="np-empty">
            <div className="np-empty-icon">
              {filter === 'archived' ? <IconFolder size={32} /> : <IconBell size={32} />}
            </div>
            <h3>{filter === 'archived' ? 'No archived notifications' : 'All caught up!'}</h3>
            <p>
              {typeFilter !== 'all'
                ? 'No notifications match this filter'
                : filter === 'archived'
                  ? 'Dismissed notifications will appear here'
                  : 'No pending notifications from the past 7 days'}
            </p>
          </div>
        ) : (
          <div
            className="np-list"
            ref={listRef}
            tabIndex={0}
            onKeyDown={handleKeyDown}
          >
            {dateGroups.map(group => {
              const groupCards = group.notifications.map(n => {
                const card = renderCard(n, flatIndex)
                flatIndex++
                return card
              })
              return (
                <div key={group.label} className="np-date-group">
                  <div className="np-date-group-header">
                    <span>{group.label}</span>
                    <div className="np-group-actions">
                      {filter === 'active' && (
                        <ConfirmPopover
                          onConfirm={() => {
                            group.notifications.forEach(n => handleDismiss(n.id))
                          }}
                          message={`Dismiss ${group.notifications.length} notification${group.notifications.length === 1 ? '' : 's'} from "${group.label}"?`}
                          confirmLabel="Dismiss All"
                          variant="warning"
                        >
                          {({ onClick }) => (
                            <button className="np-group-clear-btn" onClick={onClick}>
                              Dismiss All
                            </button>
                          )}
                        </ConfirmPopover>
                      )}
                      {filter === 'archived' && (
                        <ConfirmPopover
                          onConfirm={() => handleDeleteGroup(group.notifications)}
                          message={`Delete ${group.notifications.length} notification${group.notifications.length === 1 ? '' : 's'} from "${group.label}"?`}
                          confirmLabel="Delete"
                          variant="danger"
                        >
                          {({ onClick }) => (
                            <button className="np-group-clear-btn" onClick={onClick}>
                              Clear
                            </button>
                          )}
                        </ConfirmPopover>
                      )}
                    </div>
                  </div>
                  {groupCards}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
