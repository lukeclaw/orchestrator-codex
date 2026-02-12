import { useState, useEffect, useRef } from 'react'
import type { Project } from '../../api/types'
import Modal from '../common/Modal'
import ConfirmPopover from '../common/ConfirmPopover'
import './ProjectEditModal.css'

interface Props {
  project: Project | null
  onClose: () => void
  onUpdate: (projectId: string, data: { name?: string; description?: string; status?: string; target_date?: string }) => Promise<void>
  onDelete: (projectId: string) => Promise<void>
}

export default function ProjectEditModal({ project, onClose, onUpdate, onDelete }: Props) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [status, setStatus] = useState('active')
  const [targetDate, setTargetDate] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  
  // Track which project ID we've initialized form with to avoid re-syncing on data refresh
  const initializedProjectId = useRef<string | null>(null)

  useEffect(() => {
    // Only sync form fields when opening modal with a NEW project (different ID)
    // This prevents background data refresh from overwriting unsaved edits
    if (project && project.id !== initializedProjectId.current) {
      setName(project.name)
      setDescription(project.description || '')
      setStatus(project.status)
      setTargetDate(project.target_date || '')
      setError('')
      initializedProjectId.current = project.id
    } else if (!project) {
      // Reset tracking when modal closes
      initializedProjectId.current = null
    }
  }, [project])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!project || !name.trim()) return
    setError('')
    setSubmitting(true)
    try {
      await onUpdate(project.id, {
        name: name.trim(),
        description: description.trim() || undefined,
        status,
        target_date: targetDate || undefined,
      })
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete() {
    if (!project) return
    setSubmitting(true)
    try {
      await onDelete(project.id)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete')
      setSubmitting(false)
    }
  }

  if (!project) return null

  return (
    <Modal open={!!project} onClose={onClose} title="Edit Project">
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
          <div className="form-row">
            <div className="form-group">
              <label>Status</label>
              <select value={status} onChange={e => setStatus(e.target.value)}>
                <option value="active">Active</option>
                <option value="paused">Paused</option>
                <option value="completed">Completed</option>
              </select>
            </div>
            <div className="form-group">
              <label>Target Date</label>
              <input
                type="date"
                value={targetDate}
                onChange={e => setTargetDate(e.target.value)}
              />
            </div>
          </div>
          {error && <div className="pem-error">{error}</div>}
        </div>
        <div className="modal-footer pem-footer">
          <div className="pem-delete-area">
            <ConfirmPopover
              onConfirm={handleDelete}
              message="Delete this project and all its tasks, subtasks, and context?"
              confirmLabel="Delete All"
              variant="danger"
            >
              {({ onClick }) => (
                <button
                  type="button"
                  className="btn btn-danger"
                  onClick={onClick}
                  disabled={submitting}
                >
                  Delete Project
                </button>
              )}
            </ConfirmPopover>
          </div>
          <div className="pem-actions">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  )
}
