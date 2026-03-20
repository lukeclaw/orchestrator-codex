import { type ReactNode, useMemo } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { getPageFilters } from '../../utils/filterPersistence'
import './SidebarItem.css'

interface Props {
  to: string
  icon: ReactNode
  label: string
  badge?: number
  badgeVariant?: 'default' | 'warning' | 'danger'
  collapsed: boolean
  shortcut?: string
  preserveFilters?: boolean
}

export default function SidebarItem({ to, icon, label, badge, badgeVariant = 'default', collapsed, shortcut, preserveFilters }: Props) {
  const tooltip = collapsed ? label : undefined
  const location = useLocation()

  // Recompute when location changes — that's when FilterSync writes to sessionStorage
  const effectiveTo = useMemo(() => {
    if (!preserveFilters) return to
    const saved = getPageFilters(to)
    return saved ? `${to}?${saved}` : to
  }, [to, preserveFilters, location.pathname, location.search])

  return (
    <NavLink
      to={effectiveTo}
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
