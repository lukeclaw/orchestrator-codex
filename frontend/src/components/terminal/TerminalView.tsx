import { useEffect, useRef, useState, useCallback } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import { openUrl } from '../../api/client'
import '@xterm/xterm/css/xterm.css'
import './TerminalView.css'

interface Props {
  sessionId: string
  wsPath?: string  // Custom WebSocket path (default: /ws/terminal/{sessionId})
  sendPath?: string  // Custom REST send path (default: /api/sessions/{sessionId}/send)
  sessionStatus?: string  // Session status from parent (e.g., 'connecting', 'working')
  disableScrollback?: boolean  // Disable scrollback history (for rdev sessions with screen)
  onInputRef?: (fn: (text: string) => void) => void  // Expose function to inject text into terminal
  onFocusRef?: (fn: () => void) => void  // Expose function to focus the terminal
  onImagePaste?: (file: File) => void  // Handle image paste from Cmd+V
  onTextPaste?: (text: string) => void  // Handle long text paste from Cmd+V
  onPastingChange?: (pasting: boolean) => void  // Notify parent when context-menu paste is in progress
  onExit?: () => void  // Called when the underlying process exits (PTY closed)
}

type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'reconnecting'

// Reconnection backoff: 1s, 2s, 5s, 10s, 10s (max 5 attempts)
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 10000]
const MAX_RECONNECT_ATTEMPTS = 5

export default function TerminalView({ sessionId, wsPath, sendPath, sessionStatus, disableScrollback, onInputRef, onFocusRef, onImagePaste, onTextPaste, onPastingChange, onExit }: Props) {
  const termRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const reconnectAttemptRef = useRef(0)
  
  const onImagePasteRef = useRef(onImagePaste)
  onImagePasteRef.current = onImagePaste
  const onTextPasteRef = useRef(onTextPaste)
  onTextPasteRef.current = onTextPaste
  const onPastingChangeRef = useRef(onPastingChange)
  onPastingChangeRef.current = onPastingChange
  const onExitRef = useRef(onExit)
  onExitRef.current = onExit

  const [isFocused, setIsFocused] = useState(false)
  const [connectionState, setConnectionState] = useState<ConnectionState>('connecting')
  const [reconnectCountdown, setReconnectCountdown] = useState<number | null>(null)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null)
  // Once the terminal has received a successful WS connection, it's "ready" forever.
  // Before ready: show skeleton. After ready: show content (+ error overlay if disconnected).
  const [ready, setReady] = useState(false)

  // Terminal is locked when session is in 'connecting' state (background op in progress)
  const isLocked = sessionStatus === 'connecting'

  // Flag: set when the server reports the PTY process exited.
  // Prevents reconnection attempts — the process is gone, not just a network blip.
  const ptyExitedRef = useRef(false)

  // --- Typing latency tracker (component-level so both WS and onData can access) ---
  const latencyRef = useRef({
    lastInputTime: 0,
    lastInputData: '',
    samples: [] as number[],
  })

  const recordLatency = useCallback((ms: number) => {
    const state = latencyRef.current
    state.samples.push(ms)
    if (state.samples.length > 100) state.samples.shift()
    const sorted = [...state.samples].sort((a, b) => a - b);
    // Expose stats on window.__terminalLatency for manual inspection
    (window as any).__terminalLatency = {
      last: ms,
      avg: Math.round(sorted.reduce((a, b) => a + b, 0) / sorted.length),
      p50: sorted[Math.floor(sorted.length * 0.5)],
      p95: sorted[Math.floor(sorted.length * 0.95)],
      max: sorted[sorted.length - 1],
      min: sorted[0],
      count: sorted.length,
    }
  }, [])

  const cancelPendingReconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    if (countdownIntervalRef.current) {
      clearInterval(countdownIntervalRef.current)
      countdownIntervalRef.current = null
    }
  }, [])

  // Create WebSocket connection with reconnection support
  const connectWebSocket = useCallback((terminal: Terminal) => {
    cancelPendingReconnect()
    const old = wsRef.current
    if (old) {
      old.onclose = null   // prevent stale onclose from re-triggering
      old.onerror = null
      old.close()
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const path = wsPath || `/ws/terminal/${sessionId}`
    const ws = new WebSocket(`${proto}//${location.host}${path}`)
    ws.binaryType = 'arraybuffer'  // receive binary frames as ArrayBuffer, not Blob
    wsRef.current = ws

    // Track scroll state and divergence detection
    let userScrolledUp = false
    let lastSyncHash: number | null = null  // CRC32 from last sync message

    // Write batching: small frames (keystroke echoes) are written immediately
    // for low latency.  Larger frames are deferred to the next animation frame
    // so that screen-clearing sequences and their content are processed as one
    // atomic terminal.write(), preventing the "history flash" artifact.
    const pendingWrites: Uint8Array[] = []
    let writeRafId: number | null = null
    const IMMEDIATE_THRESHOLD = 128 // bytes — keystroke echoes are 1-10 bytes

    function flushPendingWrites() {
      writeRafId = null
      if (userScrolledUp || pendingWrites.length === 0) {
        pendingWrites.length = 0
        return
      }
      if (pendingWrites.length === 1) {
        terminal.write(pendingWrites[0])
      } else {
        const total = pendingWrites.reduce((n, b) => n + b.length, 0)
        const merged = new Uint8Array(total)
        let off = 0
        for (const b of pendingWrites) {
          merged.set(b, off)
          off += b.length
        }
        terminal.write(merged)
      }
      pendingWrites.length = 0
    }

    // Track when user scrolls up to pause live updates
    const scrollDisposable = terminal.onScroll(() => {
      const buffer = terminal.buffer.active
      userScrolledUp = buffer.viewportY < buffer.baseY
    })

    ws.onopen = () => {
      if (wsRef.current !== ws) return // stale WS (React Strict Mode cleanup)
      setConnectionState('connected')
      setReady(true)
      setReconnectCountdown(null)
      reconnectAttemptRef.current = 0

      // Send initial size after a brief delay so fit has completed
      setTimeout(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'resize',
            cols: terminal.cols,
            rows: terminal.rows,
          }))
        }
      }, 100)
    }

    ws.onerror = () => {
      // Will trigger onclose
    }

    ws.onclose = (event) => {
      scrollDisposable.dispose()
      if (writeRafId !== null) { cancelAnimationFrame(writeRafId); writeRafId = null }
      pendingWrites.length = 0

      // Ignore close events from stale WebSocket instances
      // (e.g., React Strict Mode unmounts WS #1, but WS #2 is already current)
      if (wsRef.current !== ws) return

      // PTY process exited — don't reconnect, notify parent
      if (ptyExitedRef.current) {
        setConnectionState('disconnected')
        return
      }

      // Close code 4004: "PTY not attached yet" — remote session reconnecting.
      // Use fixed 5s retries with NO attempt cap (session may take 60s+ to reconnect).
      if (event.code === 4004) {
        setConnectionState('reconnecting')
        const delay = 5000
        const delaySeconds = 5
        setReconnectCountdown(delaySeconds)

        let countdown = delaySeconds
        countdownIntervalRef.current = setInterval(() => {
          countdown--
          if (countdown > 0) {
            setReconnectCountdown(countdown)
          } else {
            if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)
            countdownIntervalRef.current = null
          }
        }, 1000)

        reconnectTimerRef.current = setTimeout(() => {
          if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)
          countdownIntervalRef.current = null
          // Do NOT increment reconnectAttemptRef — unlimited retries for 4004
          connectWebSocket(terminal)
        }, delay)
        return
      }

      // All other close codes: backoff with 5-attempt cap
      if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
        setConnectionState('disconnected')
        setReconnectCountdown(null)
        return
      }

      setConnectionState('reconnecting')

      // Calculate delay with backoff
      const delay = RECONNECT_DELAYS[Math.min(reconnectAttemptRef.current, RECONNECT_DELAYS.length - 1)]
      const delaySeconds = Math.ceil(delay / 1000)
      setReconnectCountdown(delaySeconds)

      // Countdown timer
      let countdown = delaySeconds
      countdownIntervalRef.current = setInterval(() => {
        countdown--
        if (countdown > 0) {
          setReconnectCountdown(countdown)
        } else {
          if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)
          countdownIntervalRef.current = null
        }
      }, 1000)

      reconnectTimerRef.current = setTimeout(() => {
        if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)
        countdownIntervalRef.current = null
        reconnectAttemptRef.current++
        connectWebSocket(terminal)
      }, delay)
    }

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return // stale WS

      // Binary frames = raw PTY stream bytes (high-frequency path)
      if (event.data instanceof ArrayBuffer) {
        // Measure typing latency: time from last input send to first echo
        if (latencyRef.current.lastInputTime > 0) {
          const now = performance.now()
          const latencyMs = Math.round(now - latencyRef.current.lastInputTime)
          latencyRef.current.lastInputTime = 0  // reset — only measure first echo after input
          recordLatency(latencyMs)
        }
        const bytes = new Uint8Array(event.data)
        if (bytes.length < IMMEDIATE_THRESHOLD && pendingWrites.length === 0) {
          // Small frame with nothing pending — write immediately (low latency
          // path for keystroke echoes).
          if (!userScrolledUp) {
            terminal.write(bytes)
          }
        } else {
          // Larger frame or data already pending — batch into next animation
          // frame so screen-clearing sequences and content are atomic.
          pendingWrites.push(bytes)
          if (writeRafId === null) {
            writeRafId = requestAnimationFrame(flushPendingWrites)
          }
        }
        // NOTE: No ACK needed — server uses snapshot recovery instead
        // of drop-based flow control.  Bytes are never dropped.
        return
      }

      // Text frames = JSON control messages
      try {
        const msg = JSON.parse(event.data)

        if (msg.type === 'history') {
          terminal.reset()
          if (msg.alternateScreen) {
            terminal.write('\x1b[?1049h')
          }
          // capture-pane output uses bare \n between lines — convert to
          // \r\n so xterm.js moves cursor to column 0 on each new line.
          terminal.write(msg.data.replace(/\n/g, '\r\n'))
          if (typeof msg.cursorX === 'number' && typeof msg.cursorY === 'number') {
            terminal.write(`\x1b[${msg.cursorY + 1};${msg.cursorX + 1}H`)
          }
          terminal.scrollToBottom()
          userScrolledUp = false
          if (typeof msg.hash === 'number') lastSyncHash = msg.hash
        } else if (msg.type === 'sync') {
          // Drift correction — ground truth pane capture from tmux.
          // Convert bare \n to \r\n (capture-pane uses Unix line endings).
          terminal.write('\x1b[H\x1b[J' + msg.data.replace(/\n/g, '\r\n'))
          if (typeof msg.cursorX === 'number' && typeof msg.cursorY === 'number') {
            terminal.write(`\x1b[${msg.cursorY + 1};${msg.cursorX + 1}H`)
          }
          if (typeof msg.hash === 'number') lastSyncHash = msg.hash
        } else if (msg.type === 'pty_exit') {
          // The underlying PTY process exited — suppress reconnection
          ptyExitedRef.current = true
          onExitRef.current?.()
        } else if (msg.type === 'error') {
          terminal.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`)
        }
      } catch {
        // Fallback: write raw text if JSON parse fails
        terminal.write(event.data)
      }
    }

    return ws
  }, [sessionId, wsPath, cancelPendingReconnect])

  useEffect(() => {
    if (!termRef.current) return

    // Reset reconnect counter — critical for React Strict Mode which
    // double-fires effects; the first cleanup sets this to MAX.
    reconnectAttemptRef.current = 0

    const terminal = new Terminal({
      // NOTE: convertEol is intentionally NOT set (defaults to false).
      // Raw PTY bytes from tmux use bare \n for line feed (cursor down,
      // same column).  convertEol would add \r, breaking TUI apps like
      // ink that rely on precise cursor positioning.  Sync/history text
      // messages convert \n → \r\n explicitly before writing.
      fontFamily: "'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace",
      fontSize: 12,
      lineHeight: 1.2,
      theme: {
        background: '#0d1117',
        foreground: '#e6edf3',
        cursor: '#58a6ff',
        cursorAccent: '#0d1117',
        selectionBackground: '#388bfd44',
        black: '#484f58',
        red: '#ff7b72',
        green: '#3fb950',
        yellow: '#d29922',
        blue: '#58a6ff',
        magenta: '#bc8cff',
        cyan: '#39d353',
        white: '#b1bac4',
        brightBlack: '#6e7681',
        brightRed: '#ffa198',
        brightGreen: '#56d364',
        brightYellow: '#e3b341',
        brightBlue: '#79c0ff',
        brightMagenta: '#d2a8ff',
        brightCyan: '#56d364',
        brightWhite: '#f0f6fc',
      },
      cursorBlink: true,
      allowProposedApi: true,
      scrollback: disableScrollback ? 0 : 1000,
    })

    const fitAddon = new FitAddon()
    const webLinksAddon = new WebLinksAddon((_event, uri) => {
      openUrl(uri)
    })
    terminal.loadAddon(fitAddon)
    terminal.loadAddon(webLinksAddon)
    terminal.open(termRef.current)

    requestAnimationFrame(() => {
      fitAddon.fit()
    })

    terminalRef.current = terminal
    fitAddonRef.current = fitAddon

    // Connect WebSocket
    connectWebSocket(terminal)

    // Flag: suppress onData sends while our custom paste handler is active.
    // Prevents double-paste if xterm somehow still receives the paste data.
    let pasteInProgress = false

    // Send keystrokes - block if disconnected or locked
    const inputDisposable = terminal.onData(data => {
      if (pasteInProgress) return  // Our paste handler already sent this text
      const ws = wsRef.current
      if (ws?.readyState === WebSocket.OPEN) {
        latencyRef.current.lastInputTime = performance.now()
        latencyRef.current.lastInputData = data
        ws.send(JSON.stringify({ type: 'input', data }))
      }
    })

    // Expose function to inject text into terminal (for clipboard image paste)
    if (onInputRef) {
      onInputRef((text: string) => {
        const ws = wsRef.current
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'input', data: text }))
        }
      })
    }

    // Expose function to focus the terminal
    if (onFocusRef) {
      onFocusRef(() => terminal.focus())
    }

    // Track focus state
    const textarea = termRef.current.querySelector('textarea')
    const handleFocus = () => setIsFocused(true)
    const handleBlur = () => setIsFocused(false)
    if (textarea) {
      textarea.addEventListener('focus', handleFocus)
      textarea.addEventListener('blur', handleBlur)
    }

    // Intercept Cmd+V paste on the xterm textarea — all paste types go through
    // our handler so we can track pasting state for the animation.
    // Only attach when paste callbacks are provided; otherwise let xterm handle
    // paste natively via its WebSocket (used by the interactive CLI terminal).
    const hasPasteHandlers = onImagePasteRef.current || onTextPasteRef.current || onPastingChangeRef.current

    // Defense layer 1: Block Cmd+V / Ctrl+V at the xterm keyboard level so
    // xterm never processes the keystroke.  The browser still triggers the
    // native paste action, which our capture-phase paste listener handles.
    if (hasPasteHandlers) {
      terminal.attachCustomKeyEventHandler((event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === 'v' && event.type === 'keydown') {
          return false  // tell xterm to ignore this key
        }
        return true
      })
    }

    // Defense layer 2: Capture-phase paste listener on the container div.
    // Fires BEFORE xterm's built-in paste handler on the textarea.
    // stopImmediatePropagation() prevents any other listeners (including
    // stale ones from HMR) from also processing the event.
    //
    // Use AbortController for reliable cleanup — guarantees the listener is
    // removed even if the function reference changes across HMR / StrictMode.
    const pasteAbort = new AbortController()
    if (hasPasteHandlers && termRef.current) {
      termRef.current.addEventListener('paste', (e: ClipboardEvent) => {
        // Check for image files first
        const files = e.clipboardData?.files
        if (files && files.length > 0) {
          const imageFile = Array.from(files).find(f => f.type.startsWith('image/'))
          if (imageFile) {
            e.preventDefault()
            e.stopImmediatePropagation()
            pasteInProgress = true
            onPastingChangeRef.current?.(true)
            Promise.resolve(onImagePasteRef.current?.(imageFile)).finally(() => {
              pasteInProgress = false
              onPastingChangeRef.current?.(false)
            })
            return
          }
        }
        const text = e.clipboardData?.getData('text/plain')
        if (!text) return
        // Intercept ALL text paste (short and long) so animation tracks delivery
        e.preventDefault()
        e.stopImmediatePropagation()
        pasteInProgress = true
        onPastingChangeRef.current?.(true)
        const trimmed = text.trim()
        let task: Promise<unknown>
        if (trimmed.length > 1000 && onTextPasteRef.current) {
          // Long text: delegate to parent handler (saves to file)
          task = Promise.resolve(onTextPasteRef.current(trimmed))
        } else {
          // Short text: type into terminal via REST API (no Enter — just inserts text)
          task = fetch(sendPath || `/api/sessions/${sessionId}/type`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: trimmed }),
          })
        }
        task.finally(() => {
          requestAnimationFrame(() => { pasteInProgress = false })
          onPastingChangeRef.current?.(false)
        })
      }, { capture: true, signal: pasteAbort.signal })
    }

    // Block mouse wheel scroll when scrollback is disabled (rdev + screen)
    const wheelAbort = new AbortController()
    if (disableScrollback && termRef.current) {
      termRef.current.addEventListener('wheel', (e: WheelEvent) => {
        e.preventDefault()
        e.stopPropagation()
      }, { passive: false, capture: true, signal: wheelAbort.signal })
    }

    // Handle resize
    let resizeTimeout: ReturnType<typeof setTimeout>
    const observer = new ResizeObserver(() => {
      clearTimeout(resizeTimeout)
      resizeTimeout = setTimeout(() => {
        fitAddon.fit()
        const ws = wsRef.current
        if (ws?.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'resize',
            cols: terminal.cols,
            rows: terminal.rows,
          }))
        }
      }, 50)
    })
    observer.observe(termRef.current)

    return () => {
      // Clear reconnect timer on unmount
      cancelPendingReconnect()
      reconnectAttemptRef.current = MAX_RECONNECT_ATTEMPTS // Prevent reconnect on unmount
      
      clearTimeout(resizeTimeout)
      observer.disconnect()
      inputDisposable.dispose()
      if (textarea) {
        textarea.removeEventListener('focus', handleFocus)
        textarea.removeEventListener('blur', handleBlur)
      }
      pasteAbort.abort()   // guaranteed to remove the paste listener
      wheelAbort.abort()   // guaranteed to remove the wheel listener
      wsRef.current?.close()
      terminal.dispose()
    }
  }, [sessionId, connectWebSocket])

  // Manual retry handler
  const handleRetry = useCallback(() => {
    if (terminalRef.current) {
      cancelPendingReconnect()
      reconnectAttemptRef.current = 0
      ptyExitedRef.current = false
      setConnectionState('reconnecting')
      connectWebSocket(terminalRef.current)
    }
  }, [connectWebSocket, cancelPendingReconnect])

  // Auto-reconnect when session transitions to an active state while the
  // terminal WebSocket is disconnected (e.g. worker reconnected after a
  // tunnel death that exhausted retries or triggered pty_exit).
  const prevStatusRef = useRef(sessionStatus)
  useEffect(() => {
    const prev = prevStatusRef.current
    prevStatusRef.current = sessionStatus
    const isActive = sessionStatus === 'working' || sessionStatus === 'waiting'
    const wasInactive = !prev || prev === 'disconnected' || prev === 'connecting' || prev === 'error' || prev === 'idle'
    if (isActive && wasInactive && connectionState !== 'connected' && terminalRef.current) {
      cancelPendingReconnect()
      ptyExitedRef.current = false
      reconnectAttemptRef.current = 0
      setConnectionState('reconnecting')
      // Clear old terminal content to avoid flash of stale buffer
      terminalRef.current.write('\x1b[2J\x1b[H')
      connectWebSocket(terminalRef.current)
    }
  }, [sessionStatus, connectionState, connectWebSocket, cancelPendingReconnect])

  // Before first successful connection: always show skeleton, never show errors.
  // After first connection: show content; overlay only if connection is lost.
  const showSkeleton = !ready
  const showOverlay = isLocked || (ready && connectionState !== 'connected')
  const overlayMessage = isLocked
    ? 'Setting up connection...'
    : connectionState === 'reconnecting'
    ? `Reconnecting${reconnectCountdown ? ` in ${reconnectCountdown}s` : '...'}`
    : 'Connection lost'

  // Build CSS classes
  const containerClasses = [
    'terminal-container',
    isFocused && 'terminal-focused',
    isLocked && 'terminal-locked',
    connectionState === 'connecting' && 'terminal-connecting',
    connectionState === 'disconnected' && 'terminal-disconnected',
    connectionState === 'reconnecting' && 'terminal-reconnecting',
  ].filter(Boolean).join(' ')

  // Right-click context menu handlers
  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    setContextMenu({ x: e.clientX, y: e.clientY })
  }, [])

  const closeContextMenu = useCallback(() => setContextMenu(null), [])

  const handleCopy = useCallback(async () => {
    const terminal = terminalRef.current
    if (terminal) {
      const selection = terminal.getSelection()
      if (selection) {
        await navigator.clipboard.writeText(selection)
      }
    }
    setContextMenu(null)
  }, [])

  const [ctxPasting, setCtxPasting] = useState(false)

  // Type text into terminal via REST API (no Enter — just inserts text).
  // Awaits round-trip so animation tracks actual delivery.
  const sendTextViaApi = useCallback(async (text: string) => {
    await fetch(sendPath || `/api/sessions/${sessionId}/type`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
  }, [sessionId, sendPath])

  // Write text directly to the terminal via WebSocket (no Enter appended).
  // Used by right-click paste in terminals without custom paste handlers (e.g., interactive CLI).
  const writeToWs = useCallback((text: string) => {
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'input', data: text }))
    }
  }, [])

  const handlePaste = useCallback(async () => {
    setContextMenu(null)
    setCtxPasting(true)
    onPastingChangeRef.current?.(true)
    const useRest = !!(onImagePasteRef.current || onTextPasteRef.current || onPastingChangeRef.current)
    try {
      // Read clipboard via backend (uses pbpaste/osascript natively, no browser permission popup)
      const res = await fetch('/api/clipboard')
      if (!res.ok) throw new Error('Backend clipboard read failed')
      const data: { text: string | null; image_base64: string | null } = await res.json()

      if (data.image_base64 && onImagePasteRef.current) {
        // Convert base64 to File and delegate to image paste handler
        const blob = await fetch(`data:image/png;base64,${data.image_base64}`).then(r => r.blob())
        await onImagePasteRef.current(new File([blob], 'clipboard.png', { type: 'image/png' }))
      } else if (data.text) {
        const trimmed = data.text.trim()
        if (trimmed.length > 1000 && onTextPasteRef.current) {
          // Long text: delegate to parent handler (saves to file)
          await onTextPasteRef.current(trimmed)
        } else if (trimmed) {
          if (useRest) {
            // Main terminal: send via REST API (awaits delivery, tracks animation)
            await sendTextViaApi(trimmed)
          } else {
            // Interactive CLI: write directly to WebSocket (no Enter appended)
            writeToWs(trimmed)
          }
        }
      }
    } catch {
      // Backend unavailable — try browser clipboard as last resort
      try {
        const text = await navigator.clipboard.readText()
        if (text) {
          if (useRest) {
            await sendTextViaApi(text)
          } else {
            writeToWs(text)
          }
        }
      } catch {
        // Clipboard read denied or empty
      }
    } finally {
      setCtxPasting(false)
      onPastingChangeRef.current?.(false)
    }
    terminalRef.current?.focus()
  }, [sendTextViaApi, writeToWs])

  return (
    <div className={containerClasses}>
      <div
        className={`terminal-view${disableScrollback ? ' no-scrollbar' : ''}`}
        ref={termRef}
        data-testid="terminal-view"
        onContextMenu={handleContextMenu}
      />
      {showSkeleton && (
        <div className="terminal-skeleton">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="terminal-skeleton-line" style={{ width: `${20 + ((i * 23) % 55)}%` }} />
          ))}
        </div>
      )}
      {showOverlay && (
        <div className="terminal-overlay">
          <div className="terminal-overlay-content">
            <span className="terminal-overlay-message">{overlayMessage}</span>
            {connectionState === 'disconnected' && (
              <button className="terminal-retry-btn" onClick={handleRetry}>
                Retry
              </button>
            )}
          </div>
        </div>
      )}
      {contextMenu && (
        <>
          <div className="terminal-context-backdrop" onClick={closeContextMenu} onContextMenu={e => { e.preventDefault(); closeContextMenu() }} />
          <div className="terminal-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }}>
            <button onClick={handleCopy} disabled={!terminalRef.current?.getSelection()}>Copy</button>
            <button onClick={handlePaste}>Paste</button>
          </div>
        </>
      )}
    </div>
  )
}
