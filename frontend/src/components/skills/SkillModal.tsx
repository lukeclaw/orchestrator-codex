import { useState, useEffect, useRef } from 'react'
import ConfirmPopover from '../common/ConfirmPopover'
import Markdown from '../common/Markdown'
import type { Skill } from '../../api/types'
import { parseDate } from '../common/TimeAgo'
import './SkillModal.css'

interface Props {
  skill: Skill | null
  isNew?: boolean
  defaultTarget?: string
  onClose: () => void
  onSave: (body: { id?: string; name: string; target: string; content: string; description?: string }) => Promise<unknown>
  onDelete?: (id: string) => Promise<unknown>
}

const STARTER_TEMPLATE = `# Skill Name

Describe what this skill does and when to use it.

---

## Steps

1. First step
2. Second step
3. Third step
`

export default function SkillModal({ skill, isNew, defaultTarget, onClose, onSave, onDelete }: Props) {
  const [name, setName] = useState('')
  const [target, setTarget] = useState<string>(defaultTarget || 'worker')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')
  const [viewMode, setViewMode] = useState<'edit' | 'preview'>('preview')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)

  const initializedId = useRef<string | null>(null)
  const initializedAsNew = useRef(false)

  const isBuiltIn = skill?.type === 'built_in'
  const isCustom = skill?.type === 'custom'

  useEffect(() => {
    if (skill && skill.id !== initializedId.current) {
      setName(skill.name)
      setTarget(skill.target)
      setDescription(skill.description || '')
      setContent(skill.content || '')
      setViewMode('preview')
      setNameError(null)
      initializedId.current = skill.id
      initializedAsNew.current = false
    } else if (isNew && !initializedAsNew.current) {
      setName('')
      setTarget(defaultTarget || 'worker')
      setDescription('')
      setContent(STARTER_TEMPLATE)
      setViewMode('edit')
      setNameError(null)
      initializedId.current = null
      initializedAsNew.current = true
    } else if (!skill && !isNew) {
      initializedId.current = null
      initializedAsNew.current = false
    }
  }, [skill, isNew, defaultTarget])

  useEffect(() => {
    const isOpen = !!skill || !!isNew
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [skill, isNew, onClose])

  const isOpen = !!skill || !!isNew
  if (!isOpen) return null

  function validateName(value: string): string | null {
    if (!value) return 'Name is required'
    if (!/^[a-z][a-z0-9-]*$/.test(value)) return 'Must start with a letter, use only lowercase letters, digits, and hyphens'
    if (value.length > 50) return 'Must be 50 characters or less'
    return null
  }

  function handleNameChange(value: string) {
    setName(value)
    setNameError(validateName(value))
  }

  async function handleSave() {
    const error = validateName(name)
    if (error) {
      setNameError(error)
      return
    }
    if (!content.trim()) return

    setSaving(true)
    try {
      await onSave({
        id: isCustom ? skill?.id : undefined,
        name: name.trim(),
        target,
        content: content.trim(),
        description: description.trim() || undefined,
      })
      onClose()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      // Surface API errors (e.g., name conflict) as name field errors
      if (msg.includes('400') || msg.includes('conflicts') || msg.includes('Name')) {
        setNameError(msg.replace(/^API \d+: /, '').replace(/^"/, '').replace(/"$/, ''))
      }
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!skill || !onDelete) return
    setDeleting(true)
    try {
      await onDelete(skill.id)
      onClose()
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content modal-extra-wide sk-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="sk-header">
          <div className="sk-header-top">
            <span className="sk-label">Skill</span>
            {isNew || isCustom ? (
              <div className="sk-name-field">
                <span className="sk-name-prefix">/</span>
                <input
                  className={`sk-name-input ${nameError ? 'error' : ''}`}
                  type="text"
                  value={name}
                  onChange={e => handleNameChange(e.target.value)}
                  placeholder="skill-name"
                  autoFocus={isNew}
                />
                {nameError && <span className="sk-name-error">{nameError}</span>}
              </div>
            ) : (
              <span className="sk-name-display">/{skill?.name}</span>
            )}

            <span className={`sk-badge ${isBuiltIn ? 'built-in' : 'custom'}`}>
              {isBuiltIn ? 'BUILT-IN' : isNew ? 'NEW' : 'CUSTOM'}
            </span>

            {(isNew || isCustom) && (
              <div className="sk-target-toggle">
                <button
                  className={`sk-target-btn ${target === 'brain' ? 'active' : ''}`}
                  onClick={() => setTarget('brain')}
                >Brain</button>
                <button
                  className={`sk-target-btn ${target === 'worker' ? 'active' : ''}`}
                  onClick={() => setTarget('worker')}
                >Worker</button>
              </div>
            )}

            {isBuiltIn && (
              <span className="sk-target-label">{skill?.target}</span>
            )}

            <div className="sk-header-spacer" />
            <button className="modal-close" onClick={onClose}>&times;</button>
          </div>

          {/* Description */}
          <div className="sk-desc-row">
            {isNew || isCustom ? (
              <input
                className="sk-desc-input"
                type="text"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Brief description of what this skill does..."
              />
            ) : (
              <span className={`sk-desc ${!description ? 'empty' : ''}`}>
                {description || 'No description'}
              </span>
            )}
          </div>
        </div>

        {/* Body: content */}
        <div className="sk-body">
          <div className="sk-content-header">
            <span className="sk-content-label">Content</span>
            {!isBuiltIn && (
              <div className="sk-view-toggle">
                <button
                  className={`sk-view-btn ${viewMode === 'edit' ? 'active' : ''}`}
                  onClick={() => setViewMode('edit')}
                >Edit</button>
                <button
                  className={`sk-view-btn ${viewMode === 'preview' ? 'active' : ''}`}
                  onClick={() => setViewMode('preview')}
                >Preview</button>
              </div>
            )}
          </div>

          {(isBuiltIn || viewMode === 'preview') ? (
            <div className={`sk-preview ${!content ? 'empty' : ''}`}>
              {content ? (
                <Markdown>{content}</Markdown>
              ) : (
                <span className="sk-preview-placeholder">No content</span>
              )}
            </div>
          ) : (
            <textarea
              className="sk-textarea"
              value={content}
              onChange={e => setContent(e.target.value)}
              placeholder="Enter skill content (supports markdown)..."
              autoFocus={!isNew}
            />
          )}
        </div>

        {/* Footer */}
        <div className="modal-footer">
          {isCustom && onDelete && (
            <ConfirmPopover
              message={`Delete skill "/${skill?.name}"?`}
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

          {skill && (
            <div className="sk-meta">
              <span>{parseDate(skill.created_at).toLocaleDateString()}</span>
              {skill.line_count > 0 && <span>· {skill.line_count} lines</span>}
            </div>
          )}

          <div className="sk-footer-spacer" />

          {(isNew || isCustom) ? (
            <>
              <button type="button" className="btn btn-secondary" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSave}
                disabled={saving || !name.trim() || !content.trim() || !!nameError}
              >
                {saving ? 'Saving...' : isNew ? 'Create' : 'Save'}
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
