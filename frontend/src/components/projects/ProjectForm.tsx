import { useState } from 'react'
import Modal from '../common/Modal'

interface Props {
  open: boolean
  onClose: () => void
  onSubmit: (data: { name: string; description?: string; target_date?: string; task_prefix?: string }) => Promise<unknown>
  initial?: { name: string; description?: string; target_date?: string }
  title?: string
}

// Generate a 3-letter prefix from project name
function generatePrefix(name: string): string {
  const words = name.trim().split(/[\s\-_]+/).filter(w => w)
  if (!words.length) return 'TSK'
  if (words.length >= 3) return words.slice(0, 3).map(w => w[0]).join('').toUpperCase()
  if (words.length === 2) return words.map(w => w[0]).join('').toUpperCase()
  return words[0].slice(0, 3).toUpperCase()
}

export default function ProjectForm({ open, onClose, onSubmit, initial, title = 'New Project' }: Props) {
  const [name, setName] = useState(initial?.name || '')
  const [description, setDescription] = useState(initial?.description || '')
  const [targetDate, setTargetDate] = useState(initial?.target_date || '')
  const [taskPrefix, setTaskPrefix] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  
  // Auto-generate prefix when name changes
  const handleNameChange = (newName: string) => {
    setName(newName)
    // Only auto-generate if user hasn't manually edited the prefix
    if (!taskPrefix || taskPrefix === generatePrefix(name)) {
      setTaskPrefix(generatePrefix(newName))
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setError('')
    setSubmitting(true)
    try {
      await onSubmit({
        name: name.trim(),
        description: description.trim() || undefined,
        target_date: targetDate || undefined,
        task_prefix: taskPrefix.toUpperCase() || undefined,
      })
      setName('')
      setDescription('')
      setTargetDate('')
      setTaskPrefix('')
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <form onSubmit={handleSubmit}>
        <div className="modal-body">
          <div className="form-group">
            <label>Project Name</label>
            <input
              type="text"
              value={name}
              onChange={e => handleNameChange(e.target.value)}
              placeholder="e.g. Auth Migration"
              required
            />
          </div>
          <div className="form-group">
            <label>Task Prefix</label>
            <input
              type="text"
              value={taskPrefix}
              onChange={e => setTaskPrefix(e.target.value.toUpperCase().replace(/[^A-Z]/g, '').slice(0, 5))}
              placeholder="e.g. AM"
              maxLength={5}
              style={{ width: 100 }}
            />
            <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>
              Task keys will be: {taskPrefix || 'XXX'}-1, {taskPrefix || 'XXX'}-2, ...
            </span>
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
            <label>Target Date</label>
            <input
              type="date"
              value={targetDate}
              onChange={e => setTargetDate(e.target.value)}
            />
          </div>
          {error && <div style={{ color: 'var(--red)', fontSize: 13, marginTop: 8 }}>{error}</div>}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
