import { useContext } from 'react'
import { createContext } from 'react'
import './NotificationToast.css'

// Re-use the context from NotificationContext
import type { NotificationType } from '../../context/NotificationContext'

interface Notification {
  id: number
  message: string
  type: NotificationType
}

export default function NotificationToast({ notifications }: { notifications: Notification[] }) {
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
        </div>
      ))}
    </div>
  )
}
