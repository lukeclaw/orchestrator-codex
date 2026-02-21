import { useEffect, useRef, useState, useCallback } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import './TerminalView.css'

interface Props {
  sessionId: string
  sessionStatus?: string  // Session status from parent (e.g., 'connecting', 'working')
  disableScrollback?: boolean  // Disable scrollback history (for rdev sessions with screen)
  onInputRef?: (fn: (text: string) => void) => void  // Expose function to inject text into terminal
  onImagePaste?: (file: File) => void  // Handle image paste from Cmd+V
  onTextPaste?: (text: string) => void  // Handle long text paste from Cmd+V
}

type ConnectionState = 'connected' | 'disconnected' | 'reconnecting'

// Reconnection backoff: 1s, 2s, 5s, 10s, 10s (max 5 attempts)
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 10000]
const MAX_RECONNECT_ATTEMPTS = 5

export default function TerminalView({ sessionId, sessionStatus, disableScrollback, onInputRef, onImagePaste, onTextPaste }: Props) {
  const termRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptRef = useRef(0)
  
  const onImagePasteRef = useRef(onImagePaste)
  onImagePasteRef.current = onImagePaste
  const onTextPasteRef = useRef(onTextPaste)
  onTextPasteRef.current = onTextPaste

  const [isFocused, setIsFocused] = useState(false)
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')
  const [reconnectCountdown, setReconnectCountdown] = useState<number | null>(null)
  
  // Terminal is locked when session is in 'connecting' state (background op in progress)
  const isLocked = sessionStatus === 'connecting'

  // Create WebSocket connection with reconnection support
  const connectWebSocket = useCallback((terminal: Terminal) => {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/terminal/${sessionId}`)
    ws.binaryType = 'arraybuffer'  // receive binary frames as ArrayBuffer, not Blob
    wsRef.current = ws

    // Track scroll state and divergence detection
    let userScrolledUp = false
    let lastSyncHash: number | null = null  // CRC32 from last sync message

    // Track when user scrolls up to pause live updates
    const scrollDisposable = terminal.onScroll(() => {
      const buffer = terminal.buffer.active
      userScrolledUp = buffer.viewportY < buffer.baseY
    })

    ws.onopen = () => {
      setConnectionState('connected')
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

    ws.onclose = () => {
      scrollDisposable.dispose()
      
      // Don't reconnect if component is unmounting or max attempts reached
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
      const countdownInterval = setInterval(() => {
        countdown--
        if (countdown > 0) {
          setReconnectCountdown(countdown)
        } else {
          clearInterval(countdownInterval)
        }
      }, 1000)

      reconnectTimerRef.current = setTimeout(() => {
        clearInterval(countdownInterval)
        reconnectAttemptRef.current++
        connectWebSocket(terminal)
      }, delay)
    }

    ws.onmessage = (event) => {
      // Binary frames = raw PTY stream bytes (high-frequency path)
      if (event.data instanceof ArrayBuffer) {
        // Write to terminal unless user is scrolled up reviewing history
        if (!userScrolledUp) {
          const bytes = new Uint8Array(event.data)
          terminal.write(bytes)
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
        } else if (msg.type === 'error') {
          terminal.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`)
        }
      } catch {
        // Fallback: write raw text if JSON parse fails
        terminal.write(event.data)
      }
    }

    return ws
  }, [sessionId])

  useEffect(() => {
    if (!termRef.current) return

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
      // Use backend endpoint to open in system browser (window.open doesn't
      // work reliably inside a Tauri webview)
      fetch('/api/open-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: uri }),
      }).catch(() => {
        // Fallback: try window.open in case we're running outside the app
        window.open(uri, '_blank', 'noopener')
      })
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

    // Send keystrokes - block if disconnected or locked
    const inputDisposable = terminal.onData(data => {
      const ws = wsRef.current
      if (ws?.readyState === WebSocket.OPEN) {
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

    // Track focus state
    const textarea = termRef.current.querySelector('textarea')
    const handleFocus = () => setIsFocused(true)
    const handleBlur = () => setIsFocused(false)
    if (textarea) {
      textarea.addEventListener('focus', handleFocus)
      textarea.addEventListener('blur', handleBlur)
    }

    // Intercept Cmd+V paste on the xterm textarea for images and long text
    const handlePaste = (e: ClipboardEvent) => {
      // Check for image files first
      const files = e.clipboardData?.files
      if (files && files.length > 0) {
        const imageFile = Array.from(files).find(f => f.type.startsWith('image/'))
        if (imageFile) {
          e.preventDefault()
          e.stopPropagation()
          onImagePasteRef.current?.(imageFile)
          return
        }
      }
      // Check for long text (>1000 chars) — save to file instead of dumping into terminal
      const text = e.clipboardData?.getData('text/plain')
      if (text && text.length > 1000 && onTextPasteRef.current) {
        e.preventDefault()
        e.stopPropagation()
        onTextPasteRef.current(text)
        return
      }
      // Short text: let xterm handle natively
    }
    if (textarea) {
      textarea.addEventListener('paste', handlePaste)
    }

    // Block mouse wheel scroll when scrollback is disabled (rdev + screen)
    const wheelHandler = disableScrollback
      ? (e: WheelEvent) => { e.preventDefault(); e.stopPropagation() }
      : null
    if (wheelHandler && termRef.current) {
      termRef.current.addEventListener('wheel', wheelHandler, { passive: false })
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
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
      reconnectAttemptRef.current = MAX_RECONNECT_ATTEMPTS // Prevent reconnect on unmount
      
      clearTimeout(resizeTimeout)
      observer.disconnect()
      inputDisposable.dispose()
      if (textarea) {
        textarea.removeEventListener('focus', handleFocus)
        textarea.removeEventListener('blur', handleBlur)
        textarea.removeEventListener('paste', handlePaste)
      }
      if (wheelHandler && termRef.current) {
        termRef.current.removeEventListener('wheel', wheelHandler)
      }
      wsRef.current?.close()
      terminal.dispose()
    }
  }, [sessionId, connectWebSocket])

  // Manual retry handler
  const handleRetry = useCallback(() => {
    if (terminalRef.current) {
      reconnectAttemptRef.current = 0
      setConnectionState('reconnecting')
      connectWebSocket(terminalRef.current)
    }
  }, [connectWebSocket])

  // Determine overlay state
  const showOverlay = connectionState !== 'connected' || isLocked
  const overlayMessage = isLocked
    ? 'Setting up connection...'
    : connectionState === 'reconnecting'
    ? `Reconnecting${reconnectCountdown ? ` in ${reconnectCountdown}s` : '...'}`
    : connectionState === 'disconnected'
    ? 'Connection lost'
    : null

  // Build CSS classes
  const containerClasses = [
    'terminal-container',
    isFocused && 'terminal-focused',
    isLocked && 'terminal-locked',
    connectionState === 'disconnected' && 'terminal-disconnected',
    connectionState === 'reconnecting' && 'terminal-reconnecting',
  ].filter(Boolean).join(' ')

  return (
    <div className={containerClasses}>
      <div className={`terminal-view${disableScrollback ? ' no-scrollbar' : ''}`} ref={termRef} data-testid="terminal-view" />
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
    </div>
  )
}
