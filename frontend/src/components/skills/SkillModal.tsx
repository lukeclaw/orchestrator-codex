import { useState, useEffect, useRef } from 'react'
import ConfirmPopover from '../common/ConfirmPopover'
import TagDropdown, { type TagOption } from '../common/TagDropdown'
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

const STARTER_TEMPLATE = `## Steps

1. First step
2. Second step
3. Third step

## Notes

- Additional guidelines or context
`

const TARGET_OPTIONS: TagOption[] = [
  { value: 'brain', label: 'Brain', className: 'sk-target-brain' },
  { value: 'worker', label: 'Worker', className: 'sk-target-worker' },
]

export default function SkillModal({ skill, isNew, defaultTarget, onClose, onSave, onDelete }: Props) {
  const [name, setName] = useState('')
  const [target, setTarget] = useState<string>(defaultTarget || 'worker')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')
  const [viewMode, setViewMode] = useState<'edit' | 'preview'>('preview')
  const [editingField, setEditingField] = useState<string | null>(null)
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
      setEditingField(null)
      setNameError(null)
      initializedId.current = skill.id
      initializedAsNew.current = false
    } else if (isNew && !initializedAsNew.current) {
      setName('')
      setTarget(defaultTarget || 'worker')
      setDescription('')
      setContent(STARTER_TEMPLATE)
      setViewMode('edit')
      setEditingField(null)
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

  // Build the full save body from current state
  const buildBody = (overrides?: Partial<{ name: string; target: string; content: string; description: string }>) => ({
    id: isCustom ? skill?.id : undefined,
    name: (overrides?.name ?? name).trim(),
    target: overrides?.target ?? target,
    content: (overrides?.content ?? content).trim(),
    description: (overrides?.description ?? description).trim() || undefined,
  })

  // Save current state (used by per-field saves on existing custom skills)
  const saveField = async (overrides?: Parameters<typeof buildBody>[0]) => {
    const body = buildBody(overrides)
    if (!body.name || !body.content) return
    setSaving(true)
    try {
      await onSave(body)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      if (msg.includes('400') || msg.includes('conflicts') || msg.includes('Name')) {
        setNameError(msg.replace(/^API \d+: /, '').replace(/^"/, '').replace(/"$/, ''))
      }
    } finally {
      setSaving(false)
    }
  }

  // Create new skill (footer button for isNew mode)
  async function handleCreate() {
    const error = validateName(name)
    if (error) {
      setNameError(error)
      return
    }
    if (!content.trim()) return

    setSaving(true)
    try {
      await onSave(buildBody())
      onClose()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      if (msg.includes('400') || msg.includes('conflicts') || msg.includes('Name')) {
        setNameError(msg.replace(/^API \d+: /, '').replace(/^"/, '').replace(/"$/, ''))
      }
    } finally {
      setSaving(false)
    }
  }

  // --- Per-field save/discard handlers ---
  const handleNameSave = async () => {
    const error = validateName(name)
    if (error) {
      setNameError(error)
      return
    }
    if (name.trim() === (skill?.name || '')) {
      setEditingField(null)
      return
    }
    await saveField()
    setEditingField(null)
  }

  const handleNameDiscard = () => {
    setName(skill?.name || '')
    setNameError(null)
    setEditingField(null)
  }

  const handleDescSave = async () => {
    if (description.trim() === (skill?.description || '')) {
      setEditingField(null)
      return
    }
    await saveField()
    setEditingField(null)
  }

  const handleDescDiscard = () => {
    setDescription(skill?.description || '')
    setEditingField(null)
  }

  const handleContentSave = async () => {
    if (!content.trim() || content.trim() === (skill?.content || '')) {
      setViewMode('preview')
      return
    }
    await saveField()
    setViewMode('preview')
  }

  const handleContentDiscard = () => {
    setContent(skill?.content || '')
    setViewMode('preview')
  }

  // TagDropdown auto-save for target on existing custom skills
  const handleTargetChange = async (val: string) => {
    setTarget(val)
    if (isCustom) {
      setSaving(true)
      try {
        await onSave(buildBody({ target: val }))
      } finally {
        setSaving(false)
      }
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

  const nameChanged = name.trim() !== (skill?.name || '') && !!name.trim()
  const descChanged = description.trim() !== (skill?.description || '')
  const contentChanged = content.trim() !== (skill?.content || '') && !!content.trim()

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content modal-extra-wide sk-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="sk-header">
          <div className="sk-header-top">
            <span className="sk-label">Skill</span>

            {/* Name field */}
            {isNew ? (
              <div className="sk-name-field">
                <span className="sk-name-prefix">/</span>
                <input
                  className={`sk-name-input ${nameError ? 'error' : ''}`}
                  type="text"
                  value={name}
                  onChange={e => handleNameChange(e.target.value)}
                  placeholder="skill-name"
                  autoFocus
                />
                {nameError && <span className="sk-name-error">{nameError}</span>}
              </div>
            ) : isCustom && editingField === 'name' ? (
              <div className="sk-inline-edit">
                <span className="sk-name-prefix">/</span>
                <input
                  className={`sk-name-input ${nameError ? 'error' : ''}`}
                  type="text"
                  value={name}
                  onChange={e => handleNameChange(e.target.value)}
                  autoFocus
                  onKeyDown={e => {
                    if (e.key === 'Enter') handleNameSave()
                    if (e.key === 'Escape') handleNameDiscard()
                  }}
                />
                {nameError && <span className="sk-name-error">{nameError}</span>}
                <div className="sk-inline-actions">
                  <button
                    className="sk-action-btn save"
                    onClick={handleNameSave}
                    disabled={!nameChanged || saving || !!nameError}
                    title="Save"
                  >✓</button>
                  <button
                    className="sk-action-btn cancel"
                    onClick={handleNameDiscard}
                    title="Discard"
                  >✕</button>
                </div>
              </div>
            ) : isCustom ? (
              <span
                className="sk-name-display editable"
                onClick={() => setEditingField('name')}
              >
                /{skill?.name}
              </span>
            ) : (
              <span className="sk-name-display">/{skill?.name}</span>
            )}

            <span className={`sk-badge ${isBuiltIn ? 'built-in' : 'custom'}`}>
              {isBuiltIn ? 'BUILT-IN' : isNew ? 'NEW' : 'CUSTOM'}
            </span>

            {skill && !skill.enabled && (
              <span className="sk-badge sk-badge-disabled">DISABLED</span>
            )}

            {/* Target: TagDropdown for new/custom, static badge for built-in */}
            {(isNew || isCustom) ? (
              <TagDropdown
                value={target}
                options={TARGET_OPTIONS}
                onChange={handleTargetChange}
                renderTag={(opt) => (
                  <span className={`sk-badge ${opt.className}`}>{opt.label}</span>
                )}
              />
            ) : isBuiltIn && (
              <span className="sk-badge sk-target-static">{skill?.target}</span>
            )}

            <div className="sk-header-spacer" />
            <button className="modal-close" onClick={onClose}>&times;</button>
          </div>

          {/* Description */}
          <div className="sk-desc-row">
            {isNew ? (
              <input
                className="sk-desc-input"
                type="text"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Brief description of what this skill does..."
              />
            ) : isCustom && editingField === 'desc' ? (
              <div className="sk-inline-edit">
                <input
                  className="sk-desc-input"
                  type="text"
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  placeholder="Add a brief description..."
                  autoFocus
                  onBlur={() => {
                    if (!descChanged) setEditingField(null)
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') handleDescSave()
                    if (e.key === 'Escape') handleDescDiscard()
                  }}
                />
                <div className="sk-inline-actions">
                  <button
                    className="sk-action-btn save"
                    onClick={handleDescSave}
                    disabled={!descChanged || saving}
                    title="Save"
                  >✓</button>
                  <button
                    className="sk-action-btn cancel"
                    onClick={handleDescDiscard}
                    title="Discard"
                  >✕</button>
                </div>
              </div>
            ) : isCustom ? (
              <span
                className={`sk-desc editable ${!description ? 'empty' : ''}`}
                onClick={() => setEditingField('desc')}
              >
                {description || 'Click to add description...'}
              </span>
            ) : (
              <span className={`sk-desc ${!description ? 'empty' : ''}`}>
                {description || 'No description'}
              </span>
            )}
          </div>
        </div>

        {skill && !skill.enabled && isBuiltIn && (
          <div className="sk-warning-banner">
            This built-in skill is disabled. Disabling built-in skills may break core functionality.
          </div>
        )}

        {/* Body: content */}
        <div className="sk-body">
          <div className="sk-content-header">
            <span className="sk-content-label">Content</span>
            {isNew ? (
              /* New skill: no toggle needed, always in edit mode */
              null
            ) : !isBuiltIn && viewMode === 'edit' ? (
              <div className="sk-inline-actions">
                <button
                  className="sk-action-btn save"
                  onClick={handleContentSave}
                  disabled={!contentChanged || saving}
                  title="Save"
                >✓</button>
                <button
                  className="sk-action-btn cancel"
                  onClick={handleContentDiscard}
                  title="Discard"
                >✕</button>
              </div>
            ) : !isBuiltIn ? (
              <button
                type="button"
                className="sk-edit-btn"
                onClick={() => setViewMode('edit')}
                title="Edit content"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                  <path d="m15 5 4 4" />
                </svg>
              </button>
            ) : null}
          </div>

          {(isNew || (!isBuiltIn && viewMode === 'edit')) ? (
            <textarea
              className="sk-textarea"
              value={content}
              onChange={e => setContent(e.target.value)}
              placeholder="Enter skill content (supports markdown)..."
              autoFocus={!isNew}
            />
          ) : (
            <div className={`sk-preview ${!content ? 'empty' : ''}`}>
              {content ? (
                <Markdown>{content}</Markdown>
              ) : (
                <span className="sk-preview-placeholder">No content</span>
              )}
            </div>
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

          {isNew ? (
            <>
              <button type="button" className="btn btn-secondary" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleCreate}
                disabled={saving || !name.trim() || !content.trim() || !!nameError}
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
