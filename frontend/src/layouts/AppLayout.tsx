import { Outlet } from 'react-router-dom'
import { useSidebarState } from '../hooks/useSidebarState'
import { useKeyboardNav } from '../hooks/useKeyboardNav'
import Sidebar from '../components/sidebar/Sidebar'
import Header from '../components/layout/Header'
import './AppLayout.css'

export default function AppLayout() {
  const { collapsed, toggle } = useSidebarState()
  useKeyboardNav()

  return (
    <div className="app-shell">
      <Sidebar collapsed={collapsed} onToggle={toggle} />
      <div className="app-content">
        <Header />
        <main className="app-main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
