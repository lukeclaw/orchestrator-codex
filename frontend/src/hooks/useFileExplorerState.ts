import { useState, useCallback } from 'react'
import { loadCachedExplorerOpen, saveCachedExplorerOpen } from './useEditorTabs'

const WIDTH_KEY = 'fe-width'
const VIEWER_RATIO_KEY = 'fe-viewer-ratio'
const VIEW_MODE_KEY = 'fe-view-mode'
const SHOW_IGNORED_KEY = 'fe-show-ignored'

const DEFAULT_WIDTH = 240
const MIN_WIDTH = 180
const MAX_WIDTH = 400
const DEFAULT_VIEWER_RATIO = 0.5

export type ViewMode = 'files' | 'changed'

export function useFileExplorerState(sessionId?: string) {
  const [open, setOpen] = useState(() => sessionId ? loadCachedExplorerOpen(sessionId) : false)

  const [panelWidth, setPanelWidth] = useState(() => {
    try {
      const stored = localStorage.getItem(WIDTH_KEY)
      return stored ? Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, parseInt(stored, 10))) : DEFAULT_WIDTH
    } catch { return DEFAULT_WIDTH }
  })

  const [viewerHeightRatio, setViewerHeightRatio] = useState(() => {
    try {
      const stored = localStorage.getItem(VIEWER_RATIO_KEY)
      return stored ? Math.max(0.2, Math.min(0.8, parseFloat(stored))) : DEFAULT_VIEWER_RATIO
    } catch { return DEFAULT_VIEWER_RATIO }
  })

  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try {
      const stored = localStorage.getItem(VIEW_MODE_KEY)
      return stored === 'changed' ? 'changed' : 'files'
    } catch { return 'files' }
  })

  const [showIgnored, setShowIgnored] = useState(() => {
    try { return localStorage.getItem(SHOW_IGNORED_KEY) === 'true' }
    catch { return false }
  })

  const toggleOpen = useCallback(() => {
    setOpen(prev => {
      const next = !prev
      if (sessionId) saveCachedExplorerOpen(sessionId, next)
      return next
    })
  }, [sessionId])

  const updateWidth = useCallback((w: number) => {
    const clamped = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, w))
    setPanelWidth(clamped)
    try { localStorage.setItem(WIDTH_KEY, String(clamped)) } catch {}
  }, [])

  const updateViewerHeightRatio = useCallback((r: number) => {
    const clamped = Math.max(0.2, Math.min(0.8, r))
    setViewerHeightRatio(clamped)
    try { localStorage.setItem(VIEWER_RATIO_KEY, String(clamped)) } catch {}
  }, [])

  const updateViewMode = useCallback((mode: ViewMode) => {
    setViewMode(mode)
    try { localStorage.setItem(VIEW_MODE_KEY, mode) } catch {}
  }, [])

  const toggleShowIgnored = useCallback(() => {
    setShowIgnored(prev => {
      const next = !prev
      try { localStorage.setItem(SHOW_IGNORED_KEY, String(next)) } catch {}
      return next
    })
  }, [])

  return {
    open,
    toggleOpen,
    panelWidth,
    updateWidth,
    viewerHeightRatio,
    updateViewerHeightRatio,
    viewMode,
    updateViewMode,
    showIgnored,
    toggleShowIgnored,
    MIN_WIDTH,
    MAX_WIDTH,
  }
}
