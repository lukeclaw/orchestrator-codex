import { useEffect } from 'react'
import { Outlet, useSearchParams, useLocation } from 'react-router-dom'
import { useSidebarState } from '../hooks/useSidebarState'
import { useBrainPanelState } from '../hooks/useBrainPanelState'
import { useNotifications } from '../context/NotificationContext'
import { useApp } from '../context/AppContext'
import { savePageFilters } from '../utils/filterPersistence'
import Sidebar from '../components/sidebar/Sidebar'
import Header from '../components/layout/Header'
import BrainPanel from '../components/brain/BrainPanel'
import NotificationToast from '../components/common/NotificationToast'
import GettingStartedModal from '../components/common/GettingStartedModal'
import './AppLayout.css'

/** Auto-saves current searchParams to sessionStorage on every change. */
function FilterSync() {
  const [searchParams] = useSearchParams()
  const location = useLocation()
  useEffect(() => {
    savePageFilters(location.pathname, searchParams)
  }, [searchParams, location.pathname])
  return null
}

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
          <FilterSync />
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
