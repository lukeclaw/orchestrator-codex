import { useApp } from '../../context/AppContext'
import SidebarItem from './SidebarItem'
import {
  IconDashboard,
  IconProjects,
  IconTasks,
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
  const { workers, notificationCount, updateAvailable } = useApp()

  const waitingWorkers = workers.filter(
    s => s.status === 'waiting'
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
        <SidebarItem to="/" icon={<IconDashboard size={18} />} label="Dashboard" collapsed={collapsed} shortcut="D" />
        <SidebarItem to="/projects" icon={<IconProjects size={18} />} label="Projects" collapsed={collapsed} shortcut="P" />
        <SidebarItem to="/tasks" icon={<IconTasks size={18} />} label="Tasks" collapsed={collapsed} shortcut="T" />
        <SidebarItem to="/workers" icon={<IconSessions size={18} />} label="Workers" badge={waitingWorkers} badgeVariant="warning" collapsed={collapsed} shortcut="W" />
        <SidebarItem to="/context" icon={<IconContext size={18} />} label="Context" collapsed={collapsed} shortcut="K" />
        <SidebarItem to="/skills" icon={<IconSkills size={18} />} label="Skills" collapsed={collapsed} shortcut="S" />
        <SidebarItem to="/notifications" icon={<IconBell size={18} />} label="Notifications" badge={notificationCount} badgeVariant="warning" collapsed={collapsed} shortcut="N" />
      </nav>

      <div className="sidebar-footer">
        <SidebarItem to="/settings" icon={<IconSettings size={18} />} label="Settings" badge={updateAvailable ? 1 : undefined} badgeVariant="default" collapsed={collapsed} />
      </div>
    </aside>
  )
}
