import { useState, useRef, useEffect, useCallback } from 'react'
import { api } from '../../api/client'
import './BrowserView.css'

interface Props {
  sessionId: string
  minimized?: boolean
  onMinimizedChange?: (minimized: boolean) => void
  onClose: () => void
}

function getModifiers(e: React.MouseEvent | React.KeyboardEvent): number {
  let m = 0
  if (e.altKey) m |= 1
  if (e.ctrlKey) m |= 2
  if (e.metaKey) m |= 4
  if (e.shiftKey) m |= 8
  return m
}

export default function BrowserView({ sessionId, minimized = false, onMinimizedChange, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [pageUrl, setPageUrl] = useState('')
  const [pageTitle, setPageTitle] = useState('')
  const [isExpanded, setIsExpanded] = useState(false)
  const [isFocused, setIsFocused] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const [quality, setQuality] = useState(60)
  const [zoom, setZoom] = useState(100)
  const zoomRef = useRef(100)
  const [aspectRatio, setAspectRatio] = useState(4 / 3)
  const aspectRatioRef = useRef(4 / 3)
  const [showSettings, setShowSettings] = useState(false)
  const settingsRef = useRef<HTMLDivElement>(null)
  const [urlInput, setUrlInput] = useState('')
  const [urlFocused, setUrlFocused] = useState(false)
  // Track actual frame dimensions for coordinate scaling
  const frameSizeRef = useRef({ width: 1280, height: 960 })

  const handleClose = async () => {
    try {
      await api(`/api/sessions/${sessionId}/browser-view`, { method: 'DELETE' })
    } catch {
      /* ignore — may already be closed */
    }
    onClose()
  }

  const handleMinimize = () => {
    onMinimizedChange?.(!minimized)
  }

  // Connect WebSocket on mount
  useEffect(() => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/browser-view/${sessionId}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      setError('')
    }

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        drawFrame(event.data)
      } else {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'navigate') {
            if (msg.url) setPageUrl(msg.url)
            if (msg.title) setPageTitle(msg.title)
          } else if (msg.type === 'metadata') {
            if (msg.url) setPageUrl(msg.url)
            if (msg.title) setPageTitle(msg.title)
            if (msg.viewport?.width && msg.viewport?.height) {
              const r = msg.viewport.width / msg.viewport.height
              aspectRatioRef.current = r
              setAspectRatio(r)
            }
          } else if (msg.type === 'error') {
            setError(msg.message || 'Unknown error')
          } else if (msg.type === 'closed') {
            setError(`Browser closed: ${msg.reason || 'unknown'}`)
            setConnected(false)
          }
        } catch {
          // Ignore malformed JSON
        }
      }
    }

    ws.onclose = () => {
      setConnected(false)
    }

    ws.onerror = () => {
      setError('WebSocket connection failed')
    }

    return () => {
      ws.close()
    }
  }, [sessionId])

  // Auto-focus canvas when connected
  useEffect(() => {
    if (connected && !minimized && canvasRef.current) {
      canvasRef.current.focus()
    }
  }, [connected, minimized])

  // Close settings dropdown on click outside
  useEffect(() => {
    if (!showSettings) return
    function handleClick(e: MouseEvent) {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowSettings(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [showSettings])

  // Draw JPEG frame on canvas using createImageBitmap for better performance
  const drawFrame = useCallback((jpegData: ArrayBuffer) => {
    const canvas = canvasRef.current
    if (!canvas) return

    const blob = new Blob([jpegData], { type: 'image/jpeg' })
    createImageBitmap(blob).then((bitmap) => {
      const ctx = canvas.getContext('2d')
      if (!ctx) return

      // Update canvas size to match frame (only if changed)
      if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
        canvas.width = bitmap.width
        canvas.height = bitmap.height
        frameSizeRef.current = { width: bitmap.width, height: bitmap.height }
        const r = bitmap.width / bitmap.height
        if (Math.abs(r - aspectRatioRef.current) > 0.01) {
          aspectRatioRef.current = r
          setAspectRatio(r)
        }
      }
      ctx.drawImage(bitmap, 0, 0)
      bitmap.close()
    }).catch(() => {
      // Fallback to Image() for browsers that don't support createImageBitmap
      const url = URL.createObjectURL(blob)
      const img = new Image()
      img.onload = () => {
        const ctx = canvas.getContext('2d')
        if (ctx) {
          if (canvas.width !== img.width || canvas.height !== img.height) {
            canvas.width = img.width
            canvas.height = img.height
            frameSizeRef.current = { width: img.width, height: img.height }
            const r = img.width / img.height
            if (Math.abs(r - aspectRatioRef.current) > 0.01) {
              aspectRatioRef.current = r
              setAspectRatio(r)
            }
          }
          ctx.drawImage(img, 0, 0)
        }
        URL.revokeObjectURL(url)
      }
      img.onerror = () => URL.revokeObjectURL(url)
      img.src = url
    })
  }, [])

  // Scale canvas display coordinates to browser viewport coordinates
  // When zoomed out (e.g. 50%), the virtual viewport is larger than the frame,
  // so frame coords must be scaled up by 100/zoom to get viewport coords.
  const scaleCoords = useCallback((e: React.MouseEvent): { x: number; y: number } => {
    const canvas = canvasRef.current
    if (!canvas) return { x: 0, y: 0 }

    const rect = canvas.getBoundingClientRect()
    const zoomScale = 100 / zoomRef.current
    const scaleX = (frameSizeRef.current.width / rect.width) * zoomScale
    const scaleY = (frameSizeRef.current.height / rect.height) * zoomScale
    return {
      x: Math.round((e.clientX - rect.left) * scaleX),
      y: Math.round((e.clientY - rect.top) * scaleY),
    }
  }, [])

  const sendMouseEvent = useCallback((e: React.MouseEvent, type: string) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    const { x, y } = scaleCoords(e)
    ws.send(JSON.stringify({
      type: 'mouse',
      event: type,
      x,
      y,
      button: ['left', 'middle', 'right'][e.button] || 'left',
      clickCount: e.detail || 1,
      modifiers: getModifiers(e),
    }))
  }, [scaleCoords])

  const sendKeyEvent = useCallback((e: React.KeyboardEvent, type: string) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    // Prevent browser defaults for forwarded keys
    e.preventDefault()

    ws.send(JSON.stringify({
      type: 'key',
      event: type,
      key: e.key,
      code: e.code,
      text: type === 'keyDown' && e.key.length === 1 ? e.key : '',
      modifiers: getModifiers(e),
    }))
  }, [])

  const sendScrollEvent = useCallback((e: React.WheelEvent) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    e.preventDefault()
    const { x, y } = scaleCoords(e)
    ws.send(JSON.stringify({
      type: 'scroll',
      x,
      y,
      deltaX: Math.round(e.deltaX),
      deltaY: Math.round(e.deltaY),
      modifiers: getModifiers(e),
    }))
  }, [scaleCoords])

  const handleZoomChange = useCallback((newZoom: number) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    zoomRef.current = newZoom
    setZoom(newZoom)
    ws.send(JSON.stringify({ type: 'zoom', zoom: newZoom }))
  }, [])

  const handleQualityChange = useCallback((newQuality: number) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    setQuality(newQuality)
    ws.send(JSON.stringify({ type: 'quality', quality: newQuality }))
  }, [])

  const handleGoBack = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'goBack' }))
  }, [])

  const handleGoForward = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'goForward' }))
  }, [])

  const handleNavigate = useCallback((url: string) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    let normalized = url.trim()
    if (!normalized) return
    // Add protocol if missing
    if (!/^https?:\/\//i.test(normalized)) {
      normalized = 'https://' + normalized
    }
    ws.send(JSON.stringify({ type: 'navigate', url: normalized }))
  }, [])

  // Sync pageUrl into urlInput when not focused
  useEffect(() => {
    if (!urlFocused && pageUrl) {
      setUrlInput(pageUrl)
    }
  }, [pageUrl, urlFocused])

  const classes = [
    'bv-overlay',
    isFocused && 'bv-focused',
    isExpanded && !minimized && 'bv-expanded',
    minimized && 'bv-minimized',
  ].filter(Boolean).join(' ')

  // Dynamic aspect-ratio sizing (not applied when minimized)
  const overlayStyle: React.CSSProperties = minimized ? {} : {
    aspectRatio: `${aspectRatio}`,
    '--bv-aspect': `${aspectRatio}`,
  } as React.CSSProperties

  return (
    <div
      className={classes}
      style={overlayStyle}
      tabIndex={-1}
      onFocus={() => setIsFocused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsFocused(false)
      }}
    >
      <div
        className="bv-titlebar"
        onClick={minimized ? handleMinimize : undefined}
        style={minimized ? { cursor: 'pointer' } : undefined}
      >
        <div className="bv-title-group">
          <span className={`bv-status-dot ${connected ? 'connected' : ''}`} />
          <span className="bv-title">Browser View</span>
          {minimized && pageUrl && (
            <span className="bv-url" title={pageUrl}>
              {pageTitle || new URL(pageUrl).hostname}
            </span>
          )}
        </div>
        <div className="bv-controls">
          <button
            className="bv-btn"
            onClick={handleMinimize}
            title={minimized ? 'Restore' : 'Minimize'}
          >
            {minimized ? (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 15 12 9 6 15" />
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            )}
          </button>
          {!minimized && (
            <button
              className="bv-btn"
              onClick={() => setIsExpanded(!isExpanded)}
              title={isExpanded ? 'Collapse' : 'Expand'}
            >
              {isExpanded ? (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="4 14 10 14 10 20" /><polyline points="20 10 14 10 14 4" /><line x1="14" y1="10" x2="21" y2="3" /><line x1="3" y1="21" x2="10" y2="14" />
                </svg>
              ) : (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" /><line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" />
                </svg>
              )}
            </button>
          )}
          <button className="bv-btn bv-close-btn" onClick={handleClose} title="Close">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
      {!minimized && connected && (
        <div className="bv-navbar">
          <button className="bv-nav-btn" onClick={handleGoBack} title="Back">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <button className="bv-nav-btn" onClick={handleGoForward} title="Forward">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
          <input
            className="bv-url-input"
            type="text"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onFocus={(e) => { setUrlFocused(true); e.target.select() }}
            onBlur={() => setUrlFocused(false)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleNavigate(urlInput)
                ;(e.target as HTMLInputElement).blur()
                canvasRef.current?.focus()
              }
              e.stopPropagation()
            }}
            placeholder="Enter URL..."
            spellCheck={false}
          />
          <div className="bv-settings-menu" ref={settingsRef}>
            <button
              className="bv-nav-btn"
              onClick={(e) => { e.stopPropagation(); setShowSettings(!showSettings) }}
              title="Settings"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <circle cx="12" cy="5" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="12" cy="19" r="2" />
              </svg>
            </button>
            {showSettings && (
              <div className="bv-settings-dropdown" onClick={(e) => e.stopPropagation()}>
                <div className="bv-settings-row">
                  <span>Zoom</span>
                  <div className="bv-stepper">
                    <button onClick={() => { const steps = [50,75,100,150]; const i = steps.indexOf(zoom); if (i > 0) handleZoomChange(steps[i-1]) }} disabled={zoom <= 50}>−</button>
                    <span className="bv-stepper-value">{zoom}%</span>
                    <button onClick={() => { const steps = [50,75,100,150]; const i = steps.indexOf(zoom); if (i < steps.length-1) handleZoomChange(steps[i+1]) }} disabled={zoom >= 150}>+</button>
                  </div>
                </div>
                <div className="bv-settings-row">
                  <span>Quality</span>
                  <div className="bv-stepper">
                    <button onClick={() => { const steps = [30,60,80,100]; const i = steps.indexOf(quality); if (i > 0) handleQualityChange(steps[i-1]) }} disabled={quality <= 30}>−</button>
                    <span className="bv-stepper-value">{quality === 30 ? 'Low' : quality === 60 ? 'Med' : quality === 80 ? 'High' : 'Max'}</span>
                    <button onClick={() => { const steps = [30,60,80,100]; const i = steps.indexOf(quality); if (i < steps.length-1) handleQualityChange(steps[i+1]) }} disabled={quality >= 100}>+</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      <div className="bv-canvas-container" style={minimized ? { display: 'none' } : undefined}>
        {error ? (
          <div className="bv-error">
            <span>{error}</span>
            <button className="bv-error-close" onClick={handleClose}>Close</button>
          </div>
        ) : !connected ? (
          <div className="bv-connecting">Connecting to browser...</div>
        ) : (
          <canvas
            ref={canvasRef}
            tabIndex={0}
            className="bv-canvas"
            onMouseDown={(e) => { e.preventDefault(); canvasRef.current?.focus(); sendMouseEvent(e, 'mousePressed') }}
            onMouseUp={(e) => sendMouseEvent(e, 'mouseReleased')}
            onMouseMove={(e) => sendMouseEvent(e, 'mouseMoved')}
            onKeyDown={(e) => sendKeyEvent(e, 'keyDown')}
            onKeyUp={(e) => sendKeyEvent(e, 'keyUp')}
            onWheel={(e) => sendScrollEvent(e)}
            onContextMenu={(e) => e.preventDefault()}
          />
        )}
      </div>
    </div>
  )
}
