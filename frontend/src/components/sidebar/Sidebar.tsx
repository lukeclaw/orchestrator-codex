import { useApp } from '../../context/AppContext'
import SidebarItem from './SidebarItem'
import {
  IconDashboard,
  IconProjects,
  IconSessions,
  IconDecisions,
  IconContext,
  IconActivity,
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
  const { workers, decisions } = useApp()

  const activeSessions = workers.filter(
    s => s.status === 'working' || s.status === 'idle'
  ).length

  const waitingSessions = workers.filter(s => s.status === 'waiting').length

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
        <SidebarItem to="/workers" icon={<IconSessions size={18} />} label="Workers" badge={activeSessions} collapsed={collapsed} shortcut="W" />
        {waitingSessions > 0 && (
          <SidebarItem to="/decisions" icon={<IconDecisions size={18} />} label="Waiting" badge={waitingSessions} collapsed={collapsed} />
        )}
        <SidebarItem to="/decisions" icon={<IconDecisions size={18} />} label="Decisions" badge={decisions.length} collapsed={collapsed} shortcut="E" />
        <SidebarItem to="/context" icon={<IconContext size={18} />} label="Context" collapsed={collapsed} shortcut="K" />
        <SidebarItem to="/activity" icon={<IconActivity size={18} />} label="Activity" collapsed={collapsed} shortcut="A" />
      </nav>

      <div className="sidebar-footer">
        <SidebarItem to="/settings" icon={<IconSettings size={18} />} label="Settings" collapsed={collapsed} />
      </div>
    </aside>
  )
}
