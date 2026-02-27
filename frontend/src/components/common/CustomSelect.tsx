import { useState, useRef, useEffect } from 'react'
import { IconCheck } from './Icons'
import './CustomSelect.css'

interface Option {
  value: string
  label: string
}

interface Props {
  value: string
  options: Option[]
  onChange: (value: string) => void
  prefix?: string
}

export default function CustomSelect({ value, options, onChange, prefix }: Props) {
  const [open, setOpen] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)

  const selectedLabel = options.find(o => o.value === value)?.label || value

  useEffect(() => {
    if (!open) return

    function handleClickOutside(e: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
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

  return (
    <div className="custom-select" ref={wrapperRef}>
      <button
        className="custom-select-trigger"
        onClick={() => setOpen(!open)}
        type="button"
      >
        {prefix && <span className="custom-select-prefix">{prefix}</span>}
        <span className="custom-select-value">{selectedLabel}</span>
        <svg className={`custom-select-chevron${open ? ' open' : ''}`} width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="custom-select-dropdown">
          {options.map(opt => (
            <button
              key={opt.value}
              className={`custom-select-option${opt.value === value ? ' selected' : ''}`}
              onClick={() => { onChange(opt.value); setOpen(false) }}
              type="button"
            >
              <span>{opt.label}</span>
              {opt.value === value && <IconCheck size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
