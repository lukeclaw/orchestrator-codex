import { useApp } from '../../context/AppContext'
import SessionCard from './SessionCard'
import './SessionGrid.css'

export default function SessionGrid() {
  const { sessions, loading } = useApp()

  if (loading) {
    return <p className="empty-state">Loading sessions...</p>
  }

  if (!sessions.length) {
    return (
      <p className="empty-state">
        No sessions yet. Click &quot;+ Add Session&quot; to get started.
      </p>
    )
  }

  return (
    <div className="session-grid" data-testid="session-grid">
      {sessions.map(s => (
        <SessionCard key={s.id} session={s} />
      ))}
    </div>
  )
}
