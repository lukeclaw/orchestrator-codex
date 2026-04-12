import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export type NotificationType = 'info' | 'success' | 'error' | 'warning'

interface Notification {
  id: number
  message: ReactNode
  type: NotificationType
  onClick?: () => void
}

interface NotificationContextValue {
  notifications: Notification[]
  notify: (message: ReactNode, type?: NotificationType, onClick?: () => void) => void
  dismiss: (id: number) => void
}

const NotificationContext = createContext<NotificationContextValue>({
  notifications: [],
  notify: () => {},
  dismiss: () => {},
})

export function useNotify() {
  return useContext(NotificationContext).notify
}

export function useNotifications() {
  const ctx = useContext(NotificationContext)
  return { notifications: ctx.notifications, dismiss: ctx.dismiss }
}

let nextId = 0

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [notifications, setNotifications] = useState<Notification[]>([])

  const notify = useCallback((message: ReactNode, type: NotificationType = 'info', onClick?: () => void) => {
    const id = ++nextId
    setNotifications(prev => [...prev, { id, message, type, onClick }])
    const duration = type === 'error' ? 8000 : 4000
    setTimeout(() => {
      setNotifications(prev => prev.filter(n => n.id !== id))
    }, duration)
  }, [])

  const dismiss = useCallback((id: number) => {
    setNotifications(prev => prev.filter(n => n.id !== id))
  }, [])

  return (
    <NotificationContext.Provider value={{ notifications, notify, dismiss }}>
      {children}
    </NotificationContext.Provider>
  )
}
