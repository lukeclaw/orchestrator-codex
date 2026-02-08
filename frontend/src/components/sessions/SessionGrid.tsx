import { useApp } from '../../context/AppContext'
import SessionCard from './SessionCard'
import './SessionGrid.css'

export default function SessionGrid() {
  const { workers, loading } = useApp()

  if (loading) {
    return <p className="empty-state">Loading workers...</p>
  }

  if (!workers.length) {
    return (
      <p className="empty-state">
        No workers yet. Click &quot;+ Add Worker&quot; to get started.
      </p>
    )
  }

  return (
    <div className="session-grid" data-testid="session-grid">
      {workers.map(s => (
        <SessionCard key={s.id} session={s} />
      ))}
    </div>
  )
}
