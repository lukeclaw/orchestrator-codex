import { Outlet } from 'react-router-dom'
import { useSidebarState } from '../hooks/useSidebarState'
import { useBrainPanelState } from '../hooks/useBrainPanelState'
import { useNotifications } from '../context/NotificationContext'
import { useApp } from '../context/AppContext'
import Sidebar from '../components/sidebar/Sidebar'
import Header from '../components/layout/Header'
import BrainPanel from '../components/brain/BrainPanel'
import NotificationToast from '../components/common/NotificationToast'
import GettingStartedModal from '../components/common/GettingStartedModal'
import './AppLayout.css'

export default function AppLayout() {
  const { collapsed, toggle } = useSidebarState()
  const brainPanel = useBrainPanelState()
  const notifications = useNotifications()
  const { loading, projects, tasks, workers } = useApp()

  const showGettingStarted = !loading && projects.length === 0 && tasks.length === 0 && workers.length === 0

  return (
    <div className="app-shell">
      <Sidebar collapsed={collapsed} onToggle={toggle} />
      <div className="app-content">
        <Header />
        <main className="app-main">
          <Outlet />
        </main>
      </div>
      <BrainPanel
        collapsed={brainPanel.collapsed}
        onToggleCollapsed={brainPanel.toggleCollapsed}
        width={brainPanel.width}
        onWidthChange={brainPanel.updateWidth}
        minWidth={brainPanel.MIN_WIDTH}
        maxWidth={brainPanel.MAX_WIDTH}
      />
      <NotificationToast notifications={notifications} />
      <GettingStartedModal show={showGettingStarted} />
    </div>
  )
}
