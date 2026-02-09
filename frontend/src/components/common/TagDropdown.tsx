import { useState, useRef, useEffect } from 'react'
import './TagDropdown.css'

interface TagOption {
  value: string
  label: string
  className?: string
}

interface Props {
  value: string
  options: TagOption[]
  onChange: (value: string) => void
  disabled?: boolean
  renderTag?: (option: TagOption, isSelected: boolean) => React.ReactNode
}

export default function TagDropdown({ value, options, onChange, disabled = false, renderTag }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    
    // Use setTimeout to avoid the click that opened the menu from immediately closing it
    const timeoutId = setTimeout(() => {
      document.addEventListener('click', handleClickOutside)
    }, 0)
    
    return () => {
      clearTimeout(timeoutId)
      document.removeEventListener('click', handleClickOutside)
    }
  }, [open])

  const selectedOption = options.find(o => o.value === value) || options[0]
  const otherOptions = options.filter(o => o.value !== value)

  const handleSelect = (val: string) => {
    onChange(val)
    setOpen(false)
  }

  const defaultRenderTag = (option: TagOption, isSelected: boolean) => (
    <span className={`tag-dropdown-tag ${option.className || ''} ${isSelected ? 'selected' : ''}`}>
      {option.label}
    </span>
  )

  const render = renderTag || defaultRenderTag

  return (
    <div className="tag-dropdown" ref={ref}>
      <div className="tag-dropdown-selected">
        {render(selectedOption, true)}
        {!disabled && (
          <button className="tag-dropdown-btn" onClick={() => setOpen(!open)}>
            <span className={`tag-dropdown-arrow ${open ? 'open' : ''}`}>▼</span>
          </button>
        )}
      </div>
      {open && (
        <div className="tag-dropdown-menu">
          {otherOptions.map(opt => (
            <div key={opt.value} className="tag-dropdown-option" onClick={() => handleSelect(opt.value)}>
              {render(opt, false)}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
