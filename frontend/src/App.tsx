import { Routes, Route } from 'react-router-dom'
import { AppProvider } from './context/AppContext'
import { NotificationProvider } from './context/NotificationContext'
import AppLayout from './layouts/AppLayout'
import DashboardPage from './pages/DashboardPage'
import WorkersPage from './pages/WorkersPage'
import SessionDetailPage from './pages/SessionDetailPage'
import ProjectsPage from './pages/ProjectsPage'
import ProjectDetailPage from './pages/ProjectDetailPage'
import DecisionsPage from './pages/DecisionsPage'
import ActivityPage from './pages/ActivityPage'
import ContextPage from './pages/ContextPage'
import SettingsPage from './pages/SettingsPage'

export default function App() {
  return (
    <NotificationProvider>
      <AppProvider>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/:id" element={<ProjectDetailPage />} />
            <Route path="/workers" element={<WorkersPage />} />
            <Route path="/workers/:id" element={<SessionDetailPage />} />
            <Route path="/decisions" element={<DecisionsPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/context" element={<ContextPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </AppProvider>
    </NotificationProvider>
  )
}
