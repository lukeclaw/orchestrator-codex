import { type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import './SidebarItem.css'

interface Props {
  to: string
  icon: ReactNode
  label: string
  badge?: number
  badgeVariant?: 'default' | 'warning'
  collapsed: boolean
  shortcut?: string
}

export default function SidebarItem({ to, icon, label, badge, badgeVariant = 'default', collapsed, shortcut }: Props) {
  const tooltip = collapsed ? label : undefined

  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `sidebar-item ${isActive ? 'active' : ''} ${collapsed ? 'collapsed' : ''}`
      }
      title={tooltip}
    >
      <span className="sidebar-icon">{icon}</span>
      {!collapsed && <span className="sidebar-label">{label}</span>}
      {!collapsed && badge !== undefined && badge > 0 && (
        <span className={`sidebar-badge ${badgeVariant}`}>{badge}</span>
      )}
      {collapsed && badge !== undefined && badge > 0 && (
        <span className={`sidebar-badge-dot ${badgeVariant}`} />
      )}
    </NavLink>
  )
}
