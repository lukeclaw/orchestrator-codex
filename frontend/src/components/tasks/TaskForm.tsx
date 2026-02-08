import { useState } from 'react'
import Modal from '../common/Modal'
import type { Project } from '../../api/types'

interface Props {
  open: boolean
  onClose: () => void
  onSubmit: (data: { project_id: string; title: string; description?: string; priority?: number }) => Promise<unknown>
  projects: Project[]
  defaultProjectId?: string
}

export default function TaskForm({ open, onClose, onSubmit, projects, defaultProjectId }: Props) {
  const [projectId, setProjectId] = useState(defaultProjectId || '')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState(3)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!projectId || !title.trim()) return
    setError('')
    setSubmitting(true)
    try {
      await onSubmit({
        project_id: projectId,
        title: title.trim(),
        description: description.trim() || undefined,
        priority,
      })
      setTitle('')
      setDescription('')
      setPriority(3)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create task')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New Task">
      <form onSubmit={handleSubmit}>
        <div className="modal-body">
          <div className="form-group">
            <label>Project</label>
            <select
              className="filter-select"
              style={{ width: '100%', padding: '8px 12px', fontSize: 14 }}
              value={projectId}
              onChange={e => setProjectId(e.target.value)}
              required
            >
              <option value="">Select a project...</option>
              {projects.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Title</label>
            <input
              type="text"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="e.g. Implement login endpoint"
              required
            />
          </div>
          <div className="form-group">
            <label>Description</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Optional description..."
              rows={3}
            />
          </div>
          <div className="form-group">
            <label>Priority (1=low, 5=critical)</label>
            <input
              type="number"
              min={1}
              max={5}
              value={priority}
              onChange={e => setPriority(Number(e.target.value))}
            />
          </div>
          {error && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 8 }}>{error}</div>}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? 'Creating...' : 'Create Task'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
