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
  // Indicator width: (100% - padding offsets) / N tabs
  // Transform: slide by index * (100% + gap)
  const indicatorWidth = `calc(${100 / count}% - ${(count + 1) * 4 / count}px)`
  const indicatorTransform = activeIndex > 0
    ? `translateX(calc(${activeIndex} * (100% + 4px)))`
    : undefined

  return (
    <div className="sliding-tabs">
      <div
        className="sliding-tabs__indicator"
        style={{ width: indicatorWidth, transform: indicatorTransform }}
      />
      {tabs.map(tab => (
        <button
          key={tab.value}
          className={`sliding-tabs__tab ${tab.value === value ? 'active' : ''}`}
          onClick={() => onChange(tab.value)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}
