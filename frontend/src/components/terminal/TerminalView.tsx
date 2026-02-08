import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import './TerminalView.css'

interface Props {
  sessionId: string
  onUserInput?: () => void
}

export default function TerminalView({ sessionId, onUserInput }: Props) {
  const termRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')

  useEffect(() => {
    if (!termRef.current) return

    const terminal = new Terminal({
      convertEol: true,
      fontFamily: "'SF Mono', 'Menlo', 'Monaco', 'Consolas', monospace",
      fontSize: 13,
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
      scrollback: 5000,
    })

    const fitAddon = new FitAddon()
    terminal.loadAddon(fitAddon)
    terminal.open(termRef.current)

    // Delay first fit to allow DOM layout
    requestAnimationFrame(() => {
      fitAddon.fit()
    })

    terminalRef.current = terminal
    fitAddonRef.current = fitAddon

    // Connect WebSocket
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${location.host}/ws/terminal/${sessionId}`)
    wsRef.current = ws

    ws.onopen = () => {
      setStatus('connected')
      // Send initial size after a brief delay so fit has completed
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'resize',
          cols: terminal.cols,
          rows: terminal.rows,
        }))
      }, 100)
    }

    ws.onclose = () => setStatus('disconnected')
    ws.onerror = () => ws.close()

    let scrollbackWritten = false

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'scrollback') {
          // History from before we connected — write it so user can scroll up
          terminal.write(msg.data)
          scrollbackWritten = true
        } else if (msg.type === 'output') {
          if (!scrollbackWritten) {
            // No scrollback received yet — just write directly
            terminal.reset()
            terminal.write(msg.data)
          } else {
            // Overwrite the visible area without touching scrollback:
            // \x1b[H = cursor to home (1,1)
            // \x1b[J = erase from cursor to end of display
            terminal.write('\x1b[H\x1b[J' + msg.data)
          }
        } else if (msg.type === 'error') {
          terminal.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`)
        }
      } catch {
        terminal.write(event.data)
      }
    }

    // Send keystrokes
    terminal.onData(data => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
      onUserInput?.()
    })

    // Handle resize
    let resizeTimeout: ReturnType<typeof setTimeout>
    const observer = new ResizeObserver(() => {
      // Debounce resize to avoid flooding
      clearTimeout(resizeTimeout)
      resizeTimeout = setTimeout(() => {
        fitAddon.fit()
        if (ws.readyState === WebSocket.OPEN) {
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
      clearTimeout(resizeTimeout)
      observer.disconnect()
      ws.close()
      terminal.dispose()
    }
  }, [sessionId])

  return (
    <div className="terminal-container">
      <div className="terminal-toolbar">
        <span className={`terminal-status ${status}`} />
        <span className="terminal-label">Terminal</span>
      </div>
      <div className="terminal-view" ref={termRef} data-testid="terminal-view" />
    </div>
  )
}
