import { useApp } from '../../context/AppContext'
import SidebarItem from './SidebarItem'
import {
  IconDashboard,
  IconProjects,
  IconTasks,
  IconSessions,
  IconContext,
  IconSettings,
  IconChevronLeft,
  IconChevronRight,
  IconLogo,
} from '../common/Icons'
import './Sidebar.css'

interface Props {
  collapsed: boolean
  onToggle: () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const { workers } = useApp()

  const activeSessions = workers.filter(
    s => s.status === 'working' || s.status === 'idle'
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
        <SidebarItem to="/workers" icon={<IconSessions size={18} />} label="Workers" badge={activeSessions} collapsed={collapsed} shortcut="W" />
        <SidebarItem to="/context" icon={<IconContext size={18} />} label="Context" collapsed={collapsed} shortcut="K" />
      </nav>

      <div className="sidebar-footer">
        <SidebarItem to="/settings" icon={<IconSettings size={18} />} label="Settings" collapsed={collapsed} />
      </div>
    </aside>
  )
}
