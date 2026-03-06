import { useState, useRef, useEffect } from 'react'
import './TagDropdown.css'

export interface TagOption {
  value: string
  label: string
  className?: string
  children?: TagOption[]  // Nested submenu options
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
  const [hoveredOption, setHoveredOption] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    
    const handleClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
        setHoveredOption(null)
      }
    }
    
    // Use capture phase so the listener fires even when stopPropagation is
    // called (e.g. inside modals). setTimeout avoids the opening click closing
    // the menu immediately.
    const timeoutId = setTimeout(() => {
      document.addEventListener('click', handleClickOutside, true)
    }, 0)

    return () => {
      clearTimeout(timeoutId)
      document.removeEventListener('click', handleClickOutside, true)
    }
  }, [open])

  // For nested options, find the selected option by checking children too
  const findSelectedOption = (): { option: TagOption; parent?: TagOption } => {
    for (const opt of options) {
      if (opt.value === value) return { option: opt }
      if (opt.children) {
        const child = opt.children.find(c => c.value === value)
        if (child) return { option: child, parent: opt }
      }
    }
    return { option: options[0] }
  }

  const { option: selectedOption, parent: selectedParent } = findSelectedOption()
  const otherOptions = options.filter(o => o.value !== value && o.value !== selectedParent?.value)

  const handleSelect = (val: string, hasChildren: boolean) => {
    if (hasChildren) {
      // Don't select parent options that have children
      return
    }
    onChange(val)
    setOpen(false)
    setHoveredOption(null)
  }

  const defaultRenderTag = (option: TagOption, isSelected: boolean) => (
    <span className={`tag-dropdown-tag ${option.className || ''} ${isSelected ? 'selected' : ''}`}>
      {option.label}
    </span>
  )

  const render = renderTag || defaultRenderTag

  return (
    <div className="tag-dropdown" ref={ref}>
      <div className={`tag-dropdown-selected ${!disabled ? 'interactive' : ''}`} onClick={() => !disabled && setOpen(!open)}>
        {render(selectedOption, true)}
        {!disabled && (
          <button className="tag-dropdown-btn" onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
            <span className={`tag-dropdown-arrow ${open ? 'open' : ''}`}>▼</span>
          </button>
        )}
      </div>
      {open && (
        <div className="tag-dropdown-menu">
          {otherOptions.map(opt => (
            <div
              key={opt.value}
              className={`tag-dropdown-option ${opt.children ? 'has-children' : ''}`}
              onClick={() => handleSelect(opt.value, !!opt.children)}
              onMouseEnter={() => opt.children && setHoveredOption(opt.value)}
              onMouseLeave={() => !opt.children && setHoveredOption(null)}
            >
              {render(opt, false)}
              {opt.children && <span className="tag-dropdown-submenu-arrow">►</span>}
              {opt.children && hoveredOption === opt.value && (
                <div
                  className="tag-dropdown-submenu"
                  onMouseEnter={() => setHoveredOption(opt.value)}
                  onMouseLeave={() => setHoveredOption(null)}
                >
                  {opt.children.map(child => (
                    <div
                      key={child.value}
                      className="tag-dropdown-option"
                      onClick={(e) => {
                        e.stopPropagation()
                        handleSelect(child.value, false)
                      }}
                    >
                      {render(child, false)}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
