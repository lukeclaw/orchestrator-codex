import { useState, useRef, useEffect, type ReactNode } from 'react'
import './ConfirmPopover.css'

interface Props {
  message: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  children: (props: { onClick: (e: React.MouseEvent) => void }) => ReactNode
  variant?: 'danger' | 'warning' | 'default'
}

export default function ConfirmPopover({
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  children,
  variant = 'danger',
}: Props) {
  const [open, setOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return

    function handleClickOutside(e: MouseEvent) {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }

    function handleEscape(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [open])

  function handleTriggerClick(e: React.MouseEvent) {
    e.stopPropagation()
    e.preventDefault()
    setOpen(true)
  }

  function handleConfirm(e: React.MouseEvent) {
    e.stopPropagation()
    setOpen(false)
    onConfirm()
  }

  function handleCancel(e: React.MouseEvent) {
    e.stopPropagation()
    setOpen(false)
  }

  return (
    <div className="confirm-popover-wrapper" ref={triggerRef}>
      {children({ onClick: handleTriggerClick })}
      {open && (
        <div className={`confirm-popover ${variant}`} ref={popoverRef}>
          <p className="confirm-popover-message">{message}</p>
          <div className="confirm-popover-actions">
            <button
              className="btn btn-sm btn-secondary"
              onClick={handleCancel}
            >
              {cancelLabel}
            </button>
            <button
              className={`btn btn-sm ${variant === 'danger' ? 'btn-danger' : 'btn-primary'}`}
              onClick={handleConfirm}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
