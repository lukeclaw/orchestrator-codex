import { useNavigate, Link } from 'react-router-dom'
import type { Session, Task } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import StatusDot from '../common/StatusDot'
import './WorkerCardCompact.css'

interface Props {
  session: Session
  assignedTask?: Task | null
  allRdev?: boolean
}

export default function WorkerCardCompact({ session, assignedTask, allRdev }: Props) {
  const navigate = useNavigate()

  const previewLines = session.preview ? session.preview.split('\n').slice(-8).join('\n') : ''

  // Split name into project prefix and unique suffix at the underscore
  const underscoreIdx = session.name.indexOf('_')
  const hasPrefix = underscoreIdx > 0
  const namePrefix = hasPrefix ? session.name.slice(0, underscoreIdx) : ''
  const nameSuffix = hasPrefix ? session.name.slice(underscoreIdx + 1) : session.name

  return (
    <div
      className={`wcc-card ${session.status}`}
      data-testid="worker-card"
      data-session-id={session.id}
      onClick={() => navigate(`/workers/${session.id}`)}
    >
      <div className="wcc-header">
        <StatusDot status={session.status} />
        <span className="wcc-name" title={session.name}>
          {hasPrefix && <span className="wcc-name-prefix">{namePrefix}_</span>}
          <span className="wcc-name-suffix">{nameSuffix}</span>
        </span>
        {!allRdev && session.host.includes('/') && <span className="wcc-type-tag rdev">rdev</span>}
        {session.host !== 'localhost' && !session.host.includes('/') && <span className="wcc-type-tag ssh">ssh</span>}
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
