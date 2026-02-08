import { useState } from 'react'
import Modal from '../common/Modal'

interface Props {
  open: boolean
  onClose: () => void
  onSubmit: (data: { name: string; description?: string; target_date?: string }) => Promise<unknown>
  initial?: { name: string; description?: string; target_date?: string }
  title?: string
}

export default function ProjectForm({ open, onClose, onSubmit, initial, title = 'New Project' }: Props) {
  const [name, setName] = useState(initial?.name || '')
  const [description, setDescription] = useState(initial?.description || '')
  const [targetDate, setTargetDate] = useState(initial?.target_date || '')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

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
      })
      setName('')
      setDescription('')
      setTargetDate('')
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
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Auth Migration"
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
