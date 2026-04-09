import type { ReactNode } from 'react'
import './NotificationToast.css'

// Re-use the context from NotificationContext
import type { NotificationType } from '../../context/NotificationContext'

interface Notification {
  id: number
  message: ReactNode
  type: NotificationType
  onClick?: () => void
}

interface Props {
  notifications: Notification[]
  onDismiss: (id: number) => void
}

export default function NotificationToast({ notifications, onDismiss }: Props) {
  if (notifications.length === 0) return null

  return (
    <div className="notification-container">
      {notifications.map(n => (
        <div
          key={n.id}
          className={`notification-toast ${n.type}${n.onClick ? ' clickable' : ''}`}
          onClick={n.onClick ? () => { onDismiss(n.id); n.onClick!() } : undefined}
        >
          <span className="nt-icon">
            {n.type === 'error' && '!'}
            {n.type === 'success' && '\u2713'}
            {n.type === 'warning' && '!'}
            {n.type === 'info' && 'i'}
          </span>
          <span className="nt-message">{n.message}</span>
        </div>
      ))}
    </div>
  )
}
