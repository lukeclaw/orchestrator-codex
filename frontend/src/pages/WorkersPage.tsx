import { useState, useRef, useCallback } from 'react'
import { useApp } from '../context/AppContext'
import WorkerCard from '../components/workers/WorkerCard'
import AddSessionModal from '../components/sessions/AddSessionModal'
import './WorkersPage.css'

const ORDER_KEY = 'orchestrator-worker-order'

function getStoredOrder(): string[] {
  try {
    const raw = localStorage.getItem(ORDER_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveOrder(ids: string[]) {
  localStorage.setItem(ORDER_KEY, JSON.stringify(ids))
}

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
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [overIdx, setOverIdx] = useState<number | null>(null)
  const dragCounter = useRef(0)

  // Sort workers by last_viewed_at (most recent first), then by stored order for drag reordering
  const storedOrder = getStoredOrder()
  const sorted = [...workers].sort((a, b) => {
    // Primary sort: by last_viewed_at (most recent first), fallback to created_at
    const aViewed = new Date(a.last_viewed_at || a.created_at).getTime()
    const bViewed = new Date(b.last_viewed_at || b.created_at).getTime()
    if (aViewed !== bViewed) {
      return bViewed - aViewed  // Descending (newest first)
    }
    // Secondary sort: by stored order for manual drag reordering
    const ai = storedOrder.indexOf(a.id)
    const bi = storedOrder.indexOf(b.id)
    if (ai === -1 && bi === -1) {
      return a.id.localeCompare(b.id)
    }
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })

  const filtered = statusFilter
    ? sorted.filter(s => s.status === statusFilter)
    : sorted

  function handleDragStart(idx: number) {
    return (e: React.DragEvent) => {
      setDragIdx(idx)
      e.dataTransfer.effectAllowed = 'move'
      ;(e.currentTarget as HTMLElement).classList.add('dragging')
    }
  }

  function handleDragOver(idx: number) {
    return (e: React.DragEvent) => {
      e.preventDefault()
      e.dataTransfer.dropEffect = 'move'
      setOverIdx(idx)
    }
  }

  function handleDragEnd(e: React.DragEvent) {
    ;(e.currentTarget as HTMLElement).classList.remove('dragging')
    setDragIdx(null)
    setOverIdx(null)
    dragCounter.current = 0
  }

  function handleDrop(targetIdx: number) {
    return (e: React.DragEvent) => {
      e.preventDefault()
      if (dragIdx === null || dragIdx === targetIdx) return

      const reordered = [...filtered]
      const [moved] = reordered.splice(dragIdx, 1)
      reordered.splice(targetIdx, 0, moved)

      // Save full order (include any workers not in filtered view)
      const newOrder = reordered.map(w => w.id)
      // Append IDs that are in sorted but not in filtered
      for (const w of sorted) {
        if (!newOrder.includes(w.id)) newOrder.push(w.id)
      }
      saveOrder(newOrder)

      setDragIdx(null)
      setOverIdx(null)
    }
  }

  const { removeSession } = useApp()

  const handleRemove = useCallback((id: string) => {
    // Remove from client cache immediately for instant UI feedback
    removeSession(id)
    // Remove from stored order
    const order = getStoredOrder().filter(oid => oid !== id)
    saveOrder(order)
  }, [removeSession])

  return (
    <div className="workers-page">
      <div className="page-header">
        <h1>Workers</h1>
        <div className="page-header-actions">
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
          {filtered.map((s, idx) => (
            <WorkerCard
              key={s.id}
              session={s}
              assignedTask={taskBySession.get(s.id) || null}
              onRemove={handleRemove}
              draggable={!statusFilter}
              onDragStart={handleDragStart(idx)}
              onDragOver={handleDragOver(idx)}
              onDragEnd={handleDragEnd}
              onDrop={handleDrop(idx)}
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
