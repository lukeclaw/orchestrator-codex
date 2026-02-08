import { useApp } from '../../context/AppContext'
import { shortTime } from '../common/TimeAgo'
import './ActivityTimeline.css'

function extractDetail(eventData: string | Record<string, unknown> | null): string {
  if (!eventData) return ''
  try {
    const data = typeof eventData === 'string' ? JSON.parse(eventData) : eventData
    return Object.values(data)
      .filter((v): v is string => typeof v === 'string')
      .join(' \u2014 ')
  } catch {
    return String(eventData)
  }
}

export default function ActivityTimeline() {
  const { activities, loading } = useApp()

  if (loading) {
    return <p className="empty-state">Loading activity...</p>
  }

  if (!activities.length) {
    return <p className="empty-state">No recent activity</p>
  }

  return (
    <div className="activity-timeline" data-testid="activity-timeline">
      {activities.map(a => (
        <div key={a.id} className="activity-item" data-testid="activity-item">
          <span className="at-time">{shortTime(a.created_at)}</span>
          <span className="at-type">{a.event_type}</span>
          <span className="at-detail">{extractDetail(a.event_data)}</span>
        </div>
      ))}
    </div>
  )
}
