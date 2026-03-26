import type { ReactNode } from 'react'
import './SlidingTabs.css'

export interface TabItem<T extends string = string> {
  value: T
  label: ReactNode
}

interface SlidingTabsProps<T extends string = string> {
  tabs: TabItem<T>[]
  value: T
  onChange: (value: T) => void
}

export default function SlidingTabs<T extends string = string>({
  tabs,
  value,
  onChange,
}: SlidingTabsProps<T>) {
  const activeIndex = tabs.findIndex(t => t.value === value)
  const count = tabs.length

  return (
    <div
      className="sliding-tabs"
      style={{ '--tab-count': count, '--active-index': activeIndex } as React.CSSProperties}
    >
      <div className="sliding-tabs__indicator" />
      {tabs.map(tab => (
        <button
          key={tab.value}
          type="button"
          className={`sliding-tabs__tab ${tab.value === value ? 'active' : ''}`}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
