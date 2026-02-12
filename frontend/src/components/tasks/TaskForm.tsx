import { useState, useEffect } from 'react'
import Modal from '../common/Modal'
import type { Project } from '../../api/types'

interface Props {
  open: boolean
  onClose: () => void
  onSubmit: (data: { project_id: string; title: string; description?: string; priority?: string }) => Promise<unknown>
  projects: Project[]
  defaultProjectId?: string
}

const PRIORITY_OPTIONS = [
  { value: 'H', label: 'High' },
  { value: 'M', label: 'Medium' },
  { value: 'L', label: 'Low' },
]

export default function TaskForm({ open, onClose, onSubmit, projects, defaultProjectId }: Props) {
  const [projectId, setProjectId] = useState(defaultProjectId || '')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState('M')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Update projectId when defaultProjectId changes (e.g., modal reopens for different project)
  useEffect(() => {
    if (defaultProjectId) {
      setProjectId(defaultProjectId)
    }
  }, [defaultProjectId])

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
      setPriority('M')
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
            {defaultProjectId ? (
              <input
                type="text"
                value={projects.find(p => p.id === defaultProjectId)?.name || ''}
                disabled
                style={{ background: 'var(--surface)', color: 'var(--text-secondary)' }}
              />
            ) : (
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
            )}
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
            <label>Priority</label>
            <select
              className="filter-select"
              style={{ width: '100%', padding: '8px 12px', fontSize: 14 }}
              value={priority}
              onChange={e => setPriority(e.target.value)}
            >
              {PRIORITY_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
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
