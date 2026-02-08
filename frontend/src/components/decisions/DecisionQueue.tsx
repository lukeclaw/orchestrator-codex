import { useApp } from '../../context/AppContext'
import DecisionCard from './DecisionCard'

export default function DecisionQueue() {
  const { decisions, loading } = useApp()

  if (loading) {
    return <p className="empty-state">Loading decisions...</p>
  }

  if (!decisions.length) {
    return <p className="empty-state">No pending decisions</p>
  }

  return (
    <div data-testid="decision-queue">
      {decisions.map(d => (
        <DecisionCard key={d.id} decision={d} />
      ))}
    </div>
  )
}
