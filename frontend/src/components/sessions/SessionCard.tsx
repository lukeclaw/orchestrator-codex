import { useNavigate } from 'react-router-dom'
import type { Session } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './SessionCard.css'

interface Props {
  session: Session
}

export default function SessionCard({ session }: Props) {
  const navigate = useNavigate()

  return (
    <div
      className={`session-card ${session.status}`}
      data-testid="session-card"
      data-session-id={session.id}
      onClick={() => navigate(`/workers/${session.id}`)}
    >
      <div className="sc-top">
        <span className={`status-indicator ${session.status}`} />
        <span className="sc-name">{session.name}</span>
        <span className={`status-badge ${session.status}`}>{session.status}</span>
      </div>
      <div className="sc-detail">
        <span className="sc-host">{session.host}</span>
        {session.work_dir && <span className="sc-path">{session.work_dir}</span>}
      </div>
      <div className="sc-footer">
        <span className="sc-task">
          {session.status === 'waiting'
            ? 'Needs attention'
            : session.status === 'working' ? 'Task assigned' : 'No task'}
        </span>
        <span className="sc-activity">{timeAgo(session.last_activity)}</span>
      </div>
    </div>
  )
}
