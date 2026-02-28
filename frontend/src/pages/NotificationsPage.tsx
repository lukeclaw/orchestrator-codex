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
} from '../components/common/Icons'
import ConfirmPopover from '../components/common/ConfirmPopover'
import { parseDate } from '../components/common/TimeAgo'
import './NotificationsPage.css'

type DateGroup = { label: string; notifications: Notification[] }

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
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'active' | 'archived'>('active')
  const [dismissing, setDismissing] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [focusIndex, setFocusIndex] = useState(-1)
  const listRef = useRef<HTMLDivElement>(null)

  // 7 days ago for default time filter
  const sevenDaysAgo = new Date()
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7)

  useEffect(() => {
    fetchNotifications()
  }, [filter])

  async function fetchNotifications() {
    setLoading(true)
    try {
      // Active: non-dismissed from past 7 days
      // Archived: dismissed only
      const url = filter === 'archived'
        ? '/api/notifications?dismissed=true'
        : '/api/notifications?dismissed=false'
      const data = await api<Notification[]>(url)

      // For active tab, filter to past 7 days only
      const filtered = filter === 'active'
        ? data.filter(n => parseDate(n.created_at) >= sevenDaysAgo)
        : data

      setNotifications(filtered)
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

  async function handleDismissAll() {
    try {
      await api('/api/notifications/dismiss-all', {
        method: 'POST',
        body: JSON.stringify({})
      })
      refreshNotificationCount()
      setNotifications(prev => prev.map(n => ({ ...n, dismissed: true })))
    } catch (err) {
      console.error('Failed to dismiss all notifications:', err)
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

  const activeCount = notifications.filter(n => !n.dismissed).length

  const dateGroups = groupByDate(notifications)

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
          const id = flatNotifications[focusIndex].id
          setExpanded(prev => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id); else next.add(id)
            return next
          })
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
  }, [flatNotifications, focusIndex])

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
    return (
      <article
        key={n.id}
        className={`np-card ${n.dismissed ? 'dismissed' : ''} ${dismissing.has(n.id) ? 'dismissing' : ''} ${expanded.has(n.id) ? 'expanded' : ''} ${isFocused ? 'focused' : ''} ${typeConfig.color}`}
        onClick={() => setExpanded(prev => {
          const next = new Set(prev)
          if (next.has(n.id)) next.delete(n.id); else next.add(n.id)
          return next
        })}
        style={{ cursor: 'pointer' }}
      >
        <div className="np-card-indicator" />
        <div className={`np-card-icon-wrap ${typeConfig.color}`}>{typeConfig.icon}</div>
        <div className="np-card-body">
          <div className="np-card-main">
            <div className="np-card-header">
              <span className={`np-badge ${typeConfig.color}`}>
                {typeConfig.label}
              </span>
              <time className="np-time">{formatTime(n.created_at)}</time>
              {n.metadata?.pr_title && (
                <span className="np-pr-title">{n.metadata.pr_title}</span>
              )}
            </div>
            {expanded.has(n.id) && n.notification_type === 'pr_comment' && n.metadata ? (
              <div className="np-pr-thread">
                {n.metadata.pr_title && n.link_url && (
                  <a href={n.link_url} className="np-pr-thread-title" onClick={e => { e.preventDefault(); e.stopPropagation(); openUrl(n.link_url!) }}>
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
                  <p className="np-message" style={{ whiteSpace: 'pre-wrap' }}>{n.message}</p>
                )}
              </div>
            ) : (
              <p className="np-message" title={expanded.has(n.id) ? undefined : (n.metadata?.reply || n.message)}>
                {!expanded.has(n.id) && n.metadata?.reply ? n.metadata.reply : n.message}
              </p>
            )}
          </div>
          <div className="np-card-actions" onClick={e => e.stopPropagation()}>
            {n.task_id && (
              <button
                className="np-link-btn np-hoverable-action"
                onClick={() => navigate(`/tasks/${n.task_id}`)}
              >
                View Task
              </button>
            )}
            {n.link_url && (
              <button
                className="np-link-btn np-hoverable-action"
                onClick={() => openUrl(n.link_url!)}
              >
                <IconExternalLink size={12} />
                Link
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
              <button
                className="np-action-btn delete"
                onClick={() => handleDelete(n.id)}
                title="Delete"
              >
                <IconTrash size={14} />
              </button>
            )}
          </div>
          <div className={`np-card-chevron ${expanded.has(n.id) ? 'expanded' : ''}`}>
            <IconChevronRight size={14} />
          </div>
        </div>
      </article>
    )
  }

  // Build flat index map for keyboard navigation across date groups
  let flatIndex = 0

  return (
    <div className="notifications-page">
      {/* Header */}
      <div className="page-header">
        <h1>Notifications</h1>
        <div className="page-header-actions">
          <span className="ctx-count">
            {activeCount} notification{activeCount === 1 ? '' : 's'}
          </span>
          {filter === 'active' && activeCount > 0 && (
            <ConfirmPopover
              onConfirm={handleDismissAll}
              message="Dismiss all notifications?"
              confirmLabel="Dismiss All"
              variant="warning"
            >
              {({ onClick }) => (
                <button className="np-dismiss-all-btn" onClick={onClick}>
                  <IconCheck size={16} />
                  Dismiss All
                </button>
              )}
            </ConfirmPopover>
          )}
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="np-tabs">
        <button
          className={`np-tab ${filter === 'active' ? 'active' : ''}`}
          onClick={() => setFilter('active')}
        >
          Active
          {activeCount > 0 && <span className="np-tab-badge active">{activeCount}</span>}
        </button>
        <button
          className={`np-tab ${filter === 'archived' ? 'active' : ''}`}
          onClick={() => setFilter('archived')}
        >
          Archived
        </button>
      </div>

      {/* Content */}
      <div className="np-content">
        {loading ? (
          <div className="np-empty">
            <div className="np-empty-icon">
              <IconBell size={32} />
            </div>
            <p>Loading notifications...</p>
          </div>
        ) : notifications.length === 0 ? (
          <div className="np-empty">
            <div className="np-empty-icon">
              {filter === 'archived' ? <IconFolder size={32} /> : <IconBell size={32} />}
            </div>
            <h3>{filter === 'archived' ? 'No archived notifications' : 'All caught up!'}</h3>
            <p>
              {filter === 'archived'
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
                  <div className="np-date-group-header">{group.label}</div>
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
