import { useState, useEffect, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import type { Session, Task } from '../../api/types'
import { api } from '../../api/client'
import { timeAgo } from '../common/TimeAgo'
import './WorkerCardCompact.css'

interface Props {
  session: Session
  assignedTask?: Task | null
}

export default function WorkerCardCompact({ session, assignedTask }: Props) {
  const navigate = useNavigate()
  const [preview, setPreview] = useState('')
  const intervalRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const sessionIdRef = useRef(session.id)
  const fetchGenRef = useRef(0)

  sessionIdRef.current = session.id

  useEffect(() => {
    const currentGen = ++fetchGenRef.current
    const targetId = session.id
    setPreview('')

    async function fetchPreview() {
      if (sessionIdRef.current !== targetId || fetchGenRef.current !== currentGen) return
      try {
        const data = await api<{ content: string; status: string }>(
          `/api/sessions/${targetId}/preview`
        )
        if (sessionIdRef.current !== targetId || fetchGenRef.current !== currentGen) return
        if (data.content) setPreview(data.content)
      } catch { /* ignore */ }
    }

    fetchPreview()
    intervalRef.current = setInterval(fetchPreview, 5000)
    return () => clearInterval(intervalRef.current)
  }, [session.id])

  const previewLines = preview ? preview.split('\n').slice(-8).join('\n') : ''

  return (
    <div
      className={`wcc-card ${session.status}`}
      onClick={() => navigate(`/workers/${session.id}`)}
    >
      <div className="wcc-header">
        <span className={`status-indicator ${session.status}`} />
        <span className="wcc-name">{session.name}</span>
        {session.host.includes('/') && <span className="wcc-type-tag rdev">rdev</span>}
        <span className={`status-badge ${session.status}`}>{session.status}</span>
      </div>

      <div className="wcc-preview">
        <pre>{previewLines || 'No output yet...'}</pre>
      </div>

      <div className="wcc-footer">
        {assignedTask ? (
          <Link
            to={`/tasks/${assignedTask.id}`}
            className="wcc-task-badge"
            onClick={e => e.stopPropagation()}
            title={assignedTask.title}
          >
            <span className="wcc-task-key">{assignedTask.task_key}</span>
            <span className="wcc-task-title">{assignedTask.title}</span>
          </Link>
        ) : (
          <span className="wcc-no-task">No task</span>
        )}
        <span className="wcc-activity" title="Last viewed">{timeAgo(session.last_viewed_at || session.created_at)}</span>
      </div>
    </div>
  )
}
