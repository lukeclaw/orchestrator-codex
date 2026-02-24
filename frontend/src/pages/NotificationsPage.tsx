import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, openUrl } from '../api/client'
import { useApp } from '../context/AppContext'
import type { Notification } from '../api/types'
import { IconCheck, IconExternalLink, IconTrash } from '../components/common/Icons'
import { parseDate } from '../components/common/TimeAgo'
import './NotificationsPage.css'

export default function NotificationsPage() {
  const navigate = useNavigate()
  const { refreshNotificationCount } = useApp()
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'active' | 'archived'>('active')
  const [dismissing, setDismissing] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

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
        return { icon: '💬', label: 'PR Comment', color: 'purple' }
      case 'warning':
        return { icon: '⚠️', label: 'Warning', color: 'amber' }
      default:
        return { icon: 'ℹ️', label: 'Info', color: 'blue' }
    }
  }

  const activeCount = notifications.filter(n => !n.dismissed).length

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
            <button className="np-dismiss-all-btn" onClick={handleDismissAll}>
              <IconCheck size={16} />
              Dismiss All
            </button>
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
          {activeCount > 0 && <span className="np-tab-badge">{activeCount}</span>}
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
            <div className="np-empty-icon">⏳</div>
            <p>Loading notifications...</p>
          </div>
        ) : notifications.length === 0 ? (
          <div className="np-empty">
            <div className="np-empty-icon">{filter === 'archived' ? '📦' : '🎉'}</div>
            <h3>{filter === 'archived' ? 'No archived notifications' : 'All caught up!'}</h3>
            <p>
              {filter === 'archived' 
                ? 'Dismissed notifications will appear here'
                : 'No pending notifications from the past 7 days'}
            </p>
          </div>
        ) : (
          <div className="np-list">
            {notifications.map(n => {
              const typeConfig = getTypeConfig(n.notification_type)
              return (
                <article
                  key={n.id}
                  className={`np-card ${n.dismissed ? 'dismissed' : ''} ${dismissing.has(n.id) ? 'dismissing' : ''} ${expanded.has(n.id) ? 'expanded' : ''} ${typeConfig.color}`}
                  onClick={() => setExpanded(prev => {
                    const next = new Set(prev)
                    if (next.has(n.id)) next.delete(n.id); else next.add(n.id)
                    return next
                  })}
                  style={{ cursor: 'pointer' }}
                >
                  <div className="np-card-indicator" />
                  <div className="np-card-icon">{typeConfig.icon}</div>
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
                          className="np-link-btn"
                          onClick={() => navigate(`/tasks/${n.task_id}`)}
                        >
                          View Task
                        </button>
                      )}
                      {n.link_url && (
                        <button
                          className="np-link-btn"
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
                  </div>
                </article>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
