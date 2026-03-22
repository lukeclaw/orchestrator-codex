import { useState, useEffect, useRef, useMemo } from 'react'
import ConfirmPopover from '../common/ConfirmPopover'
import TagDropdown, { type TagOption } from '../common/TagDropdown'
import Markdown from '../common/Markdown'
import type { ContextItem, Project } from '../../api/types'
import { parseDate } from '../common/TimeAgo'
import './ContextModal.css'

interface InitialContent {
  title?: string
  content?: string
  description?: string
  category?: string
}

interface Props {
  context: ContextItem | null
  projectId?: string
  projects?: Project[]
  isNew?: boolean
  readOnly?: boolean
  initialContent?: InitialContent
  onClose: () => void
  onSave: (body: Partial<ContextItem> & { title: string; content: string }) => Promise<unknown>
  onDelete?: (id: string) => Promise<unknown>
}

const CATEGORY_OPTIONS = [
  { value: '', label: 'No category', className: 'cm-cat-none' },
  { value: 'instruction', label: 'Instruction', className: 'cm-cat-instruction' },
  { value: 'reference', label: 'Reference', className: 'cm-cat-reference' },
]

export default function ContextModal({ context, projectId, projects = [], isNew, readOnly, initialContent, onClose, onSave, onDelete }: Props) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')
  const [category, setCategory] = useState('')
  const [scope, setScope] = useState<'global' | 'project' | 'brain'>(projectId ? 'project' : 'global')
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null)

  // Build scope options with projects as children under "Project"
  const scopeOptions: TagOption[] = useMemo(() => {
    const projectChildren = projects.map(p => ({
      value: `project:${p.id}`,
      label: p.name,
      className: 'cm-scope-project-item',
    }))
    return [
      { value: 'global', label: 'Global', className: 'cm-scope-global' },
      { value: 'brain', label: 'Brain', className: 'cm-scope-brain' },
      {
        value: 'project',
        label: 'Project',
        className: 'cm-scope-project',
        children: projectChildren.length > 0 ? projectChildren : undefined,
      },
    ]
  }, [projects])
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [viewMode, setViewMode] = useState<'edit' | 'preview'>('edit')
  const [editingField, setEditingField] = useState<string | null>(null)

  const initializedContextId = useRef<string | null>(null)
  const initializedAsNew = useRef(false)

  const scopeLocked = !!projectId

  useEffect(() => {
    if (context && context.id !== initializedContextId.current) {
      setTitle(context.title)
      setDescription(context.description || '')
      setContent(context.content || '')
      setCategory(context.category || '')
      setScope(context.scope as 'global' | 'project' | 'brain')
      setSelectedProjectId(context.project_id)
      setViewMode('preview')
      setEditingField(null)
      initializedContextId.current = context.id
      initializedAsNew.current = false
    } else if (isNew && !initializedAsNew.current) {
      setTitle(initialContent?.title || '')
      setDescription(initialContent?.description || '')
      setContent(initialContent?.content || '')
      setCategory(initialContent?.category || '')
      setScope(projectId ? 'project' : 'global')
      setSelectedProjectId(projectId || null)
      setViewMode('preview')
      setEditingField(null)
      initializedContextId.current = null
      initializedAsNew.current = true
    } else if (!context && !isNew) {
      initializedContextId.current = null
      initializedAsNew.current = false
    }
  }, [context, isNew, projectId])

  useEffect(() => {
    const isOpen = !!context || !!isNew
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [context, isNew, onClose])

  const isOpen = !!context || !!isNew
  if (!isOpen) return null

  // Build the full save body from current state
  const buildBody = () => ({
    id: context?.id,
    title: title.trim(),
    description: description.trim() || null,
    content: content.trim(),
    category: category || null,
    scope: (scopeLocked ? 'project' : scope) as string,
    project_id: scopeLocked ? projectId : (scope === 'project' ? selectedProjectId : null),
    source: 'user' as const,
  })

  // Save current state (used by per-field saves on existing context)
  const saveField = async () => {
    if (!title.trim() || !content.trim()) return
    setSaving(true)
    try {
      await onSave(buildBody() as Parameters<typeof onSave>[0])
    } finally {
      setSaving(false)
    }
  }

  // Create new context (footer button for isNew mode)
  const handleCreate = async () => {
    if (!title.trim() || !content.trim()) return
    setSaving(true)
    try {
      await onSave(buildBody() as Parameters<typeof onSave>[0])
      onClose()
    } finally {
      setSaving(false)
    }
  }

  // --- Per-field save handlers ---
  const handleTitleSave = async () => {
    if (!title.trim() || title.trim() === (context?.title || '')) {
      setTitle(context?.title || '')
      setEditingField(null)
      return
    }
    await saveField()
    setEditingField(null)
  }

  const handleTitleDiscard = () => {
    setTitle(context?.title || '')
    setEditingField(null)
  }

  const handleDescSave = async () => {
    if (description.trim() === (context?.description || '')) {
      setEditingField(null)
      return
    }
    await saveField()
    setEditingField(null)
  }

  const handleDescDiscard = () => {
    setDescription(context?.description || '')
    setEditingField(null)
  }

  const handleContentSave = async () => {
    if (!content.trim() || content.trim() === (context?.content || '')) {
      setViewMode('preview')
      return
    }
    await saveField()
    setViewMode('preview')
  }

  const handleContentDiscard = () => {
    setContent(context?.content || '')
    setViewMode('preview')
  }

  // TagDropdown change auto-saves for existing context
  const handleCategoryChange = async (val: string) => {
    setCategory(val)
    if (context) {
      // Need to use the new value directly since setState is async
      setSaving(true)
      try {
        await onSave({ ...buildBody(), category: val || null } as Parameters<typeof onSave>[0])
      } finally {
        setSaving(false)
      }
    }
  }

  const handleScopeChange = async (val: string) => {
    // Parse project:id format from nested submenu
    let newScope: 'global' | 'project' | 'brain'
    let newProjectId: string | null = null

    if (val.startsWith('project:')) {
      newScope = 'project'
      newProjectId = val.slice('project:'.length)
    } else {
      newScope = val as 'global' | 'project' | 'brain'
      if (newScope !== 'project') {
        newProjectId = null
      }
    }

    setScope(newScope)
    setSelectedProjectId(newProjectId)

    if (context) {
      setSaving(true)
      try {
        await onSave({
          ...buildBody(),
          scope: newScope,
          project_id: newProjectId,
        } as Parameters<typeof onSave>[0])
      } finally {
        setSaving(false)
      }
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

  const titleChanged = title.trim() !== (context?.title || '') && !!title.trim()
  const descChanged = description.trim() !== (context?.description || '')
  const contentChanged = content.trim() !== (context?.content || '') && !!content.trim()

  return (
    <div className="modal-backdrop">
      <div className="modal-content modal-extra-wide cm-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="cm-header">
          <div className="cm-header-top">
            <span className="cm-label">Context</span>
            {editingField === 'title' ? (
              <div className="cm-inline-edit">
                <input
                  className="cm-title-input"
                  type="text"
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  placeholder="Context title..."
                  autoFocus
                  onBlur={() => {
                    if (isNew || !titleChanged) setEditingField(null)
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') { isNew ? setEditingField(null) : handleTitleSave() }
                    if (e.key === 'Escape') { isNew ? setEditingField(null) : handleTitleDiscard() }
                  }}
                />
                {!isNew && (
                  <div className="cm-inline-actions">
                    <button
                      className="cm-action-btn save"
                      onClick={handleTitleSave}
                      disabled={!titleChanged || saving}
                      title="Save"
                    >✓</button>
                    <button
                      className="cm-action-btn cancel"
                      onClick={handleTitleDiscard}
                      title="Discard"
                    >✕</button>
                  </div>
                )}
              </div>
            ) : (
              <span
                className={`cm-title ${readOnly ? '' : 'editable'} ${!title ? 'empty' : ''}`}
                onClick={() => !readOnly && setEditingField('title')}
              >
                {title || (readOnly ? 'Untitled' : 'Click to add title...')}
              </span>
            )}
            <TagDropdown
              value={category}
              options={CATEGORY_OPTIONS}
              onChange={handleCategoryChange}
              disabled={readOnly}
              renderTag={(opt) => (
                <span className={`cm-badge ${opt.className}`}>{opt.label}</span>
              )}
            />
            {!scopeLocked && !readOnly && (
              <TagDropdown
                value={scope === 'project' && selectedProjectId ? `project:${selectedProjectId}` : scope}
                options={scopeOptions}
                onChange={handleScopeChange}
                renderTag={(opt) => (
                  <span className={`cm-badge ${opt.className}`}>{opt.label}</span>
                )}
              />
            )}
            <div className="cm-header-spacer" />
            <button className="modal-close" onClick={onClose}>&times;</button>
          </div>
          <div className="cm-header-desc">
            {editingField === 'desc' ? (
              <div className="cm-inline-edit">
                <input
                  className="cm-desc-input"
                  type="text"
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  placeholder="Add a brief description..."
                  autoFocus
                  onBlur={() => {
                    if (isNew || !descChanged) setEditingField(null)
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') { isNew ? setEditingField(null) : handleDescSave() }
                    if (e.key === 'Escape') { isNew ? setEditingField(null) : handleDescDiscard() }
                  }}
                />
                {!isNew && (
                  <div className="cm-inline-actions">
                    <button
                      className="cm-action-btn save"
                      onClick={handleDescSave}
                      disabled={!descChanged || saving}
                      title="Save"
                    >✓</button>
                    <button
                      className="cm-action-btn cancel"
                      onClick={handleDescDiscard}
                      title="Discard"
                    >✕</button>
                  </div>
                )}
              </div>
            ) : (
              <span
                className={`cm-desc ${readOnly ? '' : 'editable'} ${!description ? 'empty' : ''}`}
                onClick={() => !readOnly && setEditingField('desc')}
              >
                {description || (readOnly ? '' : 'Click to add description...')}
              </span>
            )}
          </div>
        </div>

        {/* Body: content only */}
        <div className="cm-body">
          <div className="cm-content-header">
            <span className="cm-content-label">Content</span>
            {viewMode === 'edit' ? (
              <div className="cm-inline-actions">
                {!isNew && (
                  <button
                    className="cm-action-btn save"
                    onClick={handleContentSave}
                    disabled={!contentChanged || saving}
                    title="Save"
                  >✓</button>
                )}
                <button
                  className="cm-action-btn cancel"
                  onClick={() => {
                    if (isNew) { setViewMode('preview') }
                    else { handleContentDiscard() }
                  }}
                  title={isNew ? 'Done' : 'Discard'}
                >✕</button>
              </div>
            ) : !readOnly ? (
              <button
                type="button"
                className="cm-edit-btn"
                onClick={() => setViewMode('edit')}
                title="Edit content"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
              </button>
            ) : null}
          </div>

          {viewMode === 'edit' ? (
            <textarea
              className="cm-textarea"
              value={content}
              onChange={e => setContent(e.target.value)}
              placeholder="Enter context content (supports markdown)..."
              autoFocus
            />
          ) : (
            <div className={`cm-preview ${!content ? 'empty' : ''}`} onClick={() => !content && setViewMode('edit')}>
              {content ? (
                <Markdown>{content}</Markdown>
              ) : (
                <span className="cm-preview-placeholder">Click to add content...</span>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
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
          {context && (
            <div className="cm-meta">
              <span>{parseDate(context.created_at).toLocaleDateString()}</span>
              {context.updated_at && (
                <span>· {parseDate(context.updated_at).toLocaleDateString()}</span>
              )}
              {context.source && <span>· {context.source}</span>}
            </div>
          )}
          <div className="cm-footer-spacer" />
          {isNew ? (
            <>
              <button type="button" className="btn btn-secondary" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleCreate}
                disabled={saving || !title.trim() || !content.trim()}
              >
                {saving ? 'Creating...' : 'Create'}
              </button>
            </>
          ) : (
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
