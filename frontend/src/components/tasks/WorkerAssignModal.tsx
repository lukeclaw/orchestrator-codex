import { useState } from 'react'
import { IconRefresh } from '../common/Icons'
import { api } from '../../api/client'
import type { Task, Session } from '../../api/types'

interface WorkerAssignModalProps {
  task: Task
  assignedSession: string
  sessions: Session[]
  tasks: Task[]
  onAssign: (sessionId: string) => Promise<void>
  onClose: () => void
}

function isWorkerConnected(status: string): boolean {
  const disconnectedStatuses = ['disconnected', 'error', 'connecting']
  return !disconnectedStatuses.includes(status)
}

export default function WorkerAssignModal({
  task,
  assignedSession,
  sessions,
  tasks,
  onAssign,
  onClose,
}: WorkerAssignModalProps) {
  const [assigning, setAssigning] = useState(false)
  const [assigningId, setAssigningId] = useState<string | null>(null)

  const handleReconnectInModal = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api(`/api/sessions/${sessionId}/reconnect`, { method: 'POST' })
    } catch (err) {
      console.error('Failed to reconnect worker:', err)
    }
  }

  const handleAssign = async (sessionId: string) => {
    if (assigning) return
    setAssigning(true)
    setAssigningId(sessionId)
    try {
      await onAssign(sessionId)
      onClose()
    } finally {
      setAssigning(false)
      setAssigningId(null)
    }
  }

  const connectedWorkers = sessions.filter(s => {
    if (s.session_type !== 'worker') return false
    if (!isWorkerConnected(s.status)) return false
    if (s.id === assignedSession) return true
    const assignedToOther = tasks.some(t => t.id !== task.id && t.assigned_session_id === s.id)
    return !assignedToOther
  })

  const disconnectedWorkers = sessions.filter(s => {
    if (s.session_type !== 'worker') return false
    if (isWorkerConnected(s.status)) return false
    const assignedToOther = tasks.some(t => t.id !== task.id && t.assigned_session_id === s.id)
    return !assignedToOther
  })

  const assignedWorker = sessions.find(s => s.id === assignedSession)

  return (
    <div className="tdp-modal-overlay" onClick={onClose}>
      <div className="tdp-modal" onClick={e => e.stopPropagation()}>
        <div className="tdp-modal-header">
          <h3>{assignedWorker ? 'Reassign Worker' : 'Assign Worker'}</h3>
          <button className="tdp-modal-close" onClick={onClose}>&times;</button>
        </div>
        <div className="tdp-modal-hint">
          After assignment, the worker will immediately start working on this task.
        </div>
        <div className="tdp-modal-body">
          <div className="tdp-worker-list">
            {assignedSession && (
              <button
                className="tdp-worker-option unassign"
                onClick={() => handleAssign('')}
                disabled={assigning}
              >
                <span className="worker-status-dot" />
                <span className="worker-name">Unassign</span>
              </button>
            )}
            {connectedWorkers.map(s => (
              <button
                key={s.id}
                className={`tdp-worker-option ${s.id === assignedSession ? 'selected' : ''}${assigningId === s.id ? ' assigning' : ''}`}
                onClick={() => {
                  if (s.id !== assignedSession) {
                    handleAssign(s.id)
                  } else {
                    onClose()
                  }
                }}
                disabled={assigning}
              >
                <span className={`worker-status-dot status-${s.status}`} />
                <span className="worker-name">{s.name}</span>
                {s.host.includes('/') ? <span className="wc-type-tag rdev">rdev</span> : s.host !== 'localhost' ? <span className="wc-type-tag ssh">ssh</span> : null}
                {assigningId === s.id ? (
                  <span className="worker-assigning-label">Assigning...</span>
                ) : (
                  <>
                    <span className={`worker-status-label status-${s.status}`}>{s.status}</span>
                    {s.id === assignedSession && <span className="worker-current">Current</span>}
                  </>
                )}
              </button>
            ))}
            {disconnectedWorkers.length > 0 && (
              <div className="tdp-worker-section-divider">
                <span>Disconnected</span>
              </div>
            )}
            {disconnectedWorkers.map(s => (
              <div
                key={s.id}
                className="tdp-worker-option disabled"
              >
                <span className={`worker-status-dot status-${s.status}`} />
                <span className="worker-name">{s.name}</span>
                {s.host.includes('/') ? <span className="wc-type-tag rdev">rdev</span> : s.host !== 'localhost' ? <span className="wc-type-tag ssh">ssh</span> : null}
                <span className={`worker-status-label status-${s.status}`}>{s.status}</span>
                <button
                  className="tdp-worker-reconnect-btn"
                  onClick={(e) => handleReconnectInModal(s.id, e)}
                  title="Reconnect worker"
                >
                  <IconRefresh size={12} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
