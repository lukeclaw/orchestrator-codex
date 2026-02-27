import { useState, useCallback, useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import type { Rdev } from '../api/types'
import WorkerCard from '../components/workers/WorkerCard'
import AddSessionModal from '../components/sessions/AddSessionModal'
import RdevTable, { RdevSortKey, SortDir } from '../components/rdevs/RdevTable'
import CreateRdevModal from '../components/rdevs/CreateRdevModal'
import CustomSelect from '../components/common/CustomSelect'
import { IconRefresh, IconSessions, IconFilter } from '../components/common/Icons'
import { useNotify } from '../context/NotificationContext'
import './WorkersPage.css'

type SortOption = 'last_viewed' | 'last_status_changed' | 'name' | 'status'

const SORT_KEY = 'orchestrator-worker-sort'

const STATUS_ORDER = ['working', 'idle', 'waiting', 'paused', 'error', 'disconnected', 'screen_detached', 'connecting'] as const

const STATUS_COLORS: Record<string, string> = {
  working: '#58a6ff',
  idle: '#3fb950',
  waiting: '#d29922',
  paused: '#f97316',
  error: '#f85149',
  disconnected: '#f85149',
  screen_detached: '#f97316',
  connecting: '#58a6ff',
}

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
  const handleSortChange = (value: string) => {
    setSortBy(value as SortOption)
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

  // Compute status counts for summary bar
  const statusCounts = workers.reduce<Record<string, number>>((acc, w) => {
    acc[w.status] = (acc[w.status] || 0) + 1
    return acc
  }, {})

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
    setRdevActionLoading(name)
    try {
      await api(`/api/rdevs/${encodeURIComponent(name)}`, { method: 'DELETE' })
      refreshRdevs(true)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to delete rdev', 'error')
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
      notify(e instanceof Error ? e.message : 'Failed to restart rdev', 'error')
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
      notify(e instanceof Error ? e.message : 'Failed to stop rdev', 'error')
    } finally {
      setRdevActionLoading(null)
    }
  }

  const createRefreshTimers = useRef<ReturnType<typeof setTimeout>[]>([])
  const notify = useNotify()

  const handleCreateRdev = (jobId: string) => {
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

    // Poll for job completion to surface errors
    const pollJob = async () => {
      const MAX_POLLS = 30  // up to ~150s
      const POLL_INTERVAL = 5000
      for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise(r => setTimeout(r, POLL_INTERVAL))
        try {
          const job = await api<{ status: string; error?: string; name?: string }>(`/api/rdevs/jobs/${jobId}`)
          if (job.status === 'done') {
            notify(`Rdev created: ${job.name}`, 'success')
            refreshRdevs(true)
            return
          }
          if (job.status === 'failed') {
            notify(`Rdev creation failed: ${job.error}`, 'error')
            return
          }
        } catch {
          // Job endpoint gone or server error — stop polling
          return
        }
      }
    }
    pollJob()
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
          {isRdevsPage ? (
            <>
              <Link to="/workers" className="page-back-link">← Workers</Link>
              <h1>Rdevs</h1>
            </>
          ) : (
            <>
              <h1>Workers</h1>
              <Link to="/workers/rdevs" className="page-sub-link">
                Rdevs ({rdevs.length}) →
              </Link>
            </>
          )}
        </div>

        {!isRdevsPage ? (
          <div className="page-header-actions">
            <CustomSelect
              prefix="Sort by:"
              value={sortBy}
              onChange={handleSortChange}
              options={[
                { value: 'last_viewed', label: 'Last Viewed' },
                { value: 'last_status_changed', label: 'Last Status Changed' },
                { value: 'name', label: 'Name' },
                { value: 'status', label: 'Status' },
              ]}
            />
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
            <CustomSelect
              value={rdevStateFilter}
              onChange={v => setRdevStateFilter(v as '' | 'RUNNING' | 'STOPPED')}
              options={[
                { value: '', label: `All (${rdevs.length})` },
                { value: 'RUNNING', label: `Running (${runningCount})` },
                { value: 'STOPPED', label: `Stopped (${stoppedCount})` },
              ]}
            />
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
          {/* Status summary bar */}
          {workers.length > 0 && (
            <div className="status-summary-bar">
              {STATUS_ORDER.filter(s => statusCounts[s]).map(status => (
                <button
                  key={status}
                  className={`status-summary-item${statusFilter === status ? ' active' : ''}`}
                  onClick={() => setStatusFilter(statusFilter === status ? '' : status)}
                  type="button"
                >
                  <span className="status-summary-dot" style={{ background: STATUS_COLORS[status] }} />
                  <span className="status-summary-count">{statusCounts[status]}</span>
                  <span className="status-summary-label">{status.replace('_', ' ')}</span>
                </button>
              ))}
            </div>
          )}

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
            <div className="workers-empty-state">
              {statusFilter ? (
                <>
                  <IconFilter size={32} />
                  <p>No workers with status "{statusFilter}"</p>
                  <button className="btn btn-secondary" onClick={() => setStatusFilter('')}>
                    Clear filter
                  </button>
                </>
              ) : (
                <>
                  <IconSessions size={48} />
                  <h3>No workers yet</h3>
                  <p>Add a worker to get started with Claude Code sessions.</p>
                  <button
                    className="btn btn-primary"
                    onClick={() => setShowAddModal(true)}
                  >
                    + Add Worker
                  </button>
                </>
              )}
            </div>
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
