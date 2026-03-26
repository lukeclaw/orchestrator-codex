import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import Modal from '../components/common/Modal'
import { isSupportedDropFile, LARGE_FILE_THRESHOLD, fileToBase64 } from '../utils/fileDropUtils'
import { useNotify } from '../context/NotificationContext'
import { useApp } from '../context/AppContext'
import { useSmartPaste } from '../hooks/useSmartPaste'
import { useFileExplorerState } from '../hooks/useFileExplorerState'
import { useEditorTabs } from '../hooks/useEditorTabs'
import TerminalView from '../components/terminal/TerminalView'
import InteractiveCLI from '../components/terminal/InteractiveCLI'
import BrowserView from '../components/browser/BrowserView'
import FileExplorerPanel from '../components/file-explorer/FileExplorerPanel'
import FileViewer from '../components/file-explorer/FileViewer'
import ProviderBadge from '../components/common/ProviderBadge'
import { IconPause, IconPlay, IconStop, IconRefresh, IconTrash, IconSync, IconBrain } from '../components/common/Icons'
import ConfirmPopover from '../components/common/ConfirmPopover'
import AssignTaskModal from '../components/tasks/AssignTaskModal'
import './SessionDetailPage.css'

interface TunnelInfo {
  remote_port: number
  pid: number
  host: string
}

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const notify = useNotify()
  
  // Use shared state from AppContext for session
  const { sessions, tasks: allTasks, refresh, interactiveCliSessions, interactiveCliMinimized, closeInteractiveCli, browserViewSessions, browserViewMinimized, closeBrowserView } = useApp()
  const session = sessions.find(s => s.id === id) || null
  const tasks = allTasks.filter(t => t.assigned_session_id === id)
  const isRdev = session?.host?.includes('/') ?? false
  const isSsh = !isRdev && (session?.host ? session.host !== 'localhost' : false)
  const isRemote = session?.host ? session.host !== 'localhost' : false

  const { readClipboard } = useSmartPaste()
  const terminalFocusRef = useRef<(() => void) | null>(null)

  // Tunnel state for rdev workers
  const [tunnels, setTunnels] = useState<Record<string, TunnelInfo>>({})
  const tunnelIntervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  // File explorer state
  const fe = useFileExplorerState(id)
  const editorTabs = useEditorTabs(id!)

  // Local state for page-specific data
  const [error, setError] = useState('')
  const [actionPending, setActionPending] = useState(false)
  const [pasting, setPasting] = useState(false)
  const [ctxPasting, setCtxPasting] = useState(false)
  const [showAssignTask, setShowAssignTask] = useState(false)
  const [dropFile, setDropFile] = useState<File | null>(null)
  const [dropUploading, setDropUploading] = useState(false)
  const [showLargeFileModal, setShowLargeFileModal] = useState(false)
  const [feConnecting, setFeConnecting] = useState(false)
  const [icliActiveLocal, setIcliActiveLocal] = useState(false)
  const [icliStarting, setIcliStarting] = useState(false)
  const [icliMinimized, setIcliMinimized] = useState(() => id ? interactiveCliMinimized.has(id) : false)
  const [bvActiveLocal, setBvActiveLocal] = useState(false)
  const [bvStarting, setBvStarting] = useState(false)
  const [bvMinimized, setBvMinimized] = useState(() => id ? browserViewMinimized.has(id) : false)

  // icliActive combines local state (from mount check) with AppContext WS events
  const icliFromContext = id ? interactiveCliSessions.has(id) : false
  const icliActive = icliActiveLocal || icliFromContext

  // Browser view state (same pattern as interactive CLI)
  const bvFromContext = id ? browserViewSessions.has(id) : false
  const bvActive = bvActiveLocal || bvFromContext

  // Auto-show overlay when interactive CLI becomes active (via WS event)
  useEffect(() => {
    if (icliFromContext) {
      setIcliActiveLocal(true)
      setIcliMinimized(false) // Show full when agent opens it
    }
  }, [icliFromContext])

  // Auto-hide when WS says closed
  useEffect(() => {
    if (!icliFromContext && icliActiveLocal) {
      // WS event says closed — re-check via API to confirm
      if (!id) return
      api<{ active: boolean }>(`/api/sessions/${id}/interactive-cli`)
        .then(r => {
          if (!r.active) {
            setIcliActiveLocal(false)
            setIcliMinimized(false)
          }
        })
        .catch(() => {})
    }
  }, [icliFromContext, icliActiveLocal, id])

  // Sync minimize state from WS events (agent triggered minimize/restore)
  useEffect(() => {
    if (id) {
      setIcliMinimized(interactiveCliMinimized.has(id))
    }
  }, [id, interactiveCliMinimized])

  // Check interactive CLI status on mount — restore previous state
  useEffect(() => {
    if (!id) return
    api<{ active: boolean }>(`/api/sessions/${id}/interactive-cli`)
      .then(r => {
        if (r.active) {
          setIcliActiveLocal(true)
          // Preserve minimize state from WS context; default to open
          setIcliMinimized(interactiveCliMinimized.has(id))
        }
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Auto-show browser view overlay when it becomes active (via WS event)
  useEffect(() => {
    if (bvFromContext) {
      setBvActiveLocal(true)
      setBvMinimized(false) // Show full when agent opens it
    }
  }, [bvFromContext])

  // Auto-hide browser view when WS says closed
  useEffect(() => {
    if (!bvFromContext && bvActiveLocal) {
      if (!id) return
      api<{ active: boolean }>(`/api/sessions/${id}/browser-view`)
        .then(r => {
          if (!r.active) {
            setBvActiveLocal(false)
            setBvMinimized(false)
          }
        })
        .catch(() => {})
    }
  }, [bvFromContext, bvActiveLocal, id])

  // Sync minimize state from WS events (agent triggered minimize/restore)
  useEffect(() => {
    if (id) {
      setBvMinimized(browserViewMinimized.has(id))
    }
  }, [id, browserViewMinimized])

  // Check browser view status on mount — restore previous state
  useEffect(() => {
    if (!id) return
    api<{ active: boolean }>(`/api/sessions/${id}/browser-view`)
      .then(r => {
        if (r.active) {
          setBvActiveLocal(true)
          // Preserve minimize state from WS context; default to open
          setBvMinimized(browserViewMinimized.has(id))
        }
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Record that user viewed this session
  useEffect(() => {
    if (id) {
      api(`/api/sessions/${id}/viewed`, { method: 'POST' }).catch(() => {})
    }
  }, [id])

  // Fetch tunnels for remote workers (rdev and SSH)
  useEffect(() => {
    if (!isRemote || !id) {
      setTunnels({})
      return
    }

    const targetSessionId = id

    async function fetchTunnels() {
      try {
        const data = await api<{ tunnels: Record<string, TunnelInfo> }>(
          `/api/sessions/${targetSessionId}/tunnels`
        )
        setTunnels(data.tunnels || {})
      } catch {
        // Silently ignore tunnel fetch errors
      }
    }

    fetchTunnels()
    tunnelIntervalRef.current = setInterval(fetchTunnels, 10000)

    return () => {
      clearInterval(tunnelIntervalRef.current)
    }
  }, [id, isRemote])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: globalThis.KeyboardEvent) => {
      // Ctrl+Shift+E to toggle file explorer
      if (e.ctrlKey && e.shiftKey && e.key === 'E') {
        e.preventDefault()
        fe.toggleOpen()
      }
      // Ctrl+S: save active tab
      if ((e.ctrlKey || e.metaKey) && e.key === 's' && !e.shiftKey) {
        e.preventDefault()
        if (editorTabs.activeTabPath) editorTabs.saveTab(editorTabs.activeTabPath)
      }
      // Cmd/Ctrl+W: close active tab when file viewer is open
      if ((e.ctrlKey || e.metaKey) && e.key === 'w' && !e.shiftKey) {
        if (fe.open && editorTabs.activeTabPath) {
          e.preventDefault()
          editorTabs.closeTab(editorTabs.activeTabPath)
        }
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [fe.toggleOpen, editorTabs])

  // Warn before leaving with unsaved changes
  useEffect(() => {
    if (!editorTabs.hasAnyDirty) return
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [editorTabs.hasAnyDirty])

  // File explorer handlers
  const handleFileSelect = useCallback((path: string) => {
    editorTabs.openTab(path, false)  // single-click = new pinned tab
  }, [editorTabs])

  const handleFileDoubleClick = useCallback((path: string) => {
    editorTabs.openTab(path, false)  // double-click = pinned
  }, [editorTabs])

  const handleNewFile = useCallback((dirPath: string, fileName: string) => {
    return editorTabs.openNewFile(dirPath, fileName)
  }, [editorTabs])

  const handleFileDeleted = useCallback((path: string) => {
    editorTabs.closeTabsByPrefix(path)
  }, [editorTabs])

  const handleFileRenamed = useCallback((oldPath: string, newPath: string) => {
    editorTabs.renameTabPaths(oldPath, newPath)
  }, [editorTabs])

  // Viewer resize handle
  const handleViewerResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startY = e.clientY
    const startRatio = fe.viewerHeightRatio
    const container = (e.target as HTMLElement).closest('.fe-right-pane')
    if (!container) return
    const containerHeight = container.getBoundingClientRect().height
    container.closest('.fe-content-area')?.classList.add('resizing')

    const onMove = (ev: MouseEvent) => {
      const delta = ev.clientY - startY
      const newRatio = startRatio + delta / containerHeight
      fe.updateViewerHeightRatio(newRatio)
    }
    const onUp = () => {
      container.closest('.fe-content-area')?.classList.remove('resizing')
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [fe.viewerHeightRatio, fe.updateViewerHeightRatio])

  async function handlePauseOrContinue() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      const endpoint = session?.status === 'paused' ? 'continue' : 'pause'
      await api(`/api/sessions/${id}/${endpoint}`, { method: 'POST' })
      refresh()
      notify(`Worker ${endpoint === 'pause' ? 'paused' : 'resumed'}`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : `Failed to ${session?.status === 'paused' ? 'continue' : 'pause'}`, 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleStop() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${id}/stop`, { method: 'POST' })
      refresh()
      notify(`Worker stopped and cleared`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to stop worker', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleReconnect() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${id}/reconnect`, { method: 'POST' })
      refresh()
      notify(`Reconnecting worker...`, 'info')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to reconnect', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleToggleAutoReconnect() {
    if (!id) return
    try {
      const result = await api<{ ok: boolean; auto_reconnect: boolean }>(
        `/api/sessions/${id}/auto-reconnect`,
        { method: 'POST' }
      )
      refresh()
      notify(`Auto-reconnect ${result.auto_reconnect ? 'enabled' : 'disabled'}`, 'success')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to toggle auto-reconnect', 'error')
    }
  }

  async function handleHealthCheck() {
    if (!id || actionPending) return
    setActionPending(true)
    try {
      const result = await api<{ alive: boolean; status: string; reason: string }>(
        `/api/sessions/${id}/health-check`,
        { method: 'POST' }
      )
      await refresh()  // Wait for data to refresh before showing notification
      if (result.alive) {
        notify(`Worker is alive: ${result.reason}`, 'success')
      } else {
        notify(`Worker disconnected: ${result.reason}`, 'warning')
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to check status', 'error')
    } finally {
      setActionPending(false)
    }
  }

  async function handleDelete() {
    if (!id) return
    try {
      await api(`/api/sessions/${id}`, { method: 'DELETE' })
      refresh()
      navigate(-1)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to delete', 'error')
    }
  }

  async function handleCheckProgress() {
    if (!id || actionPending) return
    // Validate ID is a UUID, not 'auto' or other keywords
    if (!/^[0-9a-f-]{36}$/i.test(id)) {
      notify(`Invalid worker ID: ${id}`, 'error')
      return
    }
    setActionPending(true)
    try {
      // Get brain session ID first
      const brainStatus = await api<{ session_id: string | null; running: boolean }>('/api/brain/status')
      if (!brainStatus.running || !brainStatus.session_id) {
        notify('Brain is not running. Start the brain first.', 'error')
        return
      }
      // Cancel any existing input and clear the line
      // Ctrl-C to cancel, then Ctrl-U to clear line buffer
      await api(`/api/sessions/${brainStatus.session_id}/send`, {
        method: 'POST',
        body: JSON.stringify({ message: '\x03' }),  // Ctrl-C
      })
      await new Promise(resolve => setTimeout(resolve, 50))
      await api(`/api/sessions/${brainStatus.session_id}/send`, {
        method: 'POST',
        body: JSON.stringify({ message: '\x15' }),  // Ctrl-U to clear line
      })
      await new Promise(resolve => setTimeout(resolve, 50))
      // Send check_worker command to brain for this specific worker
      const message = `/check_worker ${id}`
      await api(`/api/sessions/${brainStatus.session_id}/send`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to check progress', 'error')
    } finally {
      setActionPending(false)
    }
  }

  // Open or toggle interactive CLI
  const handleInteractiveCli = useCallback(async () => {
    if (!id || icliStarting) return
    if (icliActive) {
      // Already active — toggle minimize/restore
      setIcliMinimized(prev => !prev)
      return
    }
    setIcliStarting(true)
    try {
      for (let attempt = 0; ; attempt++) {
        try {
          await api(`/api/sessions/${id}/interactive-cli`, {
            method: 'POST',
            body: JSON.stringify({}),
          })
          setIcliActiveLocal(true)
          setIcliMinimized(false)
          return
        } catch (e) {
          // 503 = RWS still connecting — wait and retry silently
          if (e instanceof ApiError && e.status === 503 && attempt < 30) {
            await new Promise(r => setTimeout(r, 2000))
            continue
          }
          notify(e instanceof Error ? e.message : 'Failed to open interactive CLI', 'error')
          return
        }
      }
    } finally {
      setIcliStarting(false)
    }
  }, [id, icliActive, icliStarting, notify])

  // Open or toggle browser view
  const handleBrowserView = useCallback(async () => {
    if (!id || bvStarting) return
    if (bvActive) {
      // Already active — toggle minimize/restore
      setBvMinimized(prev => !prev)
      return
    }
    setBvStarting(true)
    try {
      await api(`/api/sessions/${id}/browser-view`, {
        method: 'POST',
        body: JSON.stringify({ cdp_port: 9222 }),
      })
      setBvActiveLocal(true)
      setBvMinimized(false)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to start browser view', 'error')
    } finally {
      setBvStarting(false)
    }
  }, [id, bvActive, bvStarting, notify])

  // Handle long text paste from Cmd+V in terminal — uses bracketed paste so
  // Claude Code shows the compact "[xx lines of text]" indicator.
  const handleTextPaste = useCallback(async (text: string) => {
    if (!id) return
    try {
      await api(`/api/sessions/${id}/paste-to-pane`, {
        method: 'POST',
        body: JSON.stringify({ text }),
      })
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to paste text', 'error')
    }
  }, [id, notify])

  // Handle image paste from Cmd+V in terminal (no permission popup)
  const handleImagePaste = useCallback(async (file: File) => {
    if (!id) return
    const base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve((reader.result as string).split(',')[1])
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
    try {
      const res = await api<{ ok: boolean; file_path: string; filename: string }>(
        `/api/sessions/${id}/paste-image`,
        { method: 'POST', body: JSON.stringify({ image_data: base64 }) },
      )
      if (res.ok) {
        await api(`/api/sessions/${id}/type`, {
          method: 'POST',
          body: JSON.stringify({ text: res.file_path }),
        })
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to paste image', 'error')
    }
  }, [id, notify])

  // Upload a dropped file to the worker's tmp dir, then type the path into the terminal
  const uploadDroppedFile = useCallback(async (file: File) => {
    if (!id || dropUploading) return
    setDropUploading(true)
    try {
      const data = await fileToBase64(file)
      const res = await api<{ ok: boolean; file_path: string; filename: string }>(
        `/api/sessions/${id}/upload-file`,
        { method: 'POST', body: JSON.stringify({ file_data: data, filename: file.name }) },
      )
      if (res.ok) {
        await api(`/api/sessions/${id}/type`, {
          method: 'POST',
          body: JSON.stringify({ text: res.file_path }),
        })
      }
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 413) notify('File too large to upload', 'error')
        else if (e.status === 415) notify('Unsupported file type', 'error')
        else notify(e.message, 'error')
      } else {
        notify(e instanceof Error ? e.message : 'Failed to upload file', 'error')
      }
    } finally {
      setDropUploading(false)
      setShowLargeFileModal(false)
      setDropFile(null)
    }
  }, [id, dropUploading, notify])

  // Handle file drop from Finder (non-image files)
  const handleFileDrop = useCallback((file: File) => {
    if (dropUploading || pasting || ctxPasting) {
      notify('Another paste is in progress', 'warning')
      return
    }
    if (!isSupportedDropFile(file.name)) {
      notify(`Unsupported file type: ${file.name}`, 'warning')
      return
    }
    if (file.size > LARGE_FILE_THRESHOLD) {
      setDropFile(file)
      setShowLargeFileModal(true)
      return
    }
    uploadDroppedFile(file)
  }, [dropUploading, pasting, ctxPasting, notify, uploadDroppedFile])

  const handlePaste = useCallback(async () => {
    if (!id || pasting) return
    setPasting(true)
    try {
      const result = await readClipboard()
      if (result.type === 'image') {
        const res = await api<{ ok: boolean; file_path: string; filename: string }>(
          `/api/sessions/${id}/paste-image`,
          { method: 'POST', body: JSON.stringify({ image_data: result.imageData }) },
        )
        if (res.ok) {
          await api(`/api/sessions/${id}/type`, {
            method: 'POST',
            body: JSON.stringify({ text: res.file_path }),
          })
        }
      } else if (result.text && result.text.length > 1000) {
        // Long text: bracketed paste so Claude Code shows "[xx lines of text]"
        await api(`/api/sessions/${id}/paste-to-pane`, {
          method: 'POST',
          body: JSON.stringify({ text: result.text }),
        })
      } else {
        await api(`/api/sessions/${id}/send`, {
          method: 'POST',
          body: JSON.stringify({ message: result.text }),
        })
      }
    } catch (e) {
      if (e instanceof Error && e.name === 'NotAllowedError') {
        notify('Clipboard access denied. Please allow clipboard permissions.', 'error')
      } else {
        notify(e instanceof Error ? e.message : 'Failed to paste', 'error')
      }
    } finally {
      setPasting(false)
      terminalFocusRef.current?.()
    }
  }, [id, pasting, readClipboard, notify])

  if (error) {
    return (
      <div className="error-page">
        <p>{error}</p>
        <button className="btn btn-secondary" onClick={() => navigate('/')}>Back to Dashboard</button>
      </div>
    )
  }

  if (!session) {
    return <p className="empty-state">Loading session...</p>
  }

  return (
    <div className="session-detail">
      {/* Top bar with session info */}
      <div className="sd-topbar">
        <div className="sd-topbar-left">
          <h2
            className="sd-title"
            title="Click to copy"
            style={{ cursor: 'pointer' }}
            onClick={() => {
              navigator.clipboard.writeText(session.name)
                .then(() => notify('Copied worker name', 'success'))
                .catch(() => notify('Failed to copy', 'error'))
            }}
          >{session.name}</h2>
          <ProviderBadge provider={session.provider} />
          {session.host.includes('/') && <span className="sd-type-tag rdev">rdev</span>}
          {isSsh && <span className="sd-type-tag ssh">ssh</span>}
          <span className={`status-badge ${session.status}`}>{session.status}</span>
          {/* Check Status button next to status */}
          <button
            className="sd-check-btn"
            onClick={handleHealthCheck}
            disabled={actionPending}
            title="Check if worker is alive"
          >
            <IconSync size={14} />
          </button>
        </div>
        <div className="sd-topbar-actions">

          {/* Control buttons - icon only */}
          {session.status === 'disconnected' ? (
            /* Reconnect button for disconnected workers */
            <button
              className="sd-control-btn reconnect"
              onClick={handleReconnect}
              disabled={actionPending}
              title="Reconnect"
            >
              <IconRefresh size={16} />
            </button>
          ) : (
            <>
              {/* Check Progress button - always visible */}
              <button
                className="sd-control-btn check-progress"
                onClick={handleCheckProgress}
                disabled={actionPending || session.status === 'idle'}
                title="Check Progress"
              >
                <IconBrain size={16} />
              </button>
              <button
                className={`sd-control-btn ${session.status === 'paused' ? 'continue' : 'pause'}`}
                onClick={handlePauseOrContinue}
                disabled={actionPending || session.status === 'idle'}
                title={session.status === 'paused' ? 'Continue' : 'Pause'}
              >
                {session.status === 'paused' ? <IconPlay size={16} /> : <IconPause size={16} />}
              </button>
              <ConfirmPopover
                message={`Stop worker "${session.name}" and clear context?`}
                confirmLabel="Stop"
                onConfirm={handleStop}
                variant="danger"
              >
                {({ onClick }) => (
                  <button
                    className="sd-control-btn stop"
                    onClick={onClick}
                    disabled={actionPending || session.status === 'idle'}
                    title="Stop and clear"
                  >
                    <IconStop size={16} />
                  </button>
                )}
              </ConfirmPopover>
            </>
          )}

          {/* Remove button */}
          <ConfirmPopover
            message={`Remove worker "${session.name}"?`}
            confirmLabel="Remove"
            onConfirm={handleDelete}
            variant="danger"
          >
            {({ onClick }) => (
              <button
                className="sd-control-btn remove"
                data-testid="delete-session-btn"
                onClick={onClick}
                disabled={actionPending}
                title="Remove worker"
              >
                <IconTrash size={16} />
              </button>
            )}
          </ConfirmPopover>
        </div>
      </div>

      {/* Main content area: file explorer + viewer + terminal */}
      <div className={`fe-content-area ${fe.open ? 'fe-content-area--open' : ''}`}>
        {/* File explorer panel (left) */}
        {id && (
          <FileExplorerPanel
            sessionId={id}
            workDir={session.work_dir || null}
            isOpen={fe.open}
            width={fe.panelWidth}
            onWidthChange={fe.updateWidth}
            onFileSelect={handleFileSelect}
            onFileDoubleClick={handleFileDoubleClick}
            onNewFile={handleNewFile}
            selectedFile={editorTabs.activeTabPath}
            showIgnored={fe.showIgnored}
            onToggleIgnored={fe.toggleShowIgnored}
            onFileDeleted={handleFileDeleted}
            onFileRenamed={handleFileRenamed}
            isRemote={isRemote}
            onConnectingChange={setFeConnecting}
          />
        )}

        {/* Right pane: viewer (top) + terminal (bottom) */}
        <div className="fe-right-pane">
          {/* File viewer */}
          {fe.open && editorTabs.tabs.length > 0 && id && (
            <>
              <div className="fe-viewer-area" style={{ height: `${fe.viewerHeightRatio * 100}%` }}>
                <FileViewer
                  sessionId={id}
                  tabs={editorTabs.tabs}
                  activeTabPath={editorTabs.activeTabPath}
                  pendingClose={editorTabs.pendingClose}
                  saveConflict={editorTabs.saveConflict}
                  onTabSelect={editorTabs.setActiveTab}
                  onTabClose={editorTabs.closeTab}
                  onTabPin={editorTabs.pinTab}
                  onConfirmClose={editorTabs.confirmCloseTab}
                  onCancelClose={editorTabs.cancelCloseTab}
                  onContentChange={editorTabs.updateContent}
                  onSave={editorTabs.saveTab}
                  onResolveSaveConflict={editorTabs.resolveSaveConflict}
                  onReloadTab={editorTabs.reloadTab}
                  onDismissExternalChange={editorTabs.dismissExternalChange}
                  isDirty={editorTabs.isDirty}
                />
              </div>
              <div className="fe-viewer-resize" onMouseDown={handleViewerResizeStart} />
            </>
          )}

          {/* Terminal */}
          <div className="sd-terminal-area">
            {fe.open && editorTabs.tabs.length > 0 && (
              <div className="sd-terminal-header">TERMINAL</div>
            )}
            <TerminalView sessionId={session.id} sessionStatus={session.status} reconnectStep={session.reconnect_step} onFocusRef={(fn) => { terminalFocusRef.current = fn; requestAnimationFrame(() => fn()) }} onImagePaste={handleImagePaste} onTextPaste={handleTextPaste} onFileDrop={handleFileDrop} onPastingChange={setCtxPasting} onReconnect={handleReconnect} />
          </div>

          {/* Interactive CLI overlay — inside right pane so it follows terminal position */}
          {icliActive && id && (
            <InteractiveCLI
              sessionId={id}
              minimized={icliMinimized}
              onMinimizedChange={(min) => {
                setIcliMinimized(min)
                if (min) terminalFocusRef.current?.()
              }}
              onClose={() => {
                setIcliActiveLocal(false)
                setIcliMinimized(false)
                if (id) closeInteractiveCli(id)
                terminalFocusRef.current?.()
              }}
            />
          )}

          {/* Browser View overlay — CDP screencast for remote browser */}
          {bvActive && id && (
            <BrowserView
              sessionId={id}
              minimized={bvMinimized}
              isRemote={isRemote}
              onMinimizedChange={(min) => {
                setBvMinimized(min)
                if (min) terminalFocusRef.current?.()
              }}
              onClose={() => {
                setBvActiveLocal(false)
                setBvMinimized(false)
                if (id) closeBrowserView(id)
                terminalFocusRef.current?.()
              }}
            />
          )}
        </div>
      </div>

      {/* Footer with task link */}
      <div className="sd-footer">
        <div className="sd-footer-left">
          {tasks.length > 0 ? (
            <Link
              to={`/tasks/${tasks[0].id}`}
              className="sd-task-badge"
              title={tasks[0].title}
            >
              <span className="sd-task-key">{tasks[0].task_key}</span>
              <span className="sd-task-title">{tasks[0].title}</span>
            </Link>
          ) : (
            <button className="sd-assign-task-btn" onClick={() => setShowAssignTask(true)}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="16" /><line x1="8" y1="12" x2="16" y2="12" />
              </svg>
              Assign task
            </button>
          )}
          {Object.keys(tunnels).length > 0 && (
            <div className="sd-tunnels">
              {Object.entries(tunnels).map(([port]) => (
                <a
                  key={port}
                  href={`http://localhost:${port}`}
                  className="sd-tunnel-badge"
                  onClick={e => { e.stopPropagation() }}
                  title={`Port forwarding: localhost:${port} → rdev:${port}`}
                >
                  :{port}
                </a>
              ))}
            </div>
          )}
        </div>
        <div className="sd-footer-right">
          <label className="sd-auto-reconnect-toggle" title="When enabled, automatically reconnect this worker if it disconnects">
            <span className="sd-auto-reconnect-label">Auto-reconnect</span>
            <button
              className={`sd-toggle-switch ${session.auto_reconnect ? 'on' : ''}`}
              onClick={handleToggleAutoReconnect}
              role="switch"
              aria-checked={session.auto_reconnect}
            >
              <span className="sd-toggle-knob" />
            </button>
          </label>
          <div className="sd-panel-toggles">
            {(session.work_dir || isRemote) && (
              <button
                className={`sd-panel-btn${fe.open ? ' active open' : ''}${feConnecting && fe.open ? ' starting' : ''}`}
                onClick={fe.toggleOpen}
                title="Toggle file explorer (Ctrl+Shift+E)"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
                <span>Files</span>
              </button>
            )}
            <button
              className={`sd-panel-btn sd-panel-btn--terminal${icliActive ? (icliMinimized ? ' active' : ' active open') : ''}${icliStarting ? ' starting' : ''}`}
              onClick={handleInteractiveCli}
              disabled={icliStarting}
              title={icliStarting ? 'Starting interactive CLI...' : icliActive ? (icliMinimized ? 'Restore interactive CLI' : 'Minimize interactive CLI') : 'Open interactive CLI'}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" />
              </svg>
              <span>Terminal</span>
            </button>
            <button
              className={`sd-panel-btn sd-panel-btn--browser${bvActive ? (bvMinimized ? ' active' : ' active open') : ''}${bvStarting ? ' starting' : ''}`}
              onClick={handleBrowserView}
              disabled={bvStarting}
              title={bvStarting ? 'Starting browser view...' : bvActive ? (bvMinimized ? 'Restore browser view' : 'Minimize browser view') : 'View browser'}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
              </svg>
              <span>Browser</span>
            </button>
          </div>
          <button
            className={`sd-paste-btn${pasting || ctxPasting || dropUploading ? ' pasting' : ''}`}
            onClick={handlePaste}
            disabled={pasting || ctxPasting || dropUploading}
            title={pasting || ctxPasting || dropUploading ? 'Pasting...' : 'Paste clipboard to terminal'}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
              <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
            </svg>
            <span>Paste</span>
          </button>
        </div>
      </div>
      {id && session && (
        <AssignTaskModal
          open={showAssignTask}
          onClose={() => setShowAssignTask(false)}
          sessionId={id}
          sessionName={session.name}
        />
      )}
      <Modal
        open={showLargeFileModal}
        onClose={() => { setShowLargeFileModal(false); setDropFile(null) }}
        title="Large File Upload"
      >
        <div className="modal-body" style={{ padding: '16px 20px' }}>
          <p style={{ margin: 0, color: 'var(--text-secondary)' }}>
            <strong>{dropFile?.name}</strong> is {dropFile ? `${(dropFile.size / 1024 / 1024).toFixed(1)} MB` : ''}.
            Large files may take a moment to upload.
          </p>
        </div>
        <div className="modal-footer" style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 20px' }}>
          <button className="btn btn-secondary btn-sm" onClick={() => { setShowLargeFileModal(false); setDropFile(null) }}>Cancel</button>
          <button className="btn btn-primary btn-sm" disabled={dropUploading} onClick={() => { if (dropFile) uploadDroppedFile(dropFile) }}>
            {dropUploading ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </Modal>
    </div>
  )
}
