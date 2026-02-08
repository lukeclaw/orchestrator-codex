import { useState } from 'react'
import Modal from '../common/Modal'
import { api } from '../../api/client'
import { useApp } from '../../context/AppContext'

interface Props {
  open: boolean
  onClose: () => void
}

export default function AddSessionModal({ open, onClose }: Props) {
  const { refresh } = useApp()
  const [name, setName] = useState('')
  const [workerType, setWorkerType] = useState<'rdev' | 'local'>('rdev')
  const [rdevSession, setRdevSession] = useState('')
  const [host, setHost] = useState('')
  const [mpPath, setMpPath] = useState('')
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setError('')
    setCreating(true)

    try {
      const payload: Record<string, unknown> = { name: name.trim() }

      if (workerType === 'rdev') {
        if (!rdevSession.trim()) return
        payload.host = rdevSession.trim()
      } else {
        if (!host.trim()) return
        payload.host = host.trim()
        payload.mp_path = mpPath.trim() || null
      }

      await api('/api/sessions', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setName('')
      setRdevSession('')
      setHost('')
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
              <label>rdev Session</label>
              <input
                type="text"
                data-testid="session-host-input"
                value={rdevSession}
                onChange={e => setRdevSession(e.target.value)}
                placeholder="e.g. subs-mt/sleepy-franklin"
                required
              />
              <small style={{ color: 'var(--text-muted)', fontSize: 11, marginTop: 4, display: 'block' }}>
                Format: MP_NAME/SESSION_NAME — use <code>rdev list</code> to find sessions
              </small>
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
