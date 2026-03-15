import { useState, useEffect } from 'react'
import { api, openUrl } from '../../api/client'
import { parseDate } from '../common/TimeAgo'
import type { Notification } from '../../api/types'
import {
  IconChat,
  IconInfo,
  IconAlertTriangle,
  IconChevronRight,
  IconCheck,
  IconExternalLink,
} from '../common/Icons'
import { useNotify } from '../../context/NotificationContext'

function formatNotificationTime(dateStr: string): string {
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

function getNotificationTypeConfig(type: string) {
  switch (type) {
    case 'pr_comment':
      return { icon: <IconChat size={18} />, label: 'PR Comment', color: 'purple' }
    case 'warning':
      return { icon: <IconAlertTriangle size={18} />, label: 'Warning', color: 'amber' }
    default:
      return { icon: <IconInfo size={18} />, label: 'Info', color: 'blue' }
  }
}

interface TaskNotificationsCardProps {
  taskId: string
}

export default function TaskNotificationsCard({ taskId }: TaskNotificationsCardProps) {
  const notify = useNotify()
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [notificationsExpanded, setNotificationsExpanded] = useState(true)
  const [expandedNotifications, setExpandedNotifications] = useState<Set<string>>(new Set())
  const [dismissingNotifications, setDismissingNotifications] = useState<Set<string>>(new Set())

  useEffect(() => {
    api<Notification[]>(`/api/notifications?task_id=${taskId}&dismissed=false`)
      .then(setNotifications)
      .catch(() => setNotifications([]))
  }, [taskId])

  const handleDismissNotification = async (notificationId: string) => {
    try {
      await api(`/api/notifications/${notificationId}/dismiss`, { method: 'POST' })
      setDismissingNotifications(prev => new Set(prev).add(notificationId))
      setTimeout(() => {
        setNotifications(prev => prev.filter(n => n.id !== notificationId))
        setDismissingNotifications(prev => {
          const next = new Set(prev)
          next.delete(notificationId)
          return next
        })
      }, 300)
    } catch (err) {
      console.error('Failed to dismiss notification:', err)
      notify('Failed to dismiss notification', 'error')
    }
  }

  if (notifications.length === 0) return null

  return (
    <div className="tdp-card tdp-notifications-card">
      <div className="tdp-card-header">
        <h3 className="clickable" onClick={() => setNotificationsExpanded(!notificationsExpanded)}>
          <span className={`expand-icon ${notificationsExpanded ? 'expanded' : ''}`}>&#9654;</span>
          Notifications
          <span className="count notification-count">({notifications.length})</span>
        </h3>
      </div>
      {notificationsExpanded && (
        <div className="tdp-notifications-list">
          {notifications.map(n => {
            const typeConfig = getNotificationTypeConfig(n.notification_type)
            const isPrComment = n.notification_type === 'pr_comment'
            const isExpanded = expandedNotifications.has(n.id)
            const isExpandable = isPrComment && !!n.metadata
            const toggleExpand = isExpandable ? () => setExpandedNotifications(prev => {
              const next = new Set(prev)
              if (next.has(n.id)) next.delete(n.id); else next.add(n.id)
              return next
            }) : undefined
            return (
              <article
                key={n.id}
                className={`np-card ${dismissingNotifications.has(n.id) ? 'dismissing' : ''} ${isExpanded ? 'expanded' : ''} ${typeConfig.color} ${!isExpandable ? 'non-expandable' : ''}`}
                onClick={toggleExpand}
                style={{ cursor: isExpandable ? 'pointer' : 'default' }}
              >
                <div className="np-card-indicator" />
                <div className="np-card-icon-col">
                  <div className={`np-card-icon-wrap ${typeConfig.color}`}>{typeConfig.icon}</div>
                  {isExpandable && (
                    <div className={`np-card-chevron ${isExpanded ? 'expanded' : ''}`}>
                      <IconChevronRight size={12} />
                    </div>
                  )}
                </div>
                <div className="np-card-body">
                  <div className="np-card-top">
                    <div className="np-card-header">
                      <span className={`np-badge ${typeConfig.color}`}>{typeConfig.label}</span>
                      <time className="np-time">{formatNotificationTime(n.created_at)}</time>
                      {n.metadata?.pr_title && (
                        <span className="np-pr-title">{n.metadata.pr_title}</span>
                      )}
                    </div>
                    <div className="np-card-actions" onClick={e => e.stopPropagation()}>
                      {n.link_url && (
                        <button className="np-link-btn" onClick={() => openUrl(n.link_url!)}>
                          <IconExternalLink size={12} />
                          Link
                        </button>
                      )}
                      <button
                        className="np-action-btn"
                        onClick={() => handleDismissNotification(n.id)}
                        title="Dismiss"
                      >
                        <IconCheck size={14} />
                      </button>
                    </div>
                  </div>
                  <div className="np-card-content">
                    {isExpanded && isPrComment && n.metadata ? (
                      <div className="np-pr-thread">
                        {n.metadata.pr_title && n.link_url && (
                          <a href={n.link_url} className="np-pr-thread-title" onClick={e => e.stopPropagation()}>
                            {n.metadata.pr_title}
                          </a>
                        )}
                        {n.metadata.reviewer_comment && (
                          <div className="np-comment-bubble reviewer">
                            <div className="np-comment-author reviewer">
                              <span>{n.metadata.reviewer_name || 'Reviewer'}</span>
                              {n.metadata.reviewer_commented_at && (
                                <time className="np-comment-time">{formatNotificationTime(n.metadata.reviewer_commented_at)}</time>
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
                                <time className="np-comment-time">{formatNotificationTime(n.metadata.reply_commented_at)}</time>
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
                      <p className="np-message" title={n.metadata?.reply || n.message}>
                        {n.metadata?.reply ? n.metadata.reply : n.message}
                      </p>
                    )}
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}
