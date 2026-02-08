import { useState, useEffect } from 'react'
import Modal from '../common/Modal'
import ConfirmPopover from '../common/ConfirmPopover'
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
  { value: 'requirement', label: 'Requirement' },
  { value: 'convention', label: 'Convention' },
  { value: 'reference', label: 'Reference' },
  { value: 'note', label: 'Note' },
]

export default function ContextModal({ context, projectId, isNew, onClose, onSave, onDelete }: Props) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [category, setCategory] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [viewMode, setViewMode] = useState<'edit' | 'preview'>('edit')

  useEffect(() => {
    if (context) {
      setTitle(context.title)
      setContent(context.content)
      setCategory(context.category || '')
      setViewMode('preview')
    } else if (isNew) {
      setTitle('')
      setContent('')
      setCategory('')
      setViewMode('edit')
    }
  }, [context, isNew])

  const isOpen = !!context || !!isNew

  const handleSave = async () => {
    if (!title.trim() || !content.trim()) return
    setSaving(true)
    try {
      await onSave({
        id: context?.id,
        title: title.trim(),
        content: content.trim(),
        category: category || null,
        scope: 'project',
        project_id: projectId,
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

  // Simple markdown to HTML conversion for preview
  const renderMarkdown = (text: string) => {
    let html = text
      // Escape HTML
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      // Headers
      .replace(/^### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^## (.+)$/gm, '<h3>$1</h3>')
      .replace(/^# (.+)$/gm, '<h2>$1</h2>')
      // Bold and italic
      .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      // Inline code
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // Links
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      // Line breaks
      .replace(/\n/g, '<br />')
      // Horizontal rule
      .replace(/^---$/gm, '<hr />')
    
    return html
  }

  return (
    <Modal open={isOpen} onClose={onClose} title={isNew ? 'Add Context' : 'Context Details'} wide>
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
            <div 
              className="cm-preview"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(content) || '<em>No content</em>' }}
            />
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
