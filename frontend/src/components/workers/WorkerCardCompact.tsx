import { useNavigate, Link } from 'react-router-dom'
import type { Session, Task } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './WorkerCardCompact.css'

interface Props {
  session: Session
  assignedTask?: Task | null
}

export default function WorkerCardCompact({ session, assignedTask }: Props) {
  const navigate = useNavigate()

  const previewLines = session.preview ? session.preview.split('\n').slice(-8).join('\n') : ''

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
