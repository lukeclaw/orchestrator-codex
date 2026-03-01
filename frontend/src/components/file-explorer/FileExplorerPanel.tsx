import React, { useState, useEffect, useCallback, useRef, useMemo, type KeyboardEvent } from 'react'
import { api } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import {
  IconChevronRight,
  IconFilter,
  IconX,
  IconEye,
  IconEyeOff,
} from '../common/Icons'
import { getFileIconUrl, getFolderIconUrl } from './fileIcons'
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
  showIgnored: boolean
  onToggleIgnored: () => void
  onFileDeleted?: (path: string) => void
  onFileRenamed?: (oldPath: string, newPath: string) => void
  isRemote?: boolean
  onConnectingChange?: (connecting: boolean) => void
}

// --- Helpers ---

const INDENT_PX = 8

const GIT_BADGE: Record<string, string> = {
  modified: 'M',
  added: 'A',
  untracked: 'U',
  deleted: 'D',
  renamed: 'R',
  conflicting: '!',
  ignored: 'I',
}

const GIT_STATUS_SEVERITY: Record<string, number> = {
  ignored: 0,
  untracked: 1,
  added: 2,
  renamed: 3,
  modified: 4,
  deleted: 5,
  conflicting: 6,
}

function mergeGitStatuses(statuses: (string | null)[]): string | null {
  let best: string | null = null
  let bestSev = -1
  for (const s of statuses) {
    if (s && (GIT_STATUS_SEVERITY[s] ?? -1) > bestSev) {
      best = s
      bestSev = GIT_STATUS_SEVERITY[s] ?? -1
    }
  }
  return best
}

interface FlatNode {
  node: TreeNode
  depth: number
  displayName: string
  chainPaths: string[]
  mergedGitStatus: string | null
}

/** Propagate a parent's git_status down to cached children that lack their own. */
function propagateGitStatus(nodes: TreeNode[], status: string): TreeNode[] {
  return nodes.map(n => {
    const updated = n.git_status ? n : { ...n, git_status: status }
    if (updated.children && (updated.git_status === 'untracked' || updated.git_status === 'ignored')) {
      return { ...updated, children: propagateGitStatus(updated.children, updated.git_status) }
    }
    return updated
  })
}

/** Convert API entries into TreeNodes, preserving pre-fetched children. */
function entriesToNodes(entries: FileEntry[], oldNodes?: TreeNode[]): TreeNode[] {
  const oldMap = new Map<string, TreeNode>()
  if (oldNodes) {
    for (const n of oldNodes) oldMap.set(n.path, n)
  }
  return entries.map(e => {
    const old = oldMap.get(e.path)
    let children: TreeNode[] | undefined
    if (e.children) {
      children = entriesToNodes(e.children, old?.children)
    } else if (old?.expanded && old.children) {
      children = old.children
      // When the parent is untracked/ignored, propagate status to cached
      // children that may have been fetched before the status was known.
      if (e.git_status === 'untracked' || e.git_status === 'ignored') {
        children = propagateGitStatus(children, e.git_status)
      }
    }
    return {
      ...e,
      expanded: old?.expanded ?? false,
      children,
    }
  })
}

/** Extract the file/folder name from a path. */
function extractName(path: string): string {
  return path.split('/').pop() || path
}

/** Get the parent directory path from a relative path. */
function parentDir(path: string): string {
  const parts = path.split('/')
  return parts.length > 1 ? parts.slice(0, -1).join('/') : ''
}

/** Check if ancestor is a parent of descendant. */
function isAncestor(ancestor: string, descendant: string): boolean {
  return descendant === ancestor || descendant.startsWith(ancestor + '/')
}

// --- Optimistic tree mutations ---

/** Remove a node by path from the tree. */
function removeNode(nodes: TreeNode[], path: string): TreeNode[] {
  return nodes
    .filter(n => n.path !== path)
    .map(n => n.children ? { ...n, children: removeNode(n.children, path) } : n)
}

/** Insert a node into a directory in the tree (sorted by name). */
function insertNode(nodes: TreeNode[], dirPath: string, node: TreeNode): TreeNode[] {
  if (dirPath === '' || dirPath === '.') {
    // Insert at root level, sorted: dirs first, then alphabetical
    const result = [...nodes, node]
    return result.sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  }
  return nodes.map(n => {
    if (n.path === dirPath && n.children) {
      const children = [...n.children, node].sort((a, b) => {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
        return a.name.localeCompare(b.name)
      })
      return { ...n, children, expanded: true }
    }
    if (n.children) return { ...n, children: insertNode(n.children, dirPath, node) }
    return n
  })
}

/** Update all paths under a renamed/moved node. */
function renamePaths(node: TreeNode, oldPath: string, newPath: string): TreeNode {
  const updated: TreeNode = {
    ...node,
    path: newPath,
    name: extractName(newPath),
  }
  if (node.children) {
    updated.children = node.children.map(child => {
      const childNewPath = newPath + child.path.slice(oldPath.length)
      return renamePaths(child, child.path, childNewPath)
    })
  }
  return updated
}

/** Auto-expand single-child directory chains so compact rendering kicks in. */
function autoMarkExpanded(nodes: TreeNode[]): TreeNode[] {
  return nodes.map(n => {
    if (!n.is_dir || !n.children) return n
    if (n.children.length === 1 && n.children[0].is_dir) {
      return { ...n, expanded: true, children: autoMarkExpanded(n.children) }
    }
    return n
  })
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
  showIgnored,
  onToggleIgnored,
  onFileDeleted,
  onFileRenamed,
  isRemote,
  onConnectingChange,
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
  const [rootLoading, setRootLoading] = useState(false)
  const [connecting, setConnectingRaw] = useState(false)
  const setConnecting = useCallback((v: boolean) => {
    setConnectingRaw(v)
    onConnectingChange?.(v)
  }, [onConnectingChange])
  const [filterText, setFilterText] = useState('')
  const [showFilter, setShowFilter] = useState(false)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; path: string } | null>(null)
  const [focusIndex, setFocusIndex] = useState(-1)
  // Inline new-file input: { dirPath, depth } when active
  const [newFileInput, setNewFileInput] = useState<{ dirPath: string; depth: number } | null>(null)
  const newFileInputRef = useRef<HTMLInputElement>(null)
  const newFileSubmittedRef = useRef(false)
  // Inline new-folder input
  const [newFolderInput, setNewFolderInput] = useState<{ dirPath: string; depth: number } | null>(null)
  const newFolderInputRef = useRef<HTMLInputElement>(null)
  const newFolderSubmittedRef = useRef(false)
  // Inline rename input
  const [renameTarget, setRenameTarget] = useState<{ path: string; depth: number } | null>(null)
  const renameInputRef = useRef<HTMLInputElement>(null)
  const renameSubmittedRef = useRef(false)
  // Drag and drop (mouse-event based — HTML5 DnD doesn't work in Tauri WebView)
  const [dragState, setDragState] = useState<{
    sourcePath: string
    startX: number
    startY: number
    active: boolean  // true once mouse moved >5px from start
  } | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const dropTargetRef = useRef<string | null>(null)
  const filterInputRef = useRef<HTMLInputElement>(null)
  const treeRef = useRef<HTMLDivElement>(null)

  // Fetch root directory
  const fetchDir = useCallback(async (path: string = '.', depth = 1): Promise<{ entries: FileEntry[]; gitAvailable: boolean }> => {
    if (!workDir) return { entries: [], gitAvailable: false }
    const params = new URLSearchParams({ path, depth: String(depth), show_hidden: String(showIgnored) })
    const data = await api<DirectoryResponse>(
      `/api/sessions/${sessionId}/files?${params}`
    )
    return { entries: data.entries, gitAvailable: data.git_available }
  }, [sessionId, showIgnored, workDir])

  // Load root on mount / refresh — depth=1 for reliability on large dirs
  const loadRoot = useCallback(async () => {
    const isEmpty = treeSnapshotRef.current.length === 0
    if (isEmpty && isRemote) setConnecting(true)
    setRootLoading(true)
    try {
      const { entries } = await fetchDir('.', 1)
      setTree(prev => entriesToNodes(entries, prev))
      setConnecting(false)
    } catch {
      // Silently fail - work_dir may not exist yet
      // Keep connecting=true so skeleton persists for remote hosts
    } finally {
      setRootLoading(false)
    }
  }, [fetchDir, isRemote, setConnecting])

  // Initial load + auto-refresh every 10s while panel is open
  useEffect(() => {
    if (!isOpen || !workDir) return
    loadRoot()
    const id = setInterval(loadRoot, 10000)
    return () => clearInterval(id)
  }, [isOpen, workDir, loadRoot])

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
      // Children already cached — expand and auto-expand single-child dir chains
      setTree(prev => updateNode(prev, nodePath, n => ({
        ...n,
        expanded: true,
        children: n.children ? autoMarkExpanded(n.children) : undefined,
      })))
      return
    }

    // Expanding without cached children — mark loading and fetch (depth=4 for chain discovery)
    setTree(prev => updateNode(prev, nodePath, n => ({ ...n, loading: true, expanded: true })))

    try {
      const { entries } = await fetchDir(nodePath, 4)
      setTree(prev => updateNode(prev, nodePath, n => ({
        ...n,
        loading: false,
        expanded: true,
        children: autoMarkExpanded(entriesToNodes(entries)),
      })))
    } catch {
      // depth=4 failed (large dir) — try depth=1
      try {
        const { entries } = await fetchDir(nodePath, 1)
        setTree(prev => updateNode(prev, nodePath, n => ({
          ...n,
          loading: false,
          expanded: true,
          children: autoMarkExpanded(entriesToNodes(entries)),
        })))
      } catch {
        setTree(prev => updateNode(prev, nodePath, n => ({
          ...n,
          loading: false,
          expanded: false,
        })))
      }
    }
  }, [fetchDir, findNode, updateNode, setTree])

  // Flatten tree for keyboard nav and rendering (with compact folder chain support)
  const flatNodes = useMemo(() => {
    const result: FlatNode[] = []
    const walk = (nodes: TreeNode[], depth: number) => {
      for (const n of nodes) {
        const matchesFilter = !filterText || n.name.toLowerCase().includes(filterText.toLowerCase())
        if (!matchesFilter && !n.is_dir) continue

        // Compact folder logic: follow single-child dir chains (skip when filtering)
        if (!filterText && n.is_dir && n.expanded && n.children) {
          const chainNames: string[] = [n.name]
          const chainPaths: string[] = [n.path]
          const chainStatuses: (string | null)[] = [n.git_status]
          let current = n
          while (
            current.children &&
            current.children.length === 1 &&
            current.children[0].is_dir &&
            current.children[0].expanded
          ) {
            current = current.children[0]
            chainNames.push(current.name)
            chainPaths.push(current.path)
            chainStatuses.push(current.git_status)
          }

          result.push({
            node: current,
            depth,
            displayName: chainNames.join('/'),
            chainPaths,
            mergedGitStatus: mergeGitStatuses(chainStatuses),
          })

          if (current.expanded && current.children) {
            walk(current.children, depth + 1)
          }
          continue
        }

        // Normal (non-compacted) node
        result.push({
          node: n,
          depth,
          displayName: n.name,
          chainPaths: [n.path],
          mergedGitStatus: n.git_status,
        })

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
      const filePath = dirPath ? `${dirPath}/${name}` : name
      // Optimistic: insert node into tree immediately
      const newNode: TreeNode = {
        name, path: filePath, is_dir: false,
        size: 0, modified: Date.now() / 1000, children_count: null,
        git_status: 'untracked', human_size: '0 B', children: undefined,
      }
      setTree(prev => insertNode(prev, dirPath || '.', newNode))
      const ok = await onNewFile(dirPath, name)
      if (!ok) {
        // Revert if creation failed
        setTree(prev => removeNode(prev, filePath))
      }
    }
  }, [newFileInput, onNewFile, setTree])

  const handleNewFileCancel = useCallback(() => {
    setNewFileInput(null)
  }, [])

  // New folder — context menu handler
  const handleNewFolder = useCallback((contextPath: string) => {
    setContextMenu(null)
    const node = findNode(treeSnapshotRef.current, contextPath)
    const isDir = node?.is_dir ?? contextPath === '.'
    const dirPath = isDir ? contextPath : contextPath.split('/').slice(0, -1).join('/')
    let depth = 0
    if (dirPath && dirPath !== '.') {
      depth = dirPath.split('/').length
    }
    if (isDir && node && !node.expanded) {
      toggleExpand(contextPath)
    }
    newFolderSubmittedRef.current = false
    setNewFolderInput({ dirPath, depth })
    setTimeout(() => newFolderInputRef.current?.focus(), 0)
  }, [findNode, toggleExpand])

  const handleNewFolderSubmit = useCallback(async (value: string) => {
    if (newFolderSubmittedRef.current) return
    if (!newFolderInput) return
    newFolderSubmittedRef.current = true
    const name = value.trim()
    setNewFolderInput(null)
    if (name) {
      const dirPath = newFolderInput.dirPath === '.' ? '' : newFolderInput.dirPath
      const folderPath = dirPath ? `${dirPath}/${name}` : name
      // Optimistic: insert folder node into tree immediately
      const newNode: TreeNode = {
        name, path: folderPath, is_dir: true,
        size: null, modified: Date.now() / 1000, children_count: 0,
        git_status: null, human_size: null, children: [], expanded: true,
      }
      setTree(prev => insertNode(prev, dirPath || '.', newNode))
      try {
        await api(`/api/sessions/${sessionId}/files/mkdir`, {
          method: 'POST',
          body: JSON.stringify({ path: folderPath }),
        })
      } catch (e) {
        // Revert on failure
        setTree(prev => removeNode(prev, folderPath))
        notify(e instanceof Error ? e.message : 'Failed to create folder', 'error')
      }
    }
  }, [newFolderInput, sessionId, setTree, notify])

  const handleNewFolderCancel = useCallback(() => {
    setNewFolderInput(null)
  }, [])

  // Delete handler — right-click → Delete is already intentional, no confirmation needed
  const handleDelete = useCallback(async (path: string) => {
    setContextMenu(null)
    // Optimistic: remove from tree immediately
    const snapshot = treeSnapshotRef.current
    setTree(prev => removeNode(prev, path))
    onFileDeleted?.(path)
    try {
      await api(`/api/sessions/${sessionId}/files?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
    } catch (e) {
      // Revert on failure
      setTree(snapshot)
      notify(e instanceof Error ? e.message : 'Failed to delete', 'error')
    }
  }, [sessionId, onFileDeleted, setTree, notify])

  // Rename handler — show inline input
  const handleRenameStart = useCallback((path: string) => {
    setContextMenu(null)
    // Compute depth for the inline input
    const parts = path.split('/')
    const depth = parts.length - 1
    renameSubmittedRef.current = false
    setRenameTarget({ path, depth })
    setTimeout(() => {
      const input = renameInputRef.current
      if (input) {
        input.focus()
        // Select filename without extension for files
        const name = extractName(path)
        const dotIdx = name.lastIndexOf('.')
        if (dotIdx > 0) {
          input.setSelectionRange(0, dotIdx)
        } else {
          input.select()
        }
      }
    }, 0)
  }, [])

  const handleRenameSubmit = useCallback(async (value: string) => {
    if (renameSubmittedRef.current) return
    if (!renameTarget) return
    renameSubmittedRef.current = true
    const newName = value.trim()
    setRenameTarget(null)
    if (!newName || newName === extractName(renameTarget.path)) return
    const parent = parentDir(renameTarget.path)
    const newPath = parent ? `${parent}/${newName}` : newName
    // Optimistic: update the node path/name in tree immediately
    const snapshot = treeSnapshotRef.current
    setTree(prev => {
      const walk = (nodes: TreeNode[]): TreeNode[] =>
        nodes.map(n => {
          if (n.path === renameTarget.path) return renamePaths(n, renameTarget.path, newPath)
          if (n.children) return { ...n, children: walk(n.children) }
          return n
        })
      return walk(prev)
    })
    onFileRenamed?.(renameTarget.path, newPath)
    try {
      await api(`/api/sessions/${sessionId}/files/move`, {
        method: 'POST',
        body: JSON.stringify({ from_path: renameTarget.path, to_path: newPath }),
      })
    } catch (e) {
      setTree(snapshot)
      notify(e instanceof Error ? e.message : 'Failed to rename', 'error')
    }
  }, [renameTarget, sessionId, onFileRenamed, setTree, notify])

  const handleRenameCancel = useCallback(() => {
    setRenameTarget(null)
  }, [])

  // Drag and drop via mouse events (HTML5 DnD API doesn't work in Tauri WebView)
  const DRAG_THRESHOLD = 5

  const handleNodeMouseDown = useCallback((e: React.MouseEvent, path: string) => {
    // Only left button, ignore if renaming
    if (e.button !== 0) return
    setDragState({ sourcePath: path, startX: e.clientX, startY: e.clientY, active: false })
  }, [])

  // Keep ref in sync for use in mouseup handler (avoids stale closure)
  const updateDropTarget = useCallback((val: string | null) => {
    dropTargetRef.current = val
    setDropTarget(val)
  }, [])

  // Global mousemove/mouseup while dragging — attached via useEffect
  useEffect(() => {
    if (!dragState) return

    const handleMouseMove = (e: MouseEvent) => {
      if (!dragState.active) {
        const dx = e.clientX - dragState.startX
        const dy = e.clientY - dragState.startY
        if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return
        // Activate drag
        setDragState(prev => prev ? { ...prev, active: true } : null)
      }

      // Hit-test: find the .fe-node under the cursor
      const el = document.elementFromPoint(e.clientX, e.clientY)
      if (!el) { updateDropTarget(null); return }
      const nodeEl = (el as HTMLElement).closest?.('.fe-node') as HTMLElement | null
      if (!nodeEl) {
        // Over empty tree area → root
        const treeEl = (el as HTMLElement).closest?.('.fe-tree')
        updateDropTarget(treeEl ? '.' : null)
        return
      }
      const targetPath = nodeEl.dataset.path
      const isDir = nodeEl.dataset.isDir === 'true'
      if (!targetPath) { updateDropTarget(null); return }
      const dir = isDir ? targetPath : (parentDir(targetPath) || '.')
      updateDropTarget(dir)
    }

    const handleMouseUp = async () => {
      const state = dragState
      const currentDropTarget = dropTargetRef.current
      setDragState(null)
      updateDropTarget(null)

      if (!state.active || !currentDropTarget) return

      const sourcePath = state.sourcePath
      const targetDir = currentDropTarget

      // Validation
      if (sourcePath === targetDir) return
      if (isAncestor(sourcePath, targetDir)) return
      const sourceParent = parentDir(sourcePath)
      if (sourceParent === targetDir || (sourceParent === '' && targetDir === '.')) return

      const fileName = extractName(sourcePath)
      const toPath = targetDir === '.' || targetDir === '' ? fileName : `${targetDir}/${fileName}`

      // Optimistic: move node in tree immediately
      const snapshot = treeSnapshotRef.current
      const sourceNode = findNode(snapshot, sourcePath)
      if (sourceNode) {
        const movedNode = renamePaths(sourceNode, sourcePath, toPath)
        setTree(prev => {
          let next = removeNode(prev, sourcePath)
          // Expand target dir if collapsed
          if (targetDir !== '.' && targetDir !== '') {
            next = updateNode(next, targetDir, n => ({ ...n, expanded: true }))
          }
          return insertNode(next, targetDir, movedNode)
        })
      }
      onFileRenamed?.(sourcePath, toPath)

      try {
        await api(`/api/sessions/${sessionId}/files/move`, {
          method: 'POST',
          body: JSON.stringify({ from_path: sourcePath, to_path: toPath }),
        })
      } catch (err) {
        setTree(snapshot)
        notify(err instanceof Error ? err.message : 'Failed to move', 'error')
      }
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [dragState, sessionId, onFileRenamed, notify, updateDropTarget, findNode, setTree, updateNode])

  // Set cursor to grabbing during active drag
  useEffect(() => {
    if (dragState?.active) {
      document.body.style.cursor = 'grabbing'
      document.body.style.userSelect = 'none'
      return () => {
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
      }
    }
  }, [dragState?.active])

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
        const { node, chainPaths } = flatNodes[focusIndex]
        if (node.is_dir) {
          // Collapse compact chains from the first node
          if (node.expanded && chainPaths.length > 1) {
            toggleExpand(chainPaths[0])
          } else {
            toggleExpand(node.path)
          }
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
        const { node, chainPaths } = flatNodes[focusIndex]
        if (node.is_dir && node.expanded) {
          toggleExpand(chainPaths.length > 1 ? chainPaths[0] : node.path)
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
              onClick={onToggleIgnored}
              title={showIgnored ? 'Hide hidden files' : 'Show hidden files'}
              style={{ opacity: showIgnored ? 1 : 0.5 }}
            >
              {showIgnored ? <IconEye size={14} /> : <IconEyeOff size={14} />}
            </button>
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
          {(rootLoading || connecting) && tree.length === 0 ? (
            <div className="fe-skeleton">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="fe-skeleton-row" style={{ paddingLeft: (i % 3 === 0 ? 0 : i % 3 === 1 ? 16 : 32) + 4 }}>
                  <span className="fe-skeleton-icon" />
                  <span className="fe-skeleton-text" style={{ width: `${40 + ((i * 17) % 60)}%` }} />
                </div>
              ))}
              {connecting && <div className="fe-skeleton-message">Connecting to remote host…</div>}
            </div>
          ) : (
            <>
              {/* New file input at root level */}
              {newFileInput && (newFileInput.dirPath === '.' || newFileInput.dirPath === '') && (
                <div className="fe-node fe-node--new-file" style={{ paddingLeft: 4 }}>
                  <span className="fe-chevron fe-chevron--spacer" />
                  <span className="fe-icon"><img src={getFileIconUrl('')} alt="" width={16} height={16} draggable={false} /></span>
                  <input
                    ref={newFileInputRef}
                    className="fe-new-file-input"
                    placeholder="filename"
                    onKeyDown={e => {
                      e.stopPropagation()
                      if (e.key === 'Enter') handleNewFileSubmit((e.target as HTMLInputElement).value)
                      if (e.key === 'Escape') handleNewFileCancel()
                    }}
                    onBlur={e => handleNewFileSubmit(e.target.value)}
                  />
                </div>
              )}
              {/* New folder input at root level */}
              {newFolderInput && (newFolderInput.dirPath === '.' || newFolderInput.dirPath === '') && (
                <div className="fe-node fe-node--new-file" style={{ paddingLeft: 4 }}>
                  <span className="fe-chevron fe-chevron--spacer" />
                  <span className="fe-icon"><img src={getFolderIconUrl('', false)} alt="" width={16} height={16} draggable={false} /></span>
                  <input
                    ref={newFolderInputRef}
                    className="fe-new-file-input"
                    placeholder="folder name"
                    onKeyDown={e => {
                      e.stopPropagation()
                      if (e.key === 'Enter') handleNewFolderSubmit((e.target as HTMLInputElement).value)
                      if (e.key === 'Escape') handleNewFolderCancel()
                    }}
                    onBlur={e => handleNewFolderSubmit(e.target.value)}
                  />
                </div>
              )}
              {flatNodes.map(({ node, depth, displayName, chainPaths, mergedGitStatus }, i) => {
                const dimmed = filterText && !displayName.toLowerCase().includes(filterText.toLowerCase())
                // Show new-file/folder input row after an expanded directory node
                const showNewFileAfter = newFileInput
                  && node.is_dir && node.expanded
                  && node.path === newFileInput.dirPath
                const showNewFolderAfter = newFolderInput
                  && node.is_dir && node.expanded
                  && node.path === newFolderInput.dirPath
                const isRenaming = renameTarget?.path === node.path
                const isDragging = dragState?.active && dragState.sourcePath === node.path
                const isDropTarget = node.is_dir && dropTarget === node.path
                return (
                  <React.Fragment key={node.path}>
                    <div
                      className={`fe-node ${node.path === selectedFile ? 'fe-node--selected' : ''} ${focusIndex === i ? 'fe-node--focused' : ''} ${!node.is_dir && mergedGitStatus ? `fe-node--git-${mergedGitStatus}` : ''} ${dimmed ? 'fe-node--dimmed' : ''} ${isDragging ? 'fe-node--dragging' : ''} ${isDropTarget ? 'fe-node--drop-target' : ''}`}
                      style={{ paddingLeft: depth * INDENT_PX + 4 }}
                      role="treeitem"
                      aria-expanded={node.is_dir ? node.expanded : undefined}
                      aria-level={depth + 1}
                      aria-selected={node.path === selectedFile}
                      data-path={node.path}
                      data-is-dir={String(node.is_dir)}
                      onMouseDown={!isRenaming ? (e: React.MouseEvent) => handleNodeMouseDown(e, node.path) : undefined}
                      onClick={() => {
                        setFocusIndex(i)
                        if (node.is_dir) {
                          // Collapse compact chains from the first node
                          if (node.expanded && chainPaths.length > 1) {
                            toggleExpand(chainPaths[0])
                          } else {
                            toggleExpand(node.path)
                          }
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
                        <span key={d} className="fe-indent-guide" style={{ left: d * INDENT_PX + INDENT_PX / 2 }} />
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
                        <img
                          src={node.is_dir
                            ? getFolderIconUrl(node.name, !!node.expanded)
                            : getFileIconUrl(node.name)
                          }
                          alt=""
                          width={16}
                          height={16}
                          loading="lazy"
                          draggable={false}
                        />
                        {node.is_dir && mergedGitStatus && mergedGitStatus !== 'ignored' && (
                          <span className={`fe-git-dot fe-git-dot--${mergedGitStatus}`} />
                        )}
                      </span>

                      {/* Name or inline rename input */}
                      {isRenaming ? (
                        <input
                          ref={renameInputRef}
                          className="fe-new-file-input"
                          defaultValue={node.name}
                          onKeyDown={e => {
                            e.stopPropagation()
                            if (e.key === 'Enter') handleRenameSubmit((e.target as HTMLInputElement).value)
                            if (e.key === 'Escape') handleRenameCancel()
                          }}
                          onBlur={e => handleRenameSubmit(e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      ) : (
                        <span className="fe-name">{displayName}</span>
                      )}

                      {/* Meta: git badge */}
                      {!isRenaming && mergedGitStatus && GIT_BADGE[mergedGitStatus] && (
                        <span className={`fe-git-badge fe-git-badge--${mergedGitStatus}`}>
                          {GIT_BADGE[mergedGitStatus]}
                        </span>
                      )}

                      {/* Loading spinner */}
                      {node.loading && <span className="fe-spinner" />}
                    </div>

                    {/* Inline new-file input inside expanded directory */}
                    {showNewFileAfter && (
                      <div className="fe-node fe-node--new-file" style={{ paddingLeft: (depth + 1) * INDENT_PX + 4 }}>
                        <span className="fe-chevron fe-chevron--spacer" />
                        <span className="fe-icon"><img src={getFileIconUrl('')} alt="" width={16} height={16} draggable={false} /></span>
                        <input
                          ref={newFileInputRef}
                          className="fe-new-file-input"
                          placeholder="filename"
                          onKeyDown={e => {
                            e.stopPropagation()
                            if (e.key === 'Enter') handleNewFileSubmit((e.target as HTMLInputElement).value)
                            if (e.key === 'Escape') handleNewFileCancel()
                          }}
                          onBlur={e => handleNewFileSubmit(e.target.value)}
                        />
                      </div>
                    )}
                    {/* Inline new-folder input inside expanded directory */}
                    {showNewFolderAfter && (
                      <div className="fe-node fe-node--new-file" style={{ paddingLeft: (depth + 1) * INDENT_PX + 4 }}>
                        <span className="fe-chevron fe-chevron--spacer" />
                        <span className="fe-icon"><img src={getFolderIconUrl('', false)} alt="" width={16} height={16} draggable={false} /></span>
                        <input
                          ref={newFolderInputRef}
                          className="fe-new-file-input"
                          placeholder="folder name"
                          onKeyDown={e => {
                            e.stopPropagation()
                            if (e.key === 'Enter') handleNewFolderSubmit((e.target as HTMLInputElement).value)
                            if (e.key === 'Escape') handleNewFolderCancel()
                          }}
                          onBlur={e => handleNewFolderSubmit(e.target.value)}
                        />
                      </div>
                    )}
                  </React.Fragment>
                )
              })
            }</>
          )}
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
            <button onClick={() => handleNewFolder(contextMenu.path)}>New Folder</button>
            {contextMenu.path !== '.' && (
              <button onClick={() => handleRenameStart(contextMenu.path)}>Rename</button>
            )}
            {contextMenu.path !== '.' && (
              <button onClick={() => handleDelete(contextMenu.path)}>Delete</button>
            )}
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
