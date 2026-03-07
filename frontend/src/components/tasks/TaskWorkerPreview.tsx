import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { timeAgo } from '../common/TimeAgo'
import type { Session } from '../../api/types'
import {
  IconPause,
  IconPlay,
  IconStop,
  IconRefresh,
} from '../common/Icons'
import ConfirmPopover from '../common/ConfirmPopover'

interface TaskWorkerPreviewProps {
  worker: Session
  onRefresh: () => void
}

export default function TaskWorkerPreview({ worker, onRefresh }: TaskWorkerPreviewProps) {
  const navigate = useNavigate()
  const [workerPreview, setWorkerPreview] = useState('')
  const [actionPending, setActionPending] = useState(false)

  useEffect(() => {
    async function fetchPreview() {
      try {
        const data = await api<{ content: string }>(`/api/sessions/${worker.id}/preview`)
        setWorkerPreview(data.content || '')
      } catch {
        setWorkerPreview('')
      }
    }

    fetchPreview()
    const interval = setInterval(fetchPreview, 5000)
    return () => clearInterval(interval)
  }, [worker.id])

  async function handlePauseOrContinue(e: React.MouseEvent) {
    e.stopPropagation()
    if (actionPending) return
    setActionPending(true)
    try {
      const endpoint = worker.status === 'paused' ? 'continue' : 'pause'
      await api(`/api/sessions/${worker.id}/${endpoint}`, { method: 'POST' })
      onRefresh()
    } finally {
      setActionPending(false)
    }
  }

  async function handleStop() {
    if (actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${worker.id}/stop`, { method: 'POST' })
      onRefresh()
    } finally {
      setActionPending(false)
    }
  }

  async function handleReconnect(e: React.MouseEvent) {
    e.stopPropagation()
    if (actionPending) return
    setActionPending(true)
    try {
      await api(`/api/sessions/${worker.id}/reconnect`, { method: 'POST' })
      onRefresh()
    } finally {
      setActionPending(false)
    }
  }

  const isDisconnected = worker.status === 'disconnected' || worker.status === 'screen_detached' || worker.status === 'error'

  return (
    <div
      className={`tdp-card tdp-worker-preview-card status-${worker.status}`}
      onClick={() => navigate(`/workers/${worker.id}`)}
    >
      <div className="tdp-worker-preview-header">
        <div className="tdp-worker-preview-left">
          <span className={`status-indicator ${worker.status}`} />
          <span className="tdp-worker-preview-name">
            {worker.name}
          </span>
          {worker.host.includes('/') && <span className="wc-type-tag rdev">rdev</span>}
          <span className={`status-badge small ${worker.status}`}>{worker.status}</span>
        </div>
        <div className="tdp-worker-preview-actions">
          {isDisconnected ? (
            <button
              className="wc-action-btn reconnect"
              onClick={handleReconnect}
              disabled={actionPending}
              title="Reconnect"
            >
              <IconRefresh size={14} />
            </button>
          ) : (
            <>
              <button
                className={`wc-action-btn ${worker.status === 'paused' ? 'continue' : 'pause'}`}
                onClick={handlePauseOrContinue}
                disabled={actionPending || worker.status === 'idle'}
                title={worker.status === 'paused' ? 'Continue' : 'Pause'}
              >
                {worker.status === 'paused' ? <IconPlay size={14} /> : <IconPause size={14} />}
              </button>
              <ConfirmPopover
                message={`Stop worker "${worker.name}" and clear context?`}
                confirmLabel="Stop"
                onConfirm={handleStop}
                variant="danger"
              >
                {({ onClick }) => (
                  <button
                    className="wc-action-btn stop"
                    onClick={(e) => { e.stopPropagation(); onClick(e); }}
                    disabled={actionPending || worker.status === 'idle'}
                    title="Stop and clear"
                  >
                    <IconStop size={14} />
                  </button>
                )}
              </ConfirmPopover>
            </>
          )}
        </div>
      </div>
      <div className="tdp-worker-preview-terminal">
        <pre>{workerPreview ? workerPreview.split('\n').slice(-15).join('\n') : 'No terminal output yet...'}</pre>
      </div>
      <div className="tdp-worker-preview-footer">
        <span className="tdp-worker-preview-activity">
          {worker.last_status_changed_at ? timeAgo(worker.last_status_changed_at) : 'just now'}
        </span>
      </div>
    </div>
  )
}
