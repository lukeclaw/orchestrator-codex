import { useState, useMemo } from 'react'
import type { Activity } from '../api/types'
import { useActivities } from '../hooks/useActivities'
import { useApp } from '../context/AppContext'
import FilterBar from '../components/common/FilterBar'
import { shortTime, timeAgo } from '../components/common/TimeAgo'
import './ActivityPage.css'

function groupByDay(activities: Activity[]): Map<string, Activity[]> {
  const groups = new Map<string, Activity[]>()
  for (const a of activities) {
    const date = a.created_at.split('T')[0] || a.created_at.split(' ')[0]
    const existing = groups.get(date)
    if (existing) {
      existing.push(a)
    } else {
      groups.set(date, [a])
    }
  }
  return groups
}

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

export default function ActivityPage() {
  const { sessions } = useApp()
  const [sessionFilter, setSessionFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')

  const { activities, loading } = useActivities({
    session_id: sessionFilter || undefined,
    event_type: typeFilter || undefined,
    limit: 50,
  })

  const sessionOptions = useMemo(() => [
    { value: '', label: 'All Sessions' },
    ...sessions.map(s => ({ value: s.id, label: s.name })),
  ], [sessions])

  const grouped = groupByDay(activities)

  return (
    <div className="activity-page">
      <div className="page-header">
        <h1>Activity</h1>
      </div>

      <FilterBar
        filters={[
          {
            key: 'session',
            label: 'Session',
            value: sessionFilter,
            options: sessionOptions,
          },
          {
            key: 'type',
            label: 'Type',
            value: typeFilter,
            options: [
              { value: '', label: 'All Types' },
              { value: 'session_created', label: 'Session Created' },
              { value: 'task_started', label: 'Task Started' },
              { value: 'task_completed', label: 'Task Completed' },
              { value: 'decision_requested', label: 'Decision Requested' },
              { value: 'pr_opened', label: 'PR Opened' },
              { value: 'error', label: 'Error' },
            ],
          },
        ]}
        onChange={(key, val) => {
          if (key === 'session') setSessionFilter(val)
          if (key === 'type') setTypeFilter(val)
        }}
      />

      {loading ? (
        <p className="empty-state">Loading activity...</p>
      ) : activities.length === 0 ? (
        <p className="empty-state">No activity recorded yet</p>
      ) : (
        <div className="activity-groups">
          {Array.from(grouped.entries()).map(([date, items]) => (
            <div key={date} className="ag-group">
              <div className="ag-date">{timeAgo(date + 'T00:00:00') === 'just now' ? 'Today' : date}</div>
              <div className="ag-items">
                {items.map((a, i) => (
                  <div key={i} className="ag-item" data-testid="activity-item">
                    <span className="at-time">{shortTime(a.created_at)}</span>
                    <span className="at-type">{a.event_type}</span>
                    <span className="at-detail">{extractDetail(a.event_data)}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
