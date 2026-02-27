import { useState, useEffect } from 'react'
import Modal from '../common/Modal'
import { api } from '../../api/client'
import './CreateRdevModal.css'

interface Props {
  open: boolean
  onClose: () => void
  onCreate: (jobId: string) => void
}

export default function CreateRdevModal({ open, onClose, onCreate }: Props) {
  const [mpName, setMpName] = useState('')
  const [rdevName, setRdevName] = useState('')
  const [branch, setBranch] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) {
      setMpName('')
      setRdevName('')
      setBranch('')
      setError('')
    }
  }, [open])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!mpName.trim()) {
      setError('Multiproduct name is required')
      return
    }

    const payload: Record<string, string> = {
      mp_name: mpName.trim(),
    }
    if (rdevName.trim()) {
      payload.rdev_name = rdevName.trim()
    }
    if (branch.trim()) {
      payload.branch = branch.trim()
    }

    try {
      const resp = await api<{ job_id: string }>('/api/rdevs', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      onCreate(resp.job_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create rdev')
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Create New Rdev">
      <form onSubmit={handleSubmit}>
        <div className="modal-body">
          <div className="form-group">
            <label>
              Multiproduct Name <span className="field-required">*required</span>
            </label>
            <input
              type="text"
              value={mpName}
              onChange={e => setMpName(e.target.value)}
              placeholder="e.g. subs-mt, voyager-web"
              autoFocus
            />
            <div className="field-hint">
              The multiproduct to create the rdev for
            </div>
          </div>

          <div className="form-group">
            <label>
              Rdev Name <span className="field-optional">(optional)</span>
            </label>
            <input
              type="text"
              value={rdevName}
              onChange={e => setRdevName(e.target.value)}
              placeholder="Auto-generated if empty"
            />
            <div className="field-hint">
              Custom name for the rdev (e.g. my-feature-branch)
            </div>
          </div>

          <div className="form-group">
            <label>
              Branch <span className="field-optional">(optional)</span>
            </label>
            <input
              type="text"
              value={branch}
              onChange={e => setBranch(e.target.value)}
              placeholder="Defaults to HEAD"
            />
            <div className="field-hint">
              Checkout a specific branch instead of HEAD
            </div>
          </div>

          {error && (
            <div className="form-error">{error}</div>
          )}

          <div className="create-warning">
            Creating an rdev may take up to 2 minutes. Please be patient.
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={!mpName.trim()}
          >
            Create Rdev
          </button>
        </div>
      </form>
    </Modal>
  )
}
