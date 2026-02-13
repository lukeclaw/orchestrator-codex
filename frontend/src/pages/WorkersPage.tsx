import { useState, useCallback } from 'react'
import { useApp } from '../context/AppContext'
import WorkerCard from '../components/workers/WorkerCard'
import AddSessionModal from '../components/sessions/AddSessionModal'
import './WorkersPage.css'

type SortOption = 'last_viewed' | 'last_status_changed' | 'name' | 'status'

const SORT_KEY = 'orchestrator-worker-sort'

export default function WorkersPage() {
  const { workers, tasks } = useApp()
  
  // Build a map of session_id -> assigned task for quick lookup
  const taskBySession = new Map(
    tasks
      .filter(t => t.assigned_session_id)
      .map(t => [t.assigned_session_id!, t])
  )
  const [showAddModal, setShowAddModal] = useState(false)
  const [statusFilter, setStatusFilter] = useState('')
  const [sortBy, setSortBy] = useState<SortOption>(() => {
    const stored = localStorage.getItem(SORT_KEY)
    return (stored as SortOption) || 'last_viewed'
  })

  const handleSortChange = (value: SortOption) => {
    setSortBy(value)
    localStorage.setItem(SORT_KEY, value)
  }

  // Sort workers based on selected option
  const sorted = [...workers].sort((a, b) => {
    switch (sortBy) {
      case 'last_viewed': {
        const aTime = new Date(a.last_viewed_at || a.created_at).getTime()
        const bTime = new Date(b.last_viewed_at || b.created_at).getTime()
        return bTime - aTime  // Descending (newest first)
      }
      case 'last_status_changed': {
        const aTime = new Date(a.last_status_changed_at || a.created_at).getTime()
        const bTime = new Date(b.last_status_changed_at || b.created_at).getTime()
        return bTime - aTime  // Descending (newest first)
      }
      case 'name':
        return a.name.localeCompare(b.name)
      case 'status':
        return a.status.localeCompare(b.status)
      default:
        return 0
    }
  })

  const filtered = statusFilter
    ? sorted.filter(s => s.status === statusFilter)
    : sorted

  const { removeSession } = useApp()

  const handleRemove = useCallback((id: string) => {
    removeSession(id)
  }, [removeSession])

  return (
    <div className="workers-page">
      <div className="page-header">
        <h1>Workers</h1>
        <div className="page-header-actions">
          <div className="sort-control">
            <label>Sort by:</label>
            <select
              className="sort-select"
              value={sortBy}
              onChange={e => handleSortChange(e.target.value as SortOption)}
            >
              <option value="last_viewed">Last Viewed</option>
              <option value="last_status_changed">Last Status Changed</option>
              <option value="name">Name</option>
              <option value="status">Status</option>
            </select>
          </div>
          <select
            className="status-filter-select"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
          >
            <option value="">All ({workers.length})</option>
            <option value="connecting">Connecting</option>
            <option value="idle">Idle</option>
            <option value="working">Working</option>
            <option value="waiting">Waiting</option>
            <option value="error">Error</option>
            <option value="screen_detached">Screen Detached</option>
            <option value="disconnected">Disconnected</option>
          </select>
          <button
            className="btn btn-primary"
            data-testid="add-session-btn"
            onClick={() => setShowAddModal(true)}
          >
            + Add Worker
          </button>
        </div>
      </div>

      {filtered.length > 0 ? (
        <div className="worker-grid" data-testid="session-grid">
          {filtered.map(s => (
            <WorkerCard
              key={s.id}
              session={s}
              assignedTask={taskBySession.get(s.id) || null}
              onRemove={handleRemove}
            />
          ))}
        </div>
      ) : (
        <p className="empty-state">
          {statusFilter
            ? `No workers with status "${statusFilter}"`
            : 'No workers yet. Click "+ Add Worker" to get started.'}
        </p>
      )}

      <AddSessionModal open={showAddModal} onClose={() => setShowAddModal(false)} />
    </div>
  )
}
