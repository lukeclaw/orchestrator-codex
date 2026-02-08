import { useEffect, type ReactNode } from 'react'

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  wide?: boolean
  extraWide?: boolean
  children: ReactNode
}

export default function Modal({ open, onClose, title, wide, extraWide, children }: ModalProps) {
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="modal-backdrop" onClick={onClose} data-testid="modal-backdrop">
      <div
        className={`modal-content ${extraWide ? 'modal-extra-wide' : wide ? 'modal-wide' : ''}`}
        onClick={e => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="modal-close" onClick={onClose} data-testid="modal-close">&times;</button>
        </div>
        {children}
      </div>
    </div>
  )
}
