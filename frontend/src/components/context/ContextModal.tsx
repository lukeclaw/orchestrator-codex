import { useState, useEffect, useRef } from 'react'
import Modal from '../common/Modal'
import ConfirmPopover from '../common/ConfirmPopover'
import Markdown from '../common/Markdown'
import type { ContextItem } from '../../api/types'
import './ContextModal.css'

interface Props {
  context: ContextItem | null
  projectId?: string
  isNew?: boolean
  onClose: () => void
  onSave: (body: Partial<ContextItem> & { title: string; content: string }) => Promise<unknown>
  onDelete?: (id: string) => Promise<unknown>
}

const CATEGORY_OPTIONS = [
  { value: '', label: 'No category' },
  { value: 'instruction', label: 'Instruction' },
  { value: 'requirement', label: 'Requirement' },
  { value: 'convention', label: 'Convention' },
  { value: 'reference', label: 'Reference' },
  { value: 'note', label: 'Note' },
]

export default function ContextModal({ context, projectId, isNew, onClose, onSave, onDelete }: Props) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')
  const [category, setCategory] = useState('')
  const [scope, setScope] = useState<'global' | 'project' | 'brain'>(projectId ? 'project' : 'global')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [viewMode, setViewMode] = useState<'edit' | 'preview'>('edit')
  
  // Track which context ID we've initialized form with to avoid re-syncing on data refresh
  const initializedContextId = useRef<string | null>(null)
  // Track if we've initialized for "new" mode
  const initializedAsNew = useRef(false)

  // If projectId is provided, scope is locked to project
  const scopeLocked = !!projectId

  useEffect(() => {
    // Only sync form fields when opening modal with a NEW context (different ID)
    // This prevents background data refresh from overwriting unsaved edits
    if (context && context.id !== initializedContextId.current) {
      setTitle(context.title)
      setDescription(context.description || '')
      setContent(context.content || '')
      setCategory(context.category || '')
      setScope(context.scope as 'global' | 'project' | 'brain')
      setViewMode('preview')
      initializedContextId.current = context.id
      initializedAsNew.current = false
    } else if (isNew && !initializedAsNew.current) {
      setTitle('')
      setDescription('')
      setContent('')
      setCategory('')
      setScope(projectId ? 'project' : 'global')
      setViewMode('edit')
      initializedContextId.current = null
      initializedAsNew.current = true
    } else if (!context && !isNew) {
      // Reset tracking when modal closes
      initializedContextId.current = null
      initializedAsNew.current = false
    }
  }, [context, isNew, projectId])

  const isOpen = !!context || !!isNew

  const handleSave = async () => {
    if (!title.trim() || !content.trim()) return
    setSaving(true)
    try {
      await onSave({
        id: context?.id,
        title: title.trim(),
        description: description.trim() || null,
        content: content.trim(),
        category: category || null,
        scope: scopeLocked ? 'project' : scope,
        project_id: scopeLocked ? projectId : (scope === 'project' ? context?.project_id : undefined),
        source: 'user',
      })
      onClose()
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!context || !onDelete) return
    setDeleting(true)
    try {
      await onDelete(context.id)
      onClose()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal open={isOpen} onClose={onClose} title={isNew ? 'Add Context' : 'Context Details'} wide closeOnOutsideClick={false}>
      <div className="modal-body context-modal-body">
        <div className="form-group">
          <label>Title</label>
          <input
            type="text"
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="Context title..."
            required
          />
        </div>

        <div className="form-group">
          <label>Description <span style={{ color: 'var(--text-muted)', fontWeight: 'normal' }}>(brief summary for list view)</span></label>
          <input
            type="text"
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Brief description of the content..."
          />
        </div>

        <div className="form-group">
          <label>Category</label>
          <select
            className="filter-select"
            value={category}
            onChange={e => setCategory(e.target.value)}
          >
            {CATEGORY_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {!scopeLocked && (
          <div className="form-group">
            <label>Scope</label>
            <select
              className="filter-select"
              value={scope}
              onChange={e => setScope(e.target.value as 'global' | 'project' | 'brain')}
            >
              <option value="global">Global (brain + workers)</option>
              <option value="brain">Brain only</option>
              <option value="project">Project</option>
            </select>
          </div>
        )}

        <div className="form-group cm-content-group">
          <div className="cm-content-header">
            <label>Content</label>
            <div className="toggle-group toggle-sm">
              <button
                type="button"
                className={`toggle-btn ${viewMode === 'edit' ? 'active' : ''}`}
                onClick={() => setViewMode('edit')}
              >
                Edit
              </button>
              <button
                type="button"
                className={`toggle-btn ${viewMode === 'preview' ? 'active' : ''}`}
                onClick={() => setViewMode('preview')}
              >
                Preview
              </button>
            </div>
          </div>
          
          {viewMode === 'edit' ? (
            <textarea
              value={content}
              onChange={e => setContent(e.target.value)}
              placeholder="Enter context content (supports markdown)..."
              rows={12}
              required
            />
          ) : (
            <div className="cm-preview">
              {content ? (
                <Markdown>{content}</Markdown>
              ) : (
                <em>No content</em>
              )}
            </div>
          )}
        </div>

        {context && (
          <div className="cm-meta">
            <span>Created: {new Date(context.created_at).toLocaleDateString()}</span>
            {context.updated_at && (
              <span>Updated: {new Date(context.updated_at).toLocaleDateString()}</span>
            )}
            {context.source && <span>Source: {context.source}</span>}
          </div>
        )}
      </div>

      <div className="modal-footer">
        {context && onDelete && (
          <ConfirmPopover
            message={`Delete context "${context.title}"?`}
            confirmLabel="Delete"
            onConfirm={handleDelete}
            variant="danger"
          >
            {({ onClick }) => (
              <button
                type="button"
                className="btn btn-danger"
                onClick={onClick}
                disabled={deleting}
              >
                {deleting ? 'Deleting...' : 'Delete'}
              </button>
            )}
          </ConfirmPopover>
        )}
        <div className="cm-footer-spacer" />
        <button type="button" className="btn btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving || !title.trim() || !content.trim()}
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </Modal>
  )
}
