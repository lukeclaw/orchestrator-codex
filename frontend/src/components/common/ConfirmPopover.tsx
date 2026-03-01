import { useState, useRef, useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
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
  const [position, setPosition] = useState<{ top?: number; bottom?: number; right: number }>({ right: 0 })
  const popoverRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return

    // Calculate position based on trigger element and available space
    if (triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect()
      const spaceBelow = window.innerHeight - rect.bottom
      const spaceAbove = rect.top
      const popoverHeight = 120
      const popoverWidth = 200
      
      // Calculate right position (align to right edge of trigger)
      const rightPos = window.innerWidth - rect.right
      
      if (spaceBelow < popoverHeight && spaceAbove > spaceBelow) {
        // Position above
        setPosition({ bottom: window.innerHeight - rect.top + 8, right: rightPos })
      } else {
        // Position below
        setPosition({ top: rect.bottom + 8, right: rightPos })
      }
    }

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
      {open && createPortal(
        <div
          className={`confirm-popover ${variant}`}
          ref={popoverRef}
          style={position}
        >
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
        </div>,
        document.body
      )}
    </div>
  )
}
