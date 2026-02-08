import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Session } from '../../api/types'
import { api } from '../../api/client'
import { useNotify } from '../../context/NotificationContext'
import { timeAgo } from '../common/TimeAgo'
import { IconTrash, IconGripVertical } from '../common/Icons'
import './WorkerCard.css'

interface Props {
  session: Session
  onRemove?: (id: string) => void
  draggable?: boolean
  onDragStart?: (e: React.DragEvent) => void
  onDragOver?: (e: React.DragEvent) => void
  onDragEnd?: (e: React.DragEvent) => void
  onDrop?: (e: React.DragEvent) => void
}

export default function WorkerCard({
  session, onRemove, draggable, onDragStart, onDragOver, onDragEnd, onDrop,
}: Props) {
  const navigate = useNavigate()
  const notify = useNotify()
  const [preview, setPreview] = useState('')
  const [removing, setRemoving] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  useEffect(() => {
    let cancelled = false

    async function fetchPreview() {
      try {
        const data = await api<{ content: string; status: string }>(
          `/api/sessions/${session.id}/preview`
        )
        if (!cancelled) setPreview(data.content)
      } catch {
        // ignore — preview is best-effort
      }
    }

    fetchPreview()
    intervalRef.current = setInterval(fetchPreview, 5000)

    return () => {
      cancelled = true
      clearInterval(intervalRef.current)
    }
  }, [session.id])

  async function handleRemove(e: React.MouseEvent) {
    e.stopPropagation()
    if (removing) return
    setRemoving(true)
    try {
      // Stop the worker first (send Ctrl-C), then delete
      try {
        await api(`/api/sessions/${session.id}/stop`, { method: 'POST' })
      } catch {
        // may already be stopped
      }
      await api(`/api/sessions/${session.id}`, { method: 'DELETE' })
      notify(`Removed worker ${session.name}`, 'success')
      onRemove?.(session.id)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to remove worker', 'error')
      setRemoving(false)
    }
  }

  // Take last ~20 lines for the preview
  const previewLines = preview.split('\n').slice(-20).join('\n')

  return (
    <div
      className={`worker-card ${session.status}${removing ? ' removing' : ''}`}
      data-testid="worker-card"
      data-session-id={session.id}
      draggable={draggable}
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDragEnd={onDragEnd}
      onDrop={onDrop}
      onClick={() => navigate(`/workers/${session.id}`)}
    >
      <div className="wc-header">
        <div className="wc-header-left">
          {draggable && (
            <span
              className="wc-drag-handle"
              onMouseDown={e => e.stopPropagation()}
              title="Drag to reorder"
            >
              <IconGripVertical size={14} />
            </span>
          )}
          <span className={`status-indicator ${session.status}`} />
          <span className="wc-name">{session.name}</span>
          <span className={`status-badge ${session.status}`}>{session.status}</span>
        </div>
        <button
          className="wc-remove-btn"
          onClick={handleRemove}
          disabled={removing}
          title="Remove worker"
        >
          <IconTrash size={14} />
        </button>
      </div>

      <div className="wc-terminal-preview">
        <pre>{previewLines || 'No terminal output yet...'}</pre>
      </div>

      <div className="wc-footer">
        <span className="wc-host">{session.host}</span>
        <span className="wc-task">
          {session.current_task_id ? 'Task assigned' : 'No task'}
        </span>
        <span className="wc-activity">{timeAgo(session.last_activity)}</span>
      </div>
    </div>
  )
}
