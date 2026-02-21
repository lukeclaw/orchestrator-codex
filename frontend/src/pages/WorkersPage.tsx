import { useState, useCallback, useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Rdev } from '../api/types'
import WorkerCard from '../components/workers/WorkerCard'
import AddSessionModal from '../components/sessions/AddSessionModal'
import RdevTable, { RdevSortKey, SortDir } from '../components/rdevs/RdevTable'
import CreateRdevModal from '../components/rdevs/CreateRdevModal'
import { IconRefresh } from '../components/common/Icons'
import { useNotify } from '../context/NotificationContext'
import './WorkersPage.css'

type SortOption = 'last_viewed' | 'last_status_changed' | 'name' | 'status'

const SORT_KEY = 'orchestrator-worker-sort'

export default function WorkersPage() {
  const { workers, tasks, rdevs, refreshRdevs } = useApp()
  const location = useLocation()

  // Determine active tab from URL
  const isRdevsPage = location.pathname === '/workers/rdevs'

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

  // Rdevs tab state (data comes from AppContext, only UI state is local)
  const [rdevsRefreshing, setRdevsRefreshing] = useState(false)
  const [showCreateRdevModal, setShowCreateRdevModal] = useState(false)
  const [rdevStateFilter, setRdevStateFilter] = useState<'' | 'RUNNING' | 'STOPPED'>('')
  const [rdevActionLoading, setRdevActionLoading] = useState<string | null>(null)
  const [rdevSortKey, setRdevSortKey] = useState<RdevSortKey>('name')
  const [rdevSortDir, setRdevSortDir] = useState<SortDir>('asc')

  const handleRefreshRdevs = useCallback(async () => {
    setRdevsRefreshing(true)
    try {
      await refreshRdevs(true)
    } finally {
      setRdevsRefreshing(false)
    }
  }, [refreshRdevs])

  // Auto-refresh when any rdev is in an intermediate state (not RUNNING or STOPPED)
  const hasIntermediateState = rdevs.some(r => r.state !== 'RUNNING' && r.state !== 'STOPPED')

  useEffect(() => {
    if (!isRdevsPage || !hasIntermediateState) return

    const interval = setInterval(() => {
      refreshRdevs(true)
    }, 60000) // 60 seconds

    return () => clearInterval(interval)
  }, [isRdevsPage, hasIntermediateState, refreshRdevs])

  const handleDeleteRdev = async (name: string) => {
    if (!confirm(`Delete rdev "${name}"? This cannot be undone.`)) return
    setRdevActionLoading(name)
    try {
      await api(`/api/rdevs/${encodeURIComponent(name)}`, { method: 'DELETE' })
      refreshRdevs(true)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to delete rdev')
    } finally {
      setRdevActionLoading(null)
    }
  }

  const handleRestartRdev = async (name: string) => {
    setRdevActionLoading(name)
    try {
      await api(`/api/rdevs/${encodeURIComponent(name)}/restart`, { method: 'POST' })
      refreshRdevs(true)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to restart rdev')
    } finally {
      setRdevActionLoading(null)
    }
  }

  const handleStopRdev = async (name: string) => {
    setRdevActionLoading(name)
    try {
      await api(`/api/rdevs/${encodeURIComponent(name)}/stop`, { method: 'POST' })
      refreshRdevs(true)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to stop rdev')
    } finally {
      setRdevActionLoading(null)
    }
  }

  const createRefreshTimers = useRef<ReturnType<typeof setTimeout>[]>([])
  const notify = useNotify()

  const handleCreateRdev = () => {
    setShowCreateRdevModal(false)
    notify('Rdev creation started — it may take 1-2 min to appear.', 'info')

    // Clear any previous pending timers
    createRefreshTimers.current.forEach(clearTimeout)
    createRefreshTimers.current = []

    // Schedule refreshes at 10s, 30s, and 60s to catch the new rdev
    for (const delay of [10_000, 30_000, 60_000]) {
      createRefreshTimers.current.push(
        setTimeout(() => refreshRdevs(true), delay)
      )
    }
  }

  const handleRdevSort = (key: RdevSortKey) => {
    if (rdevSortKey === key) {
      setRdevSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setRdevSortKey(key)
      setRdevSortDir(key === 'name' || key === 'cluster' ? 'asc' : 'desc')
    }
  }

  const getRdevSortValue = (r: Rdev, key: RdevSortKey): string | number => {
    switch (key) {
      case 'state': return r.state
      case 'name': return r.name.toLowerCase()
      case 'worker': return r.worker_name?.toLowerCase() || ''
      case 'cluster': return r.cluster?.toLowerCase() || ''
      case 'last_accessed': return r.last_accessed || ''
      case 'created': return r.created || ''
      default: return ''
    }
  }

  const filteredRdevs = (rdevStateFilter
    ? rdevs.filter(r => r.state === rdevStateFilter)
    : rdevs
  ).sort((a, b) => {
    const aVal = getRdevSortValue(a, rdevSortKey)
    const bVal = getRdevSortValue(b, rdevSortKey)
    const cmp = aVal < bVal ? -1 : aVal > bVal ? 1 : 0
    return rdevSortDir === 'asc' ? cmp : -cmp
  })

  const runningCount = rdevs.filter(r => r.state === 'RUNNING').length
  const stoppedCount = rdevs.filter(r => r.state === 'STOPPED').length

  return (
    <div className="workers-page">
      <div className="page-header">
        <div className="page-header-left">
          <h1>{isRdevsPage ? 'Rdevs' : 'Workers'}</h1>
          <Link
            to={isRdevsPage ? '/workers' : '/workers/rdevs'}
            className="tab-toggle-btn"
          >
            {isRdevsPage ? `Workers (${workers.length})` : `Rdevs (${rdevs.length})`} →
          </Link>
        </div>

        {!isRdevsPage ? (
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
        ) : (
          <div className="page-header-actions">
            <button
              className="btn btn-icon"
              onClick={handleRefreshRdevs}
              disabled={rdevsRefreshing}
              title="Refresh rdev list"
            >
              <IconRefresh size={16} className={rdevsRefreshing ? 'spinning' : ''} />
            </button>
            <select
              className="status-filter-select"
              value={rdevStateFilter}
              onChange={e => setRdevStateFilter(e.target.value as '' | 'RUNNING' | 'STOPPED')}
            >
              <option value="">All ({rdevs.length})</option>
              <option value="RUNNING">Running ({runningCount})</option>
              <option value="STOPPED">Stopped ({stoppedCount})</option>
            </select>
            <button
              className="btn btn-primary"
              onClick={() => setShowCreateRdevModal(true)}
            >
              + New Rdev
            </button>
          </div>
        )}
      </div>

      {!isRdevsPage ? (
        <>
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
        </>
      ) : (
        <>
          {filteredRdevs.length > 0 ? (
            <RdevTable
              rdevs={filteredRdevs}
              onDelete={handleDeleteRdev}
              onRestart={handleRestartRdev}
              onStop={handleStopRdev}
              actionLoading={rdevActionLoading}
              sortKey={rdevSortKey}
              sortDir={rdevSortDir}
              onSort={handleRdevSort}
            />
          ) : (
            <p className="empty-state">
              {rdevStateFilter
                ? `No rdevs with state "${rdevStateFilter}"`
                : 'No rdevs found. Click "+ New Rdev" to create one.'}
            </p>
          )}
        </>
      )}

      <AddSessionModal open={showAddModal} onClose={() => setShowAddModal(false)} />
      <CreateRdevModal
        open={showCreateRdevModal}
        onClose={() => setShowCreateRdevModal(false)}
        onCreate={handleCreateRdev}
      />
    </div>
  )
}
