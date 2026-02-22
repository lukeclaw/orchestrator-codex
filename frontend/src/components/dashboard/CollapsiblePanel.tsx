import { useState, useRef, useEffect, useCallback, type ReactNode } from 'react'
import './CollapsiblePanel.css'

interface Props {
  id: string
  title: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  defaultCollapsed?: boolean
  'data-testid'?: string
}

const STORAGE_KEY = 'dashboard-collapsed'

function loadCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
  } catch {
    return {}
  }
}

function saveCollapsed(state: Record<string, boolean>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
}

export default function CollapsiblePanel({
  id,
  title,
  actions,
  children,
  className,
  defaultCollapsed = false,
  ...rest
}: Props) {
  const [collapsed, setCollapsed] = useState(() => {
    const stored = loadCollapsed()
    return stored[id] ?? defaultCollapsed
  })
  const bodyRef = useRef<HTMLDivElement>(null)
  const [bodyHeight, setBodyHeight] = useState<number | undefined>(undefined)

  const measure = useCallback(() => {
    if (bodyRef.current) {
      setBodyHeight(bodyRef.current.scrollHeight)
    }
  }, [])

  // Measure on mount and when children change
  useEffect(() => {
    measure()
  }, [children, measure])

  // Also measure on resize
  useEffect(() => {
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [measure])

  const toggle = () => {
    // Re-measure before expanding so we have the correct target height
    if (collapsed && bodyRef.current) {
      setBodyHeight(bodyRef.current.scrollHeight)
    }
    setCollapsed(prev => {
      const next = !prev
      const state = loadCollapsed()
      state[id] = next
      saveCollapsed(state)
      return next
    })
  }

  const testId = rest['data-testid']

  return (
    <section
      className={`panel collapsible-panel${collapsed ? ' collapsed' : ''}${className ? ` ${className}` : ''}`}
      {...(testId ? { 'data-testid': testId } : {})}
    >
      <div className="panel-header collapsible-panel-header" onClick={toggle}>
        <div className="collapsible-panel-left">
          <button
            className="collapsible-chevron"
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand' : 'Collapse'}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M6.22 3.22a.75.75 0 0 1 1.06 0l4.25 4.25a.75.75 0 0 1 0 1.06l-4.25 4.25a.75.75 0 0 1-1.06-1.06L9.94 8 6.22 4.28a.75.75 0 0 1 0-1.06Z" />
            </svg>
          </button>
          {typeof title === 'string' ? <h2>{title}</h2> : title}
        </div>
        {actions && (
          <div className="collapsible-panel-actions" onClick={e => e.stopPropagation()}>
            {actions}
          </div>
        )}
      </div>
      <div
        ref={bodyRef}
        className="collapsible-panel-body"
        style={{
          maxHeight: collapsed ? 0 : bodyHeight,
        }}
      >
        {children}
      </div>
    </section>
  )
}
