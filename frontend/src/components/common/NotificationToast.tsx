import './NotificationToast.css'

import type { NotificationType } from '../../context/NotificationContext'
import { useDismissNotification } from '../../context/NotificationContext'

interface Notification {
  id: number
  message: string
  type: NotificationType
}

export default function NotificationToast({ notifications }: { notifications: Notification[] }) {
  const dismiss = useDismissNotification()

  if (notifications.length === 0) return null

  return (
    <div className="notification-container">
      {notifications.map(n => (
        <div key={n.id} className={`notification-toast ${n.type}`}>
          <span className="nt-icon">
            {n.type === 'error' && '!'}
            {n.type === 'success' && '\u2713'}
            {n.type === 'warning' && '!'}
            {n.type === 'info' && 'i'}
          </span>
          <span className="nt-message">{n.message}</span>
          <button className="nt-dismiss" onClick={() => dismiss(n.id)} aria-label="Dismiss">
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
