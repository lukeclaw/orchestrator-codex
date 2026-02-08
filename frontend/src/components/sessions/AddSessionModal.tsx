import { useState, useEffect, useCallback } from 'react'
import Modal from '../common/Modal'
import { api } from '../../api/client'
import { useApp } from '../../context/AppContext'
import { IconRefresh } from '../common/Icons'
import './AddSessionModal.css'

interface Props {
  open: boolean
  onClose: () => void
}

interface RdevInstance {
  name: string
  state: string
  cluster: string
  created: string
  last_accessed: string
  in_use: boolean
  worker_name?: string
}

// Generate a default worker name like "w1", "w2", etc.
function generateWorkerName(): string {
  const timestamp = Date.now().toString(36).slice(-3)
  return `w${timestamp}`
}

const WORKER_BASE_DIR = '/tmp/orchestrator/workers'

export default function AddSessionModal({ open, onClose }: Props) {
  const { refresh } = useApp()
  const [name, setName] = useState(generateWorkerName)
  const [workerType, setWorkerType] = useState<'rdev' | 'local'>('local')
  const [selectedRdev, setSelectedRdev] = useState<string>('')
  const [host, setHost] = useState('localhost')
  const [mpPath, setMpPath] = useState('')
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  
  // Rdev list state
  const [rdevs, setRdevs] = useState<RdevInstance[]>([])
  const [loadingRdevs, setLoadingRdevs] = useState(false)
  const [rdevError, setRdevError] = useState('')

  // Fetch rdev list (forceRefresh bypasses server cache)
  const fetchRdevs = useCallback(async (forceRefresh = false) => {
    setLoadingRdevs(true)
    setRdevError('')
    try {
      const url = forceRefresh ? '/api/rdevs?refresh=true' : '/api/rdevs'
      const data = await api<RdevInstance[]>(url)
      setRdevs(data)
    } catch (e) {
      setRdevError(e instanceof Error ? e.message : 'Failed to fetch rdev list')
    } finally {
      setLoadingRdevs(false)
    }
  }, [])

  // Fetch rdevs when modal opens and rdev type is selected
  useEffect(() => {
    if (open && workerType === 'rdev') {
      fetchRdevs()
    }
  }, [open, workerType, fetchRdevs])

  // Update mpPath when name changes (for local workers)
  useEffect(() => {
    if (workerType === 'local' && name) {
      setMpPath(`${WORKER_BASE_DIR}/${name}`)
    }
  }, [name, workerType])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setError('')
    setCreating(true)

    try {
      const payload: Record<string, unknown> = { name: name.trim() }

      if (workerType === 'rdev') {
        if (!selectedRdev) return
        payload.host = selectedRdev
      } else {
        if (!host.trim()) return
        payload.host = host.trim()
        payload.mp_path = mpPath.trim() || null
      }

      await api('/api/sessions', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      // Reset with new defaults for next use
      setName(generateWorkerName())
      setSelectedRdev('')
      setHost('localhost')
      setMpPath('')
      onClose()
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create session')
    } finally {
      setCreating(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Add New Worker">
      <form onSubmit={handleSubmit} data-testid="add-session-form">
        <div className="modal-body">
          <div className="form-group">
            <label>Worker Type</label>
            <div className="toggle-group" data-testid="worker-type-toggle">
              <button
                type="button"
                className={`toggle-btn${workerType === 'rdev' ? ' active' : ''}`}
                onClick={() => setWorkerType('rdev')}
              >
                rdev VM
              </button>
              <button
                type="button"
                className={`toggle-btn${workerType === 'local' ? ' active' : ''}`}
                onClick={() => setWorkerType('local')}
              >
                Local
              </button>
            </div>
          </div>

          <div className="form-group">
            <label>Worker Name</label>
            <input
              type="text"
              data-testid="session-name-input"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. api-worker"
              required
            />
          </div>

          {workerType === 'rdev' ? (
            <div className="form-group">
              <div className="rdev-list-header">
                <label>Select rdev Instance</label>
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => fetchRdevs(true)}
                  disabled={loadingRdevs}
                  title="Refresh rdev list"
                >
                  <IconRefresh size={14} className={loadingRdevs ? 'spinning' : ''} />
                </button>
              </div>
              
              {rdevError && (
                <div className="rdev-error">{rdevError}</div>
              )}
              
              {loadingRdevs ? (
                <div className="rdev-loading">Loading rdev instances...</div>
              ) : rdevs.length === 0 ? (
                <div className="rdev-empty">No rdev instances found. Create one with <code>rdev create</code></div>
              ) : (
                <div className="rdev-list">
                  {rdevs.map(rdev => (
                    <div
                      key={rdev.name}
                      className={`rdev-item ${selectedRdev === rdev.name ? 'selected' : ''} ${rdev.in_use ? 'in-use' : ''} ${rdev.state !== 'RUNNING' ? 'not-running' : ''}`}
                      onClick={() => {
                        if (!rdev.in_use && rdev.state === 'RUNNING') {
                          setSelectedRdev(rdev.name)
                          // Auto-set worker name to rdev name
                          setName(rdev.name)
                        }
                      }}
                    >
                      <div className="rdev-item-main">
                        <span className={`rdev-state ${rdev.state.toLowerCase()}`}>{rdev.state}</span>
                        <span className="rdev-name">{rdev.name}</span>
                      </div>
                      <div className="rdev-item-meta">
                        {rdev.in_use ? (
                          <span className="rdev-in-use">Worker: {rdev.worker_name}</span>
                        ) : (
                          <span className="rdev-accessed">{rdev.last_accessed}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="form-group">
                <label>Host</label>
                <input
                  type="text"
                  data-testid="session-host-input"
                  value={host}
                  onChange={e => setHost(e.target.value)}
                  placeholder="localhost"
                  required
                />
              </div>
              <div className="form-group">
                <label>Working Directory</label>
                <input
                  type="text"
                  data-testid="session-path-input"
                  value={mpPath}
                  onChange={e => setMpPath(e.target.value)}
                  placeholder="e.g. /src/my-project (optional)"
                />
              </div>
            </>
          )}

          {error && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 8 }}>{error}</div>}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button
            type="submit"
            className="btn btn-primary"
            data-testid="create-session-btn"
            disabled={creating}
          >
            {creating ? 'Creating...' : 'Create Worker'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
