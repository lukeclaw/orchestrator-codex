import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { api } from '../../api/client'
import Markdown from '../common/Markdown'
import { IconX, IconPin } from '../common/Icons'
import './FileViewer.css'

interface FileContentResponse {
  path: string
  content: string
  truncated: boolean
  total_lines: number | null
  size: number
  binary: boolean
  language: string | null
  modified: number | null
}

interface FileViewerProps {
  sessionId: string
  filePath: string | null
  isPinned: boolean
  onClose: () => void
  onPin: () => void
}

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.ico'])

function isImage(path: string): boolean {
  const ext = path.slice(path.lastIndexOf('.')).toLowerCase()
  return IMAGE_EXTENSIONS.has(ext)
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function timeAgo(epoch: number): string {
  const secs = Math.floor(Date.now() / 1000 - epoch)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  if (secs < 2592000) return `${Math.floor(secs / 86400)}d ago`
  return `${Math.floor(secs / 2592000)}mo ago`
}

export default function FileViewer({ sessionId, filePath, isPinned, onClose, onPin }: FileViewerProps) {
  const [content, setContent] = useState<FileContentResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showPreview, setShowPreview] = useState(true)
  const abortRef = useRef<AbortController | null>(null)

  const fetchContent = useCallback(async (path: string) => {
    // Abort previous request
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)

    try {
      const params = new URLSearchParams({ path })
      const data = await api<FileContentResponse>(
        `/api/sessions/${sessionId}/files/content?${params}`,
        { signal: controller.signal },
      )
      if (!controller.signal.aborted) {
        setContent(data)
        setLoading(false)
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : 'Failed to load file')
        setLoading(false)
      }
    }
  }, [sessionId])

  useEffect(() => {
    if (filePath) {
      fetchContent(filePath)
      // Default to preview for markdown files
      setShowPreview(filePath.endsWith('.md') || filePath.endsWith('.markdown'))
    } else {
      setContent(null)
    }
    return () => {
      abortRef.current?.abort()
    }
  }, [filePath, fetchContent])

  // Wrap source content in a fenced code block for syntax-highlighted rendering
  const sourceAsMarkdown = useMemo(() => {
    if (!content || content.binary) return ''
    const lang = content.language || ''
    return '```' + lang + '\n' + content.content + '\n```'
  }, [content])

  if (!filePath) {
    return (
      <div className="fe-viewer fe-viewer--empty">
        <span className="fe-viewer-placeholder">Select a file to view</span>
      </div>
    )
  }

  const fileName = filePath.split('/').pop() || filePath
  const isMarkdown = content?.language === 'markdown'

  return (
    <div className="fe-viewer">
      {/* Tab header */}
      <div className="fe-viewer__header">
        <span className={`fe-viewer__tab ${!isPinned ? 'fe-viewer__tab--preview' : ''}`}>
          {fileName}
          {content && (
            <span className="fe-viewer__size">
              {humanSize(content.size)}
              {content.modified != null && <> &middot; {timeAgo(content.modified)}</>}
            </span>
          )}
        </span>
        <div className="fe-viewer__actions">
          {isMarkdown && (
            <label className="fe-viewer__md-toggle" title="Toggle between source and preview">
              <span className={`fe-viewer__md-label ${!showPreview ? 'fe-viewer__md-label--active' : ''}`}>Source</span>
              <button
                className={`fe-viewer__md-switch ${showPreview ? 'on' : ''}`}
                onClick={() => setShowPreview(p => !p)}
                role="switch"
                aria-checked={showPreview}
              >
                <span className="fe-viewer__md-knob" />
              </button>
              <span className={`fe-viewer__md-label ${showPreview ? 'fe-viewer__md-label--active' : ''}`}>Preview</span>
            </label>
          )}
          <button className="fe-viewer__pin" onClick={onPin} title={isPinned ? 'Unpin' : 'Pin'}>
            <IconPin size={14} />
          </button>
          <button className="fe-viewer__close" onClick={onClose} title="Close">
            <IconX size={14} />
          </button>
        </div>
      </div>

      {/* Content area */}
      <div className="fe-viewer__content">
        {loading && (
          <div className="fe-viewer__skeleton">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="fe-viewer__skeleton-line" style={{ width: `${40 + Math.random() * 50}%` }} />
            ))}
          </div>
        )}

        {error && (
          <div className="fe-viewer__error">
            <span>{error}</span>
            <button onClick={() => filePath && fetchContent(filePath)}>Retry</button>
          </div>
        )}

        {content && !loading && !error && (
          <>
            {content.binary && filePath && isImage(filePath) ? (
              <div className="fe-viewer__image">
                <img
                  src={`/api/sessions/${sessionId}/files/raw?path=${encodeURIComponent(filePath)}`}
                  alt={filePath.split('/').pop() || filePath}
                />
              </div>
            ) : content.binary ? (
              <div className="fe-viewer__binary">Binary file — preview not available</div>
            ) : isMarkdown && showPreview ? (
              <div className="fe-md-preview">
                <Markdown>{content.content}</Markdown>
              </div>
            ) : (
              <div className="fe-viewer__source">
                <Markdown className="fe-viewer__code-wrap">{sourceAsMarkdown}</Markdown>
              </div>
            )}

            {content.truncated && (
              <div className="fe-viewer__truncated">
                File truncated — showing {content.content.split('\n').length} of {content.total_lines} lines
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
