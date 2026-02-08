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
  const [host, setHost] = useState('')
  const [mpPath, setMpPath] = useState('')
  const [error, setError] = useState('')

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !host.trim()) return
    setError('')

    try {
      await api('/api/sessions', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          host: host.trim(),
          mp_path: mpPath.trim() || null,
        }),
      })
      setName('')
      setHost('')
      setMpPath('')
      onClose()
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create session')
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Add New Session">
      <form onSubmit={handleSubmit} data-testid="add-session-form">
        <div className="modal-body">
          <div className="form-group">
            <label>Session Name</label>
            <input
              type="text"
              data-testid="session-name-input"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. worker-alpha"
              required
            />
          </div>
          <div className="form-group">
            <label>Host</label>
            <input
              type="text"
              data-testid="session-host-input"
              value={host}
              onChange={e => setHost(e.target.value)}
              placeholder="e.g. rdev1.example.com or localhost"
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
          {error && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 8 }}>{error}</div>}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" data-testid="create-session-btn">Create Session</button>
        </div>
      </form>
    </Modal>
  )
}
