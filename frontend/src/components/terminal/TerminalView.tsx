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
  const [isFocused, setIsFocused] = useState(false)

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
      scrollback: 0,  // Disable scrollback - keep it simple
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
      // Send initial size after a brief delay so fit has completed
      setTimeout(() => {
        ws.send(JSON.stringify({
          type: 'resize',
          cols: terminal.cols,
          rows: terminal.rows,
        }))
      }, 100)
    }

    ws.onerror = () => ws.close()

    // Track last content for smart diffing
    let lastContent = ''

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'output') {
          // Only update if content actually changed
          if (msg.data !== lastContent) {
            lastContent = msg.data
            
            // Trim content to terminal rows only if needed
            let content = msg.data
            const lineCount = (content.match(/\n/g) || []).length + 1
            if (lineCount > terminal.rows) {
              // Find the nth newline and slice there
              let idx = 0
              for (let i = 0; i < terminal.rows && idx < content.length; i++) {
                const next = content.indexOf('\n', idx)
                if (next === -1) break
                idx = next + 1
              }
              content = content.slice(0, idx > 0 ? idx - 1 : content.length)
            }
            
            // Single write: home cursor, clear screen, content, then position cursor
            let output = '\x1b[H\x1b[J' + content
            if (typeof msg.cursorX === 'number' && typeof msg.cursorY === 'number') {
              output += `\x1b[${msg.cursorY + 1};${msg.cursorX + 1}H`
            }
            terminal.write(output)
          }
        } else if (msg.type === 'error') {
          terminal.write(`\r\n\x1b[31m${msg.message}\x1b[0m\r\n`)
        }
      } catch {
        terminal.write(event.data)
      }
    }

    // Send keystrokes with local echo for immediate feedback
    terminal.onData(data => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
      onUserInput?.()
    })

    // Track focus state
    const textarea = termRef.current.querySelector('textarea')
    const handleFocus = () => setIsFocused(true)
    const handleBlur = () => setIsFocused(false)
    if (textarea) {
      textarea.addEventListener('focus', handleFocus)
      textarea.addEventListener('blur', handleBlur)
    }

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
      if (textarea) {
        textarea.removeEventListener('focus', handleFocus)
        textarea.removeEventListener('blur', handleBlur)
      }
      ws.close()
      terminal.dispose()
    }
  }, [sessionId])

  return (
    <div className={`terminal-container${isFocused ? ' terminal-focused' : ''}`}>
      <div className="terminal-view" ref={termRef} data-testid="terminal-view" />
    </div>
  )
}
