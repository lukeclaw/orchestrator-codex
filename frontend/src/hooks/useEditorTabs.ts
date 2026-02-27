import { useState, useCallback, useRef, useEffect } from 'react'
import { api } from '../api/client'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FileContentResponse {
  path: string
  content: string
  truncated: boolean
  total_lines: number | null
  size: number
  binary: boolean
  language: string | null
  modified: number | null
}

interface FileWriteResponse {
  path: string
  size: number
  modified: number
  conflict: boolean
}

export interface Tab {
  path: string                    // relative path (unique key)
  fileName: string                // extracted from path
  originalContent: string | null  // as fetched from server (null = not loaded)
  currentContent: string | null   // live editor state (null = not loaded)
  binary: boolean
  truncated: boolean
  totalLines: number | null
  size: number
  language: string | null         // for Monaco language mode
  modified: number | null         // mtime from server (for conflict detection)
  isPreview: boolean              // italic tab, replaced on next single-click
  isNew: boolean                  // true = new file, not yet on disk
  loading: boolean
  error: string | null
  saving: boolean
}

export interface EditorTabsAPI {
  tabs: Tab[]
  activeTabPath: string | null
  pendingClose: string | null
  openTab(path: string, preview?: boolean): void
  openNewFile(dirPath: string, fileName: string): Promise<boolean>
  closeTab(path: string): boolean
  confirmCloseTab(path: string): void
  cancelCloseTab(): void
  setActiveTab(path: string): void
  pinTab(path: string): void
  updateContent(path: string, content: string): void
  saveTab(path: string): Promise<boolean>
  isDirty(path: string): boolean
  hasAnyDirty: boolean
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_TABS = 20

function extractFileName(path: string): string {
  return path.split('/').pop() || path
}

// Detect language from file name/extension for new files
const EXT_LANGUAGE: Record<string, string> = {
  '.py': 'python', '.pyi': 'python',
  '.js': 'javascript', '.jsx': 'javascript',
  '.ts': 'typescript', '.tsx': 'typescript',
  '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml',
  '.toml': 'toml', '.md': 'markdown',
  '.html': 'html', '.htm': 'html',
  '.css': 'css', '.scss': 'scss', '.less': 'less',
  '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
  '.rs': 'rust', '.go': 'go', '.java': 'java',
  '.c': 'c', '.h': 'c', '.cpp': 'cpp', '.hpp': 'cpp',
  '.rb': 'ruby', '.php': 'php', '.sql': 'sql',
  '.xml': 'xml', '.svg': 'xml', '.r': 'r', '.lua': 'lua',
  '.swift': 'swift', '.kt': 'kotlin',
  '.dockerfile': 'dockerfile', '.tf': 'hcl',
  '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini', '.env': 'ini',
}

function detectLanguage(path: string): string | null {
  const ext = path.slice(path.lastIndexOf('.')).toLowerCase()
  const lang = EXT_LANGUAGE[ext]
  if (lang) return lang
  const basename = path.split('/').pop()?.toLowerCase() || ''
  if (basename === 'dockerfile') return 'dockerfile'
  if (basename === 'makefile') return 'makefile'
  return null
}

// ---------------------------------------------------------------------------
// Persistence helpers
// ---------------------------------------------------------------------------

function cacheKey(sessionId: string): string {
  return `editor-tabs:${sessionId}`
}

interface CachedTabState {
  paths: string[]
  active: string | null
  explorerOpen?: boolean
}

function loadCachedTabs(sessionId: string): CachedTabState | null {
  try {
    const raw = sessionStorage.getItem(cacheKey(sessionId))
    if (!raw) return null
    return JSON.parse(raw) as CachedTabState
  } catch {
    return null
  }
}

function saveCachedTabs(sessionId: string, tabs: Tab[], active: string | null, explorerOpen?: boolean) {
  try {
    const prev = loadCachedTabs(sessionId)
    const state: CachedTabState = {
      paths: tabs.filter(t => !t.isNew).map(t => t.path),
      active,
      explorerOpen: explorerOpen ?? prev?.explorerOpen ?? false,
    }
    sessionStorage.setItem(cacheKey(sessionId), JSON.stringify(state))
  } catch {
    // Ignore storage errors
  }
}

export function loadCachedExplorerOpen(sessionId: string): boolean {
  const cached = loadCachedTabs(sessionId)
  return cached?.explorerOpen ?? false
}

export function saveCachedExplorerOpen(sessionId: string, open: boolean) {
  try {
    const cached = loadCachedTabs(sessionId)
    const state: CachedTabState = {
      paths: cached?.paths ?? [],
      active: cached?.active ?? null,
      explorerOpen: open,
    }
    sessionStorage.setItem(cacheKey(sessionId), JSON.stringify(state))
  } catch {
    // Ignore storage errors
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useEditorTabs(sessionId: string): EditorTabsAPI {
  const [tabs, setTabs] = useState<Tab[]>([])
  const [activeTabPath, setActiveTabPath] = useState<string | null>(null)
  const abortControllers = useRef<Map<string, AbortController>>(new Map())
  const tabsRef = useRef<Tab[]>(tabs)
  tabsRef.current = tabs
  const restoredRef = useRef(false)

  // ------- fetch content helper -------
  const fetchTabContent = useCallback(async (path: string) => {
    // Abort previous fetch for this path
    const prev = abortControllers.current.get(path)
    if (prev) prev.abort()

    const controller = new AbortController()
    abortControllers.current.set(path, controller)

    setTabs(prev => prev.map(t =>
      t.path === path ? { ...t, loading: true, error: null } : t
    ))

    try {
      const params = new URLSearchParams({ path, max_lines: '10000' })
      const data = await api<FileContentResponse>(
        `/api/sessions/${sessionId}/files/content?${params}`,
        { signal: controller.signal },
      )
      if (!controller.signal.aborted) {
        setTabs(prev => prev.map(t =>
          t.path === path ? {
            ...t,
            originalContent: data.content,
            currentContent: data.content,
            binary: data.binary,
            truncated: data.truncated,
            totalLines: data.total_lines,
            size: data.size,
            language: data.language,
            modified: data.modified,
            loading: false,
            error: null,
          } : t
        ))
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      if (!controller.signal.aborted) {
        setTabs(prev => prev.map(t =>
          t.path === path ? {
            ...t,
            loading: false,
            error: e instanceof Error ? e.message : 'Failed to load file',
          } : t
        ))
      }
    } finally {
      abortControllers.current.delete(path)
    }
  }, [sessionId])

  // ------- restore cached tabs on mount -------
  useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    const cached = loadCachedTabs(sessionId)
    if (!cached || cached.paths.length === 0) return

    const newTabs: Tab[] = cached.paths.map(path => ({
      path,
      fileName: extractFileName(path),
      originalContent: null,
      currentContent: null,
      binary: false,
      truncated: false,
      totalLines: null,
      size: 0,
      language: detectLanguage(path),
      modified: null,
      isPreview: false,
      isNew: false,
      loading: true,
      error: null,
      saving: false,
    }))

    setTabs(newTabs)
    setActiveTabPath(cached.active && cached.paths.includes(cached.active) ? cached.active : cached.paths[0])
    for (const path of cached.paths) {
      fetchTabContent(path)
    }
  }, [sessionId, fetchTabContent])

  // ------- persist tab paths on change -------
  useEffect(() => {
    if (!restoredRef.current) return
    saveCachedTabs(sessionId, tabs, activeTabPath)
  }, [sessionId, tabs, activeTabPath])

  // ------- openTab -------
  const openTab = useCallback((path: string, preview = true) => {
    setTabs(prev => {
      const existing = prev.find(t => t.path === path)
      if (existing) {
        // Already open — activate and optionally pin
        setActiveTabPath(path)
        if (!preview && existing.isPreview) {
          return prev.map(t => t.path === path ? { ...t, isPreview: false } : t)
        }
        return prev
      }

      // Replace existing preview tab if opening in preview mode
      let next = prev
      if (preview) {
        const previewIdx = prev.findIndex(t => t.isPreview)
        if (previewIdx !== -1) {
          // Cancel any inflight fetch for the old preview tab
          const oldPath = prev[previewIdx].path
          const ctrl = abortControllers.current.get(oldPath)
          if (ctrl) ctrl.abort()
          next = [...prev.slice(0, previewIdx), ...prev.slice(previewIdx + 1)]
        }
      }

      // Enforce memory cap
      if (next.length >= MAX_TABS) {
        // Remove oldest non-dirty preview tab
        const removable = next.findIndex(t => t.isPreview && t.originalContent === t.currentContent)
        if (removable !== -1) {
          next = [...next.slice(0, removable), ...next.slice(removable + 1)]
        } else {
          // Remove oldest non-dirty tab
          const removable2 = next.findIndex(t => t.originalContent === t.currentContent)
          if (removable2 !== -1) {
            next = [...next.slice(0, removable2), ...next.slice(removable2 + 1)]
          }
        }
      }

      const newTab: Tab = {
        path,
        fileName: extractFileName(path),
        originalContent: null,
        currentContent: null,
        binary: false,
        truncated: false,
        totalLines: null,
        size: 0,
        language: detectLanguage(path),
        modified: null,
        isPreview: preview,
        isNew: false,
        loading: true,
        error: null,
        saving: false,
      }

      setActiveTabPath(path)
      // Schedule fetch (outside setState)
      setTimeout(() => fetchTabContent(path), 0)

      return [...next, newTab]
    })
  }, [fetchTabContent])

  // ------- openNewFile -------
  const openNewFile = useCallback(async (dirPath: string, fileName: string): Promise<boolean> => {
    const path = dirPath ? `${dirPath}/${fileName}` : fileName

    // Check if tab already exists
    const existing = tabsRef.current.find(t => t.path === path)
    if (existing) {
      setActiveTabPath(path)
      return true
    }

    // Create tab in saving state
    const newTab: Tab = {
      path,
      fileName,
      originalContent: '',
      currentContent: '',
      binary: false,
      truncated: false,
      totalLines: 0,
      size: 0,
      language: detectLanguage(path),
      modified: null,
      isPreview: false,
      isNew: true,
      loading: false,
      error: null,
      saving: true,
    }

    setTabs(prev => [...prev, newTab])
    setActiveTabPath(path)

    // Immediately create the empty file on disk
    try {
      const data = await api<FileWriteResponse>(
        `/api/sessions/${sessionId}/files/content`,
        {
          method: 'PUT',
          body: JSON.stringify({
            path,
            content: '',
            expected_mtime: null,
            create: true,
          }),
        },
      )
      setTabs(prev => prev.map(t =>
        t.path === path ? {
          ...t,
          originalContent: '',
          modified: data.modified,
          size: data.size,
          isNew: false,
          saving: false,
        } : t
      ))
      return true
    } catch (e) {
      setTabs(prev => prev.map(t =>
        t.path === path ? {
          ...t,
          saving: false,
          error: e instanceof Error ? e.message : 'Failed to create file',
        } : t
      ))
      return false
    }
  }, [sessionId])

  // ------- closeTab -------
  // Track which tab is pending close confirmation
  const [pendingClose, setPendingClose] = useState<string | null>(null)

  const closeTab = useCallback((path: string): boolean => {
    const tab = tabsRef.current.find(t => t.path === path)
    if (tab) {
      const dirty = tab.isNew
        ? (tab.currentContent ?? '') !== ''
        : tab.originalContent !== tab.currentContent
      if (dirty) {
        setPendingClose(path)
        return false
      }
    }

    doCloseTab(path)
    return true
  }, [])

  const confirmCloseTab = useCallback((path: string) => {
    setPendingClose(null)
    doCloseTab(path)
  }, [])

  const cancelCloseTab = useCallback(() => {
    setPendingClose(null)
  }, [])

  const doCloseTab = useCallback((path: string) => {
    // Cancel any inflight request
    const ctrl = abortControllers.current.get(path)
    if (ctrl) ctrl.abort()

    setTabs(prev => {
      const idx = prev.findIndex(t => t.path === path)
      if (idx === -1) return prev
      const next = [...prev.slice(0, idx), ...prev.slice(idx + 1)]

      // Activate neighbor if closing active tab
      setActiveTabPath(current => {
        if (current !== path) return current
        if (next.length === 0) return null
        // Prefer right neighbor, then left
        const newIdx = Math.min(idx, next.length - 1)
        return next[newIdx].path
      })

      return next
    })
  }, [])

  // ------- setActiveTab -------
  const setActive = useCallback((path: string) => {
    setActiveTabPath(path)
  }, [])

  // ------- pinTab -------
  const pinTab = useCallback((path: string) => {
    setTabs(prev => prev.map(t =>
      t.path === path ? { ...t, isPreview: false } : t
    ))
  }, [])

  // ------- updateContent -------
  const updateContent = useCallback((path: string, content: string) => {
    setTabs(prev => prev.map(t => {
      if (t.path !== path) return t
      // Auto-pin on edit
      return { ...t, currentContent: content, isPreview: false }
    }))
  }, [])

  // ------- saveTab -------
  const saveTab = useCallback(async (path: string): Promise<boolean> => {
    const tab = tabs.find(t => t.path === path)
    if (!tab || tab.saving) return false

    setTabs(prev => prev.map(t =>
      t.path === path ? { ...t, saving: true } : t
    ))

    try {
      const data = await api<FileWriteResponse>(
        `/api/sessions/${sessionId}/files/content`,
        {
          method: 'PUT',
          body: JSON.stringify({
            path,
            content: tab.currentContent ?? '',
            expected_mtime: tab.isNew ? null : tab.modified,
            create: tab.isNew,
          }),
        },
      )

      if (data.conflict) {
        const choice = window.confirm(
          'File was modified externally. Overwrite with your changes?'
        )
        if (choice) {
          // Retry without mtime check
          const retry = await api<FileWriteResponse>(
            `/api/sessions/${sessionId}/files/content`,
            {
              method: 'PUT',
              body: JSON.stringify({
                path,
                content: tab.currentContent ?? '',
                expected_mtime: null,
                create: tab.isNew,
              }),
            },
          )
          setTabs(prev => prev.map(t =>
            t.path === path ? {
              ...t,
              originalContent: t.currentContent,
              modified: retry.modified,
              size: retry.size,
              isNew: false,
              saving: false,
            } : t
          ))
          return true
        }
        // User cancelled overwrite
        setTabs(prev => prev.map(t =>
          t.path === path ? { ...t, saving: false } : t
        ))
        return false
      }

      setTabs(prev => prev.map(t =>
        t.path === path ? {
          ...t,
          originalContent: t.currentContent,
          modified: data.modified,
          size: data.size,
          isNew: false,
          saving: false,
        } : t
      ))
      return true
    } catch (e) {
      setTabs(prev => prev.map(t =>
        t.path === path ? {
          ...t,
          saving: false,
          error: e instanceof Error ? e.message : 'Save failed',
        } : t
      ))
      return false
    }
  }, [tabs, sessionId])

  // ------- isDirty -------
  const isDirty = useCallback((path: string): boolean => {
    const tab = tabs.find(t => t.path === path)
    if (!tab) return false
    if (tab.isNew) return (tab.currentContent ?? '') !== ''
    return tab.originalContent !== tab.currentContent
  }, [tabs])

  // ------- hasAnyDirty -------
  const hasAnyDirty = tabs.some(t => {
    if (t.isNew) return (t.currentContent ?? '') !== ''
    return t.originalContent !== t.currentContent
  })

  return {
    tabs,
    activeTabPath,
    pendingClose,
    openTab,
    openNewFile,
    closeTab,
    confirmCloseTab,
    cancelCloseTab,
    setActiveTab: setActive,
    pinTab,
    updateContent,
    saveTab,
    isDirty,
    hasAnyDirty,
  }
}
