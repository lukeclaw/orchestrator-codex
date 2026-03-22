import { useApp } from '../../context/AppContext'
import { useSettings } from '../../context/SettingsContext'
import SidebarItem from './SidebarItem'
import {
  IconDashboard,
  IconProjects,
  IconTasks,
  IconPullRequest,
  IconSessions,
  IconContext,
  IconSkills,
  IconSettings,
  IconChevronLeft,
  IconChevronRight,
  IconLogo,
  IconBell,
} from '../common/Icons'
import './Sidebar.css'

interface Props {
  collapsed: boolean
  onToggle: () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const { workers, notificationCount, updateAvailable, prBadgeCount } = useApp()
  const { getValue, loading: settingsLoading } = useSettings()
  const preserveFilters = !settingsLoading && Boolean(getValue('ui.preserve_filters'))

  const blockedWorkers = workers.filter(
    s => s.status === 'blocked'
  ).length

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        {collapsed ? (
          <button className="sidebar-toggle" onClick={onToggle} title="Expand sidebar">
            <IconChevronRight size={16} />
          </button>
        ) : (
          <>
            <div className="sidebar-brand-group">
              <IconLogo size={24} />
              <span className="sidebar-brand">Orchestrator</span>
            </div>
            <button className="sidebar-toggle" onClick={onToggle} title="Collapse sidebar">
              <IconChevronLeft size={16} />
            </button>
          </>
        )}
      </div>

      <nav className="sidebar-nav">
        <SidebarItem to="/" icon={<IconDashboard size={18} />} label="Dashboard" collapsed={collapsed} shortcut="D" preserveFilters={preserveFilters} />
        <SidebarItem to="/projects" icon={<IconProjects size={18} />} label="Projects" collapsed={collapsed} shortcut="P" preserveFilters={preserveFilters} />
        <SidebarItem to="/tasks" icon={<IconTasks size={18} />} label="Tasks" collapsed={collapsed} shortcut="T" preserveFilters={preserveFilters} />
        <SidebarItem to="/prs" icon={<IconPullRequest size={18} />} label="PRs" badge={prBadgeCount} badgeVariant="warning" collapsed={collapsed} shortcut="R" preserveFilters={preserveFilters} />
        <SidebarItem to="/workers" icon={<IconSessions size={18} />} label="Workers" badge={blockedWorkers} badgeVariant="warning" collapsed={collapsed} shortcut="W" preserveFilters={preserveFilters} />
        <SidebarItem to="/context" icon={<IconContext size={18} />} label="Context" collapsed={collapsed} shortcut="K" preserveFilters={preserveFilters} />
        <SidebarItem to="/skills" icon={<IconSkills size={18} />} label="Skills" collapsed={collapsed} shortcut="S" preserveFilters={preserveFilters} />
        <SidebarItem to="/notifications" icon={<IconBell size={18} />} label="Notifications" badge={notificationCount} badgeVariant="warning" collapsed={collapsed} shortcut="N" preserveFilters={preserveFilters} />
      </nav>

      <div className="sidebar-footer">
        <SidebarItem to="/settings" icon={<IconSettings size={18} />} label="Settings" badge={updateAvailable ? 1 : undefined} badgeVariant="default" collapsed={collapsed} preserveFilters={preserveFilters} />
      </div>
    </aside>
  )
}
