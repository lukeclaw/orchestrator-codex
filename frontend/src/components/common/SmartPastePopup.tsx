import { useState } from 'react'
import Modal from './Modal'

interface PasteOption {
  label: string
  description?: string
  action: () => Promise<void>
}

interface SmartPastePopupProps {
  open: boolean
  onClose: () => void
  title: string
  preview?: string       // First ~200 chars
  charCount?: number
  options: PasteOption[]
}

export default function SmartPastePopup({ open, onClose, title, preview, charCount, options }: SmartPastePopupProps) {
  const [loading, setLoading] = useState<number | null>(null)

  async function handleAction(idx: number) {
    setLoading(idx)
    try {
      await options[idx].action()
      onClose()
    } catch {
      // Error handling delegated to the action callback
    } finally {
      setLoading(null)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="modal-body" style={{ padding: '12px 16px' }}>
        {preview && (
          <pre style={{
            fontSize: '12px',
            color: 'var(--text-muted)',
            background: 'var(--surface)',
            padding: '8px 10px',
            borderRadius: 'var(--radius)',
            maxHeight: '120px',
            overflow: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            margin: '0 0 8px',
          }}>
            {preview}
          </pre>
        )}
        {charCount != null && (
          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            {charCount.toLocaleString()} characters
          </span>
        )}
      </div>
      <div className="modal-footer" style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', padding: '12px 16px' }}>
        <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
        {options.map((opt, i) => (
          <button
            key={i}
            className="btn btn-primary"
            onClick={() => handleAction(i)}
            disabled={loading !== null}
            title={opt.description}
          >
            {loading === i ? 'Pasting...' : opt.label}
          </button>
        ))}
      </div>
    </Modal>
  )
}
