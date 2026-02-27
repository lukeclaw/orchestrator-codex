import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export type NotificationType = 'info' | 'success' | 'error' | 'warning'

interface Notification {
  id: number
  message: string
  type: NotificationType
}

interface NotificationContextValue {
  notifications: Notification[]
  notify: (message: string, type?: NotificationType) => void
}

const NotificationContext = createContext<NotificationContextValue>({
  notifications: [],
  notify: () => {},
})

export function useNotify() {
  return useContext(NotificationContext).notify
}

export function useNotifications() {
  return useContext(NotificationContext).notifications
}

let nextId = 0

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [notifications, setNotifications] = useState<Notification[]>([])

  const notify = useCallback((message: string, type: NotificationType = 'info') => {
    const id = ++nextId
    setNotifications(prev => [...prev, { id, message, type }])
    const duration = type === 'error' ? 8000 : 4000
    setTimeout(() => {
      setNotifications(prev => prev.filter(n => n.id !== id))
    }, duration)
  }, [])

  return (
    <NotificationContext.Provider value={{ notifications, notify }}>
      {children}
    </NotificationContext.Provider>
  )
}
