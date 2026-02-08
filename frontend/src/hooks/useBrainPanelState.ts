import { useState, useCallback } from 'react'

const COLLAPSED_KEY = 'brain-panel-collapsed'
const WIDTH_KEY = 'brain-panel-width'
const DEFAULT_WIDTH = 480
const MIN_WIDTH = 320
const MAX_WIDTH = 800

export function useBrainPanelState() {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === 'true' }
    catch { return false }
  })

  const [width, setWidth] = useState(() => {
    try {
      const stored = localStorage.getItem(WIDTH_KEY)
      return stored ? Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, parseInt(stored, 10))) : DEFAULT_WIDTH
    } catch { return DEFAULT_WIDTH }
  })

  const toggleCollapsed = useCallback(() => {
    setCollapsed(prev => {
      const next = !prev
      try { localStorage.setItem(COLLAPSED_KEY, String(next)) } catch {}
      return next
    })
  }, [])

  const updateWidth = useCallback((w: number) => {
    const clamped = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, w))
    setWidth(clamped)
    try { localStorage.setItem(WIDTH_KEY, String(clamped)) } catch {}
  }, [])

  return { collapsed, toggleCollapsed, width, updateWidth, MIN_WIDTH, MAX_WIDTH }
}
