import React, { useState, useEffect, useCallback, useRef, useMemo, type KeyboardEvent } from 'react'
import { api } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import {
  IconChevronRight,
  IconFolder,
  IconFolderOpen,
  IconFile,
  IconFilter,
  IconRefresh,
  IconX,
} from '../common/Icons'
import type { ViewMode } from '../../hooks/useFileExplorerState'
import './FileExplorerPanel.css'

// --- Types ---

interface FileEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  modified: number | null
  children_count: number | null
  git_status: string | null
  human_size: string | null
  children: FileEntry[] | null
}

interface DirectoryResponse {
  work_dir: string
  path: string
  entries: FileEntry[]
  git_available: boolean
}

interface TreeNode extends Omit<FileEntry, 'children'> {
  children?: TreeNode[]
  loading?: boolean
  expanded?: boolean
}

// --- Props ---

interface FileExplorerPanelProps {
  sessionId: string
  workDir: string | null
  isOpen: boolean
  width: number
  onWidthChange: (w: number) => void
  onFileSelect: (path: string) => void
  onFileDoubleClick?: (path: string) => void
  onNewFile?: (dirPath: string, fileName: string) => Promise<boolean>
  selectedFile: string | null
  viewMode: ViewMode
  onViewModeChange: (mode: ViewMode) => void
  showIgnored: boolean
  onToggleIgnored: () => void
}

// --- Helpers ---

const GIT_BADGE: Record<string, string> = {
  modified: 'M',
  added: 'A',
  untracked: 'U',
  deleted: 'D',
  renamed: 'R',
  conflicting: '!',
  ignored: 'I',
}

/** Convert API entries into TreeNodes, preserving pre-fetched children. */
function entriesToNodes(entries: FileEntry[]): TreeNode[] {
  return entries.map(e => ({
    ...e,
    expanded: false,
    children: e.children ? entriesToNodes(e.children) : undefined,
  }))
}

// --- Component ---

export default function FileExplorerPanel({
  sessionId,
  workDir,
  isOpen,
  width,
  onWidthChange,
  onFileSelect,
  onFileDoubleClick,
  onNewFile,
  selectedFile,
  viewMode,
  onViewModeChange,
  showIgnored,
  onToggleIgnored,
}: FileExplorerPanelProps) {
  const notify = useNotify()
  const [tree, setTreeRaw] = useState<TreeNode[]>([])
  const treeSnapshotRef = useRef<TreeNode[]>([])
  // Wrap setTree to keep the ref in sync
  const setTree = useCallback((updater: TreeNode[] | ((prev: TreeNode[]) => TreeNode[])) => {
    setTreeRaw(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater
      treeSnapshotRef.current = next
      return next
    })
  }, [])
  const [changedFiles, setChangedFiles] = useState<FileEntry[]>([])
  const [gitAvailable, setGitAvailable] = useState(false)
  const [rootLoading, setRootLoading] = useState(false)
  const [filterText, setFilterText] = useState('')
  const [showFilter, setShowFilter] = useState(false)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; path: string } | null>(null)
  const [focusIndex, setFocusIndex] = useState(-1)
  // Inline new-file input: { dirPath, depth } when active
  const [newFileInput, setNewFileInput] = useState<{ dirPath: string; depth: number } | null>(null)
  const newFileInputRef = useRef<HTMLInputElement>(null)
  const newFileSubmittedRef = useRef(false)
  const filterInputRef = useRef<HTMLInputElement>(null)
  const treeRef = useRef<HTMLDivElement>(null)

  // Fetch root directory
  const fetchDir = useCallback(async (path: string = '.', depth = 1): Promise<{ entries: FileEntry[]; gitAvailable: boolean }> => {
    const params = new URLSearchParams({ path, depth: String(depth), show_ignored: String(showIgnored) })
    const data = await api<DirectoryResponse>(
      `/api/sessions/${sessionId}/files?${params}`
    )
    return { entries: data.entries, gitAvailable: data.git_available }
  }, [sessionId, showIgnored])

  // Load root on mount / refresh — depth=5 prefetches multiple levels
  const loadRoot = useCallback(async () => {
    setRootLoading(true)
    try {
      const { entries, gitAvailable: ga } = await fetchDir('.', 5)
      setTree(entriesToNodes(entries))
      setGitAvailable(ga)
      // For "changed" view, collect all files with git status
      if (ga) {
        setChangedFiles(entries.filter(e => e.git_status && e.git_status !== 'ignored'))
      }
    } catch {
      // Silently fail - work_dir may not exist yet
    } finally {
      setRootLoading(false)
    }
  }, [fetchDir])

  useEffect(() => {
    if (isOpen) {
      loadRoot()
    }
  }, [isOpen, loadRoot])

  // Find a node by path in a tree
  const findNode = useCallback((nodes: TreeNode[], path: string): TreeNode | null => {
    for (const n of nodes) {
      if (n.path === path) return n
      if (n.children) {
        const found = findNode(n.children, path)
        if (found) return found
      }
    }
    return null
  }, [])

  // Helper to update a node by path in the tree
  const updateNode = useCallback((nodes: TreeNode[], path: string, updater: (n: TreeNode) => TreeNode): TreeNode[] =>
    nodes.map(n => {
      if (n.path === path) return updater(n)
      if (n.children) return { ...n, children: updateNode(n.children, path, updater) }
      return n
    }), [])

  // Expand a directory node
  const toggleExpand = useCallback(async (nodePath: string) => {
    // Read current state synchronously from the ref
    const node = findNode(treeSnapshotRef.current, nodePath)
    if (!node || !node.is_dir) return

    if (node.expanded) {
      // Collapsing — just toggle off, no fetch needed
      setTree(prev => updateNode(prev, nodePath, n => ({ ...n, expanded: false })))
      return
    }

    if (node.children) {
      // Children already cached — just expand, no fetch needed
      setTree(prev => updateNode(prev, nodePath, n => ({ ...n, expanded: true })))
      return
    }

    // Expanding without cached children — mark loading and fetch (depth=2 prefetches)
    setTree(prev => updateNode(prev, nodePath, n => ({ ...n, loading: true, expanded: true })))

    try {
      const { entries } = await fetchDir(nodePath, 5)
      setTree(prev => updateNode(prev, nodePath, n => ({
        ...n,
        loading: false,
        expanded: true,
        children: entriesToNodes(entries),
      })))
    } catch {
      setTree(prev => updateNode(prev, nodePath, n => ({
        ...n,
        loading: false,
        expanded: false,
      })))
    }
  }, [fetchDir, findNode, updateNode, setTree])

  // Flatten tree for keyboard nav and rendering
  const flatNodes = useMemo(() => {
    const result: { node: TreeNode; depth: number }[] = []
    const walk = (nodes: TreeNode[], depth: number) => {
      for (const n of nodes) {
        const matchesFilter = !filterText || n.name.toLowerCase().includes(filterText.toLowerCase())
        if (matchesFilter || n.is_dir) {
          result.push({ node: n, depth })
        }
        if (n.expanded && n.children) {
          walk(n.children, depth + 1)
        }
      }
    }
    walk(tree, 0)
    return result
  }, [tree, filterText])

  // Context menu actions
  const handleCopyPath = useCallback(async (relPath: string) => {
    try {
      const fullPath = workDir ? `${workDir}/${relPath}` : relPath
      await navigator.clipboard.writeText(fullPath)
      notify('Copied!', 'success')
    } catch {
      notify('Failed to copy path', 'error')
    }
    setContextMenu(null)
  }, [workDir, notify])

  const handleCopyRelativePath = useCallback(async (relPath: string) => {
    try {
      await navigator.clipboard.writeText(relPath)
      notify('Copied!', 'success')
    } catch {
      notify('Failed to copy path', 'error')
    }
    setContextMenu(null)
  }, [notify])

  const handleCollapseAll = useCallback(() => {
    setTree(prev => {
      const collapse = (nodes: TreeNode[]): TreeNode[] =>
        nodes.map(n => ({ ...n, expanded: false, children: n.children ? collapse(n.children) : undefined }))
      return collapse(prev)
    })
    setContextMenu(null)
  }, [])

  const handleNewFile = useCallback((contextPath: string) => {
    setContextMenu(null)
    if (!onNewFile) return
    // Determine directory: if the context target is a dir, use it; otherwise use parent
    const node = findNode(treeSnapshotRef.current, contextPath)
    const isDir = node?.is_dir ?? contextPath === '.'
    const dirPath = isDir ? contextPath : contextPath.split('/').slice(0, -1).join('/')
    // Find depth for the inline input
    let depth = 0
    if (dirPath && dirPath !== '.') {
      depth = dirPath.split('/').length
    }
    // Expand the target directory if collapsed
    if (isDir && node && !node.expanded) {
      toggleExpand(contextPath)
    }
    newFileSubmittedRef.current = false
    setNewFileInput({ dirPath, depth })
    setTimeout(() => newFileInputRef.current?.focus(), 0)
  }, [onNewFile, findNode, toggleExpand])

  const handleNewFileSubmit = useCallback(async (value: string) => {
    if (newFileSubmittedRef.current) return
    if (!newFileInput || !onNewFile) return
    newFileSubmittedRef.current = true
    const name = value.trim()
    setNewFileInput(null)
    if (name) {
      const dirPath = newFileInput.dirPath === '.' ? '' : newFileInput.dirPath
      const ok = await onNewFile(dirPath, name)
      if (ok) {
        // Refresh tree to show the newly created file
        loadRoot()
      }
    }
  }, [newFileInput, onNewFile, loadRoot])

  const handleNewFileCancel = useCallback(() => {
    setNewFileInput(null)
  }, [])

  // Keyboard navigation
  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === '/') {
      e.preventDefault()
      setShowFilter(true)
      setTimeout(() => filterInputRef.current?.focus(), 0)
      return
    }
    if (e.key === 'Escape') {
      if (showFilter) {
        setShowFilter(false)
        setFilterText('')
      }
      setContextMenu(null)
      return
    }

    const len = flatNodes.length
    if (!len) return

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocusIndex(prev => Math.min(prev + 1, len - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocusIndex(prev => Math.max(prev - 1, 0))
    } else if (e.key === 'Enter') {
      if (focusIndex >= 0 && focusIndex < len) {
        const { node } = flatNodes[focusIndex]
        if (node.is_dir) {
          toggleExpand(node.path)
        } else {
          onFileSelect(node.path)
        }
      }
    } else if (e.key === 'ArrowRight') {
      if (focusIndex >= 0 && focusIndex < len) {
        const { node } = flatNodes[focusIndex]
        if (node.is_dir && !node.expanded) {
          toggleExpand(node.path)
        }
      }
    } else if (e.key === 'ArrowLeft') {
      if (focusIndex >= 0 && focusIndex < len) {
        const { node } = flatNodes[focusIndex]
        if (node.is_dir && node.expanded) {
          toggleExpand(node.path)
        }
      }
    }
  }, [flatNodes, focusIndex, showFilter, toggleExpand, onFileSelect])

  // Close context menu
  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  // Resize handle
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = width
    const parent = (e.target as HTMLElement).closest('.fe-content-area')
    parent?.classList.add('resizing')

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientX - startX
      onWidthChange(startWidth + delta)
    }
    const onUp = () => {
      parent?.classList.remove('resizing')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [width, onWidthChange])

  // Truncated work_dir for display
  const displayPath = workDir
    ? workDir.split('/').slice(-2).join('/')
    : ''

  return (
    <>
      <div
        className={`fe-panel ${isOpen ? 'open' : ''}`}
        style={{ width: isOpen ? width : 0 }}
        role="tree"
        tabIndex={0}
        onKeyDown={handleKeyDown}
        ref={treeRef}
      >
        {/* Header */}
        <div className="fe-header">
          <span className="fe-header-title">EXPLORER</span>
          <div className="fe-header-actions">
            <button
              className="fe-header-btn"
              onClick={() => {
                setShowFilter(prev => !prev)
                if (!showFilter) setTimeout(() => filterInputRef.current?.focus(), 0)
              }}
              title="Filter files (/)"
            >
              <IconFilter size={14} />
            </button>
            <button
              className="fe-header-btn"
              onClick={loadRoot}
              title="Refresh"
            >
              <IconRefresh size={14} />
            </button>
          </div>
        </div>

        {/* Work dir path */}
        <div className="fe-workdir" title={workDir || ''}>
          {displayPath}
        </div>

        {/* Filter input */}
        {showFilter && (
          <div className="fe-filter">
            <input
              ref={filterInputRef}
              className="fe-filter-input"
              type="text"
              placeholder="Filter files..."
              value={filterText}
              onChange={e => setFilterText(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Escape') {
                  setShowFilter(false)
                  setFilterText('')
                  treeRef.current?.focus()
                }
              }}
            />
            <button
              className="fe-filter-close"
              onClick={() => { setShowFilter(false); setFilterText('') }}
            >
              <IconX size={12} />
            </button>
          </div>
        )}

        {/* Tree content */}
        <div
          className="fe-tree"
          onContextMenu={e => {
            // Right-click on empty area (not on a node)
            if ((e.target as HTMLElement).classList.contains('fe-tree')) {
              e.preventDefault()
              setContextMenu({ x: e.clientX, y: e.clientY, path: '.' })
            }
          }}
        >
          {rootLoading && tree.length === 0 ? (
            <div className="fe-skeleton">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="fe-skeleton-row" style={{ paddingLeft: (i % 3 === 0 ? 0 : i % 3 === 1 ? 16 : 32) + 4 }}>
                  <span className="fe-skeleton-icon" />
                  <span className="fe-skeleton-text" style={{ width: `${40 + ((i * 17) % 60)}%` }} />
                </div>
              ))}
            </div>
          ) : viewMode === 'files' ? (
            <>
              {/* New file input at root level */}
              {newFileInput && (newFileInput.dirPath === '.' || newFileInput.dirPath === '') && (
                <div className="fe-node fe-node--new-file" style={{ paddingLeft: 4 }}>
                  <span className="fe-chevron fe-chevron--spacer" />
                  <span className="fe-icon"><IconFile size={16} /></span>
                  <input
                    ref={newFileInputRef}
                    className="fe-new-file-input"
                    placeholder="filename"
                    onKeyDown={e => {
                      if (e.key === 'Enter') handleNewFileSubmit((e.target as HTMLInputElement).value)
                      if (e.key === 'Escape') handleNewFileCancel()
                    }}
                    onBlur={e => handleNewFileSubmit(e.target.value)}
                  />
                </div>
              )}
              {flatNodes.map(({ node, depth }, i) => {
                const dimmed = filterText && !node.name.toLowerCase().includes(filterText.toLowerCase())
                // Show new-file input row after an expanded directory node
                const showNewFileAfter = newFileInput
                  && node.is_dir && node.expanded
                  && node.path === newFileInput.dirPath
                return (
                  <React.Fragment key={node.path}>
                    <div
                      className={`fe-node ${node.path === selectedFile ? 'fe-node--selected' : ''} ${focusIndex === i ? 'fe-node--focused' : ''} ${node.git_status ? `fe-node--git-${node.git_status}` : ''} ${dimmed ? 'fe-node--dimmed' : ''}`}
                      style={{ paddingLeft: depth * 16 + 4 }}
                      role="treeitem"
                      aria-expanded={node.is_dir ? node.expanded : undefined}
                      aria-level={depth + 1}
                      aria-selected={node.path === selectedFile}
                      onClick={() => {
                        setFocusIndex(i)
                        if (node.is_dir) {
                          toggleExpand(node.path)
                        } else {
                          onFileSelect(node.path)
                        }
                      }}
                      onDoubleClick={() => {
                        if (!node.is_dir && onFileDoubleClick) {
                          onFileDoubleClick(node.path)
                        }
                      }}
                      onContextMenu={e => {
                        e.preventDefault()
                        setContextMenu({ x: e.clientX, y: e.clientY, path: node.path })
                      }}
                    >
                      {/* Indent guides */}
                      {depth > 0 && Array.from({ length: depth }).map((_, d) => (
                        <span key={d} className="fe-indent-guide" style={{ left: d * 16 + 8 }} />
                      ))}

                      {/* Chevron for dirs */}
                      {node.is_dir ? (
                        <span className={`fe-chevron ${node.expanded ? 'fe-chevron--open' : ''}`}>
                          <IconChevronRight size={14} />
                        </span>
                      ) : (
                        <span className="fe-chevron fe-chevron--spacer" />
                      )}

                      {/* Icon */}
                      <span className="fe-icon">
                        {node.is_dir
                          ? (node.expanded ? <IconFolderOpen size={16} /> : <IconFolder size={16} />)
                          : <IconFile size={16} />
                        }
                      </span>

                      {/* Name */}
                      <span className="fe-name">{node.name}</span>

                      {/* Meta: git badge */}
                      {node.git_status && GIT_BADGE[node.git_status] && (
                        <span className={`fe-git-badge fe-git-badge--${node.git_status}`}>
                          {GIT_BADGE[node.git_status]}
                        </span>
                      )}

                      {/* Loading spinner */}
                      {node.loading && <span className="fe-spinner" />}
                    </div>

                    {/* Inline new-file input inside expanded directory */}
                    {showNewFileAfter && (
                      <div className="fe-node fe-node--new-file" style={{ paddingLeft: (depth + 1) * 16 + 4 }}>
                        <span className="fe-chevron fe-chevron--spacer" />
                        <span className="fe-icon"><IconFile size={16} /></span>
                        <input
                          ref={newFileInputRef}
                          className="fe-new-file-input"
                          placeholder="filename"
                          onKeyDown={e => {
                            if (e.key === 'Enter') handleNewFileSubmit((e.target as HTMLInputElement).value)
                            if (e.key === 'Escape') handleNewFileCancel()
                          }}
                          onBlur={e => handleNewFileSubmit(e.target.value)}
                        />
                      </div>
                    )}
                  </React.Fragment>
                )
              })
            }</>

          ) : (
            /* Changed files view */
            changedFiles.length === 0 ? (
              <div className="fe-empty">No changes detected</div>
            ) : (
              changedFiles.map(f => (
                <div
                  key={f.path}
                  className={`fe-node fe-node--changed ${f.path === selectedFile ? 'fe-node--selected' : ''} ${f.git_status ? `fe-node--git-${f.git_status}` : ''}`}
                  onClick={() => onFileSelect(f.path)}
                  onContextMenu={e => {
                    e.preventDefault()
                    setContextMenu({ x: e.clientX, y: e.clientY, path: f.path })
                  }}
                >
                  <span className="fe-icon"><IconFile size={16} /></span>
                  <span className="fe-name">{f.path}</span>
                  {f.git_status && GIT_BADGE[f.git_status] && (
                    <span className={`fe-git-badge fe-git-badge--${f.git_status}`}>
                      {GIT_BADGE[f.git_status]}
                    </span>
                  )}
                </div>
              ))
            )
          )}
        </div>

        {/* View mode tabs */}
        <div className="fe-tabs">
          <button
            className={`fe-tab ${viewMode === 'files' ? 'fe-tab--active' : ''}`}
            onClick={() => onViewModeChange('files')}
          >
            Files
          </button>
          <button
            className={`fe-tab ${viewMode === 'changed' ? 'fe-tab--active' : ''}`}
            onClick={() => onViewModeChange('changed')}
            disabled={!gitAvailable}
          >
            Changed
          </button>
        </div>
      </div>

      {/* Resize handle */}
      {isOpen && (
        <div
          className="fe-resize-handle"
          onMouseDown={handleResizeStart}
        />
      )}

      {/* Context menu with backdrop */}
      {contextMenu && (
        <>
          <div className="fe-context-backdrop" onClick={closeContextMenu} onContextMenu={e => { e.preventDefault(); closeContextMenu() }} />
          <div
            className="fe-context-menu"
            style={{ left: contextMenu.x, top: contextMenu.y }}
          >
            {onNewFile && <button onClick={() => handleNewFile(contextMenu.path)}>New File</button>}
            <button onClick={() => handleCopyPath(contextMenu.path)}>Copy path</button>
            <button onClick={() => handleCopyRelativePath(contextMenu.path)}>Copy relative path</button>
            <button onClick={() => { closeContextMenu(); loadRoot() }}>Refresh</button>
            <button onClick={handleCollapseAll}>Collapse all</button>
          </div>
        </>
      )}
    </>
  )
}
