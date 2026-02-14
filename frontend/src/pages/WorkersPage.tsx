import { useState, useCallback, useEffect } from 'react'
import { useApp } from '../context/AppContext'
import { api } from '../api/client'
import WorkerCard from '../components/workers/WorkerCard'
import AddSessionModal from '../components/sessions/AddSessionModal'
import RdevTable from '../components/rdevs/RdevTable'
import CreateRdevModal from '../components/rdevs/CreateRdevModal'
import { IconRefresh } from '../components/common/Icons'
import './WorkersPage.css'

type SortOption = 'last_viewed' | 'last_status_changed' | 'name' | 'status'
type TabType = 'workers' | 'rdevs'

interface Rdev {
  name: string
  state: string
  cluster: string
  created: string
  last_accessed: string
  in_use: boolean
  worker_name?: string
  worker_status?: string
}

const SORT_KEY = 'orchestrator-worker-sort'
const TAB_KEY = 'orchestrator-workers-tab'

export default function WorkersPage() {
  const { workers, tasks } = useApp()
  const [activeTab, setActiveTab] = useState<TabType>(() => {
    const stored = localStorage.getItem(TAB_KEY)
    return (stored as TabType) || 'workers'
  })
  
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

  // Rdevs tab state
  const [rdevs, setRdevs] = useState<Rdev[]>([])
  const [rdevsLoading, setRdevsLoading] = useState(false)
  const [rdevsRefreshing, setRdevsRefreshing] = useState(false)
  const [showCreateRdevModal, setShowCreateRdevModal] = useState(false)
  const [rdevStateFilter, setRdevStateFilter] = useState<'' | 'RUNNING' | 'STOPPED'>('')
  const [rdevActionLoading, setRdevActionLoading] = useState<string | null>(null)

  const handleTabChange = (tab: TabType) => {
    setActiveTab(tab)
    localStorage.setItem(TAB_KEY, tab)
  }

  const fetchRdevs = useCallback(async (forceRefresh = false) => {
    if (forceRefresh) {
      setRdevsRefreshing(true)
    } else {
      setRdevsLoading(true)
    }
    try {
      const url = forceRefresh ? '/api/rdevs?refresh=true' : '/api/rdevs'
      const data = await api<Rdev[]>(url)
      setRdevs(data)
    } catch (e) {
      console.error('Failed to fetch rdevs:', e)
    } finally {
      setRdevsLoading(false)
      setRdevsRefreshing(false)
    }
  }, [])

  useEffect(() => {
    if (activeTab === 'rdevs' && rdevs.length === 0) {
      fetchRdevs()
    }
  }, [activeTab, rdevs.length, fetchRdevs])

  const handleDeleteRdev = async (name: string) => {
    if (!confirm(`Delete rdev "${name}"? This cannot be undone.`)) return
    setRdevActionLoading(name)
    try {
      await api(`/api/rdevs/${encodeURIComponent(name)}`, { method: 'DELETE' })
      fetchRdevs(true)
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
      fetchRdevs(true)
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
      fetchRdevs(true)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Failed to stop rdev')
    } finally {
      setRdevActionLoading(null)
    }
  }

  const handleCreateRdev = () => {
    setShowCreateRdevModal(false)
    fetchRdevs(true)
  }

  const filteredRdevs = rdevStateFilter
    ? rdevs.filter(r => r.state === rdevStateFilter)
    : rdevs

  const runningCount = rdevs.filter(r => r.state === 'RUNNING').length
  const stoppedCount = rdevs.filter(r => r.state === 'STOPPED').length

  return (
    <div className="workers-page">
      <div className="page-header">
        <div className="page-header-left">
          <h1>Workers</h1>
          <div className="tab-nav">
            <button
              className={`tab-btn ${activeTab === 'workers' ? 'active' : ''}`}
              onClick={() => handleTabChange('workers')}
            >
              Workers ({workers.length})
            </button>
            <button
              className={`tab-btn ${activeTab === 'rdevs' ? 'active' : ''}`}
              onClick={() => handleTabChange('rdevs')}
            >
              Rdevs ({rdevs.length})
            </button>
          </div>
        </div>

        {activeTab === 'workers' ? (
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
              onClick={() => fetchRdevs(true)}
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

      {activeTab === 'workers' ? (
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
          {rdevsLoading ? (
            <div className="loading-state">Loading rdev instances...</div>
          ) : filteredRdevs.length > 0 ? (
            <RdevTable
              rdevs={filteredRdevs}
              onDelete={handleDeleteRdev}
              onRestart={handleRestartRdev}
              onStop={handleStopRdev}
              actionLoading={rdevActionLoading}
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
