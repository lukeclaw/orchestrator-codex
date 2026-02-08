import { useApp } from '../../context/AppContext'
import SidebarItem from './SidebarItem'
import {
  IconDashboard,
  IconProjects,
  IconSessions,
  IconTasks,
  IconDecisions,
  IconChat,
  IconActivity,
  IconSettings,
  IconChevronLeft,
  IconChevronRight,
} from '../common/Icons'
import './Sidebar.css'

interface Props {
  collapsed: boolean
  onToggle: () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const { sessions, decisions } = useApp()

  const activeSessions = sessions.filter(
    s => s.status === 'working' || s.status === 'idle'
  ).length

  const waitingSessions = sessions.filter(s => s.status === 'waiting').length

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        {!collapsed && <span className="sidebar-brand">Orchestrator</span>}
        <button className="sidebar-toggle" onClick={onToggle} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? <IconChevronRight size={16} /> : <IconChevronLeft size={16} />}
        </button>
      </div>

      <nav className="sidebar-nav">
        <SidebarItem to="/" icon={<IconDashboard size={18} />} label="Dashboard" collapsed={collapsed} shortcut="D" />
        <SidebarItem to="/projects" icon={<IconProjects size={18} />} label="Projects" collapsed={collapsed} shortcut="P" />
        <SidebarItem to="/sessions" icon={<IconSessions size={18} />} label="Sessions" badge={activeSessions} collapsed={collapsed} shortcut="S" />
        {waitingSessions > 0 && (
          <SidebarItem to="/decisions" icon={<IconDecisions size={18} />} label="Waiting" badge={waitingSessions} collapsed={collapsed} />
        )}
        <SidebarItem to="/tasks" icon={<IconTasks size={18} />} label="Tasks" collapsed={collapsed} shortcut="T" />
        <SidebarItem to="/decisions" icon={<IconDecisions size={18} />} label="Decisions" badge={decisions.length} collapsed={collapsed} shortcut="E" />
        <SidebarItem to="/chat" icon={<IconChat size={18} />} label="Chat" collapsed={collapsed} shortcut="C" />
        <SidebarItem to="/activity" icon={<IconActivity size={18} />} label="Activity" collapsed={collapsed} shortcut="A" />
      </nav>

      <div className="sidebar-footer">
        <SidebarItem to="/settings" icon={<IconSettings size={18} />} label="Settings" collapsed={collapsed} />
      </div>
    </aside>
  )
}
