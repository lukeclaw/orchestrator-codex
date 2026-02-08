import { useState, useCallback } from 'react'

const STORAGE_KEY = 'sidebar-collapsed'

export function useSidebarState() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })

  const toggle = useCallback(() => {
    setCollapsed(prev => {
      const next = !prev
      try { localStorage.setItem(STORAGE_KEY, String(next)) } catch {}
      return next
    })
  }, [])

  return { collapsed, toggle }
}
