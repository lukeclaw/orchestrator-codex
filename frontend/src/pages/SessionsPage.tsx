import { useState } from 'react'
import { useApp } from '../context/AppContext'
import SessionGrid from '../components/sessions/SessionGrid'
import AddSessionModal from '../components/sessions/AddSessionModal'
import FilterBar from '../components/common/FilterBar'
import './SessionsPage.css'

export default function SessionsPage() {
  const { sessions } = useApp()
  const [showAddModal, setShowAddModal] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')

  const filtered = statusFilter
    ? sessions.filter(s => s.status === statusFilter)
    : sessions

  return (
    <div className="sessions-page">
      <div className="page-header">
        <h1>Sessions</h1>
        <button
          className="btn btn-primary"
          data-testid="add-session-btn"
          onClick={() => setShowAddModal(true)}
        >
          + Add Session
        </button>
      </div>

      <FilterBar
        filters={[{
          key: 'status',
          label: 'Status',
          value: statusFilter,
          options: [
            { value: '', label: 'All ({0})'.replace('{0}', String(sessions.length)) },
            { value: 'idle', label: 'Idle' },
            { value: 'working', label: 'Working' },
            { value: 'waiting', label: 'Waiting' },
            { value: 'error', label: 'Error' },
            { value: 'disconnected', label: 'Disconnected' },
          ],
        }]}
        onChange={(_, v) => setStatusFilter(v)}
      />

      {statusFilter ? (
        filtered.length > 0 ? (
          <div className="session-grid" data-testid="session-grid">
            {filtered.map(s => (
              <div
                key={s.id}
                className={`session-card ${s.status}`}
                data-testid="session-card"
                data-session-id={s.id}
                onClick={() => window.location.href = `/sessions/${s.id}`}
              >
                <div className="sc-top">
                  <span className={`status-indicator ${s.status}`} />
                  <span className="sc-name">{s.name}</span>
                  <span className={`status-badge ${s.status}`}>{s.status}</span>
                </div>
                <div className="sc-detail">
                  <span className="sc-host">{s.host}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="empty-state">No sessions with status "{statusFilter}"</p>
        )
      ) : (
        <SessionGrid />
      )}

      <AddSessionModal open={showAddModal} onClose={() => setShowAddModal(false)} />
    </div>
  )
}
