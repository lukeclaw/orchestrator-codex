import { useState, useRef, useEffect } from 'react'
import { api } from '../../api/client'
import TerminalView from './TerminalView'
import './InteractiveCLI.css'

interface Props {
  sessionId: string
  minimized?: boolean
  onMinimizedChange?: (minimized: boolean) => void
  onClose: () => void
}

export default function InteractiveCLI({ sessionId, minimized = false, onMinimizedChange, onClose }: Props) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [isFocused, setIsFocused] = useState(false)
  const overlayRef = useRef<HTMLDivElement>(null)
  const termFocusRef = useRef<(() => void) | null>(null)

  const handleClose = async () => {
    try {
      await api(`/api/sessions/${sessionId}/interactive-cli`, { method: 'DELETE' })
    } catch {
      /* ignore — may already be closed */
    }
    onClose()
  }

  const handleMinimize = () => {
    onMinimizedChange?.(!minimized)
  }

  // Re-focus terminal when restoring from minimized
  useEffect(() => {
    if (!minimized && termFocusRef.current) {
      requestAnimationFrame(() => termFocusRef.current?.())
    }
  }, [minimized])

  const classes = [
    'icli-overlay',
    isFocused && 'icli-focused',
    isExpanded && !minimized && 'icli-expanded',
    minimized && 'icli-minimized',
  ].filter(Boolean).join(' ')

  return (
    <div
      ref={overlayRef}
      className={classes}
      tabIndex={-1}
      onFocus={() => {
        setIsFocused(true)
        // Always redirect focus to the terminal when not minimized
        if (!minimized && termFocusRef.current) {
          termFocusRef.current()
        }
      }}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setIsFocused(false)
      }}
    >
      <div
        className="icli-titlebar"
        onMouseDown={(e) => {
          if (!minimized) {
            e.preventDefault() // prevent text selection and default focus shift
            if (termFocusRef.current) termFocusRef.current()
            setIsFocused(true)
          }
        }}
        onClick={minimized ? handleMinimize : undefined}
        style={minimized ? { cursor: 'pointer' } : undefined}
      >
        <span className="icli-title">Interactive CLI</span>
        <div className="icli-controls">
          <button
            className="icli-btn"
            onClick={handleMinimize}
            title={minimized ? 'Restore' : 'Minimize'}
          >
            {minimized ? (
              /* chevron-up (restore) */
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 15 12 9 6 15" />
              </svg>
            ) : (
              /* minimize (line) */
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            )}
          </button>
          {!minimized && (
            <button
              className="icli-btn"
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
          <button className="icli-btn icli-close-btn" onClick={handleClose} title="Close">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
      <div className="icli-terminal" style={minimized ? { display: 'none' } : undefined}>
        <TerminalView
          sessionId={sessionId}
          wsPath={`/ws/terminal/${sessionId}/interactive`}
          sendPath={`/api/sessions/${sessionId}/interactive-cli/send`}
          onFocusRef={(fn) => {
            termFocusRef.current = fn
            // Auto-focus terminal on initial mount
            requestAnimationFrame(() => fn())
          }}
        />
      </div>
    </div>
  )
}
