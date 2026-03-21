import { type ReactNode } from 'react'
import { Routes, Route } from 'react-router-dom'
import { AppProvider } from './context/AppContext'
import { NotificationProvider } from './context/NotificationContext'
import { SettingsProvider } from './context/SettingsContext'
import { useTheme } from './hooks/useTheme'
import ErrorBoundary from './components/common/ErrorBoundary'
import AppLayout from './layouts/AppLayout'
import DashboardPage from './pages/DashboardPage'
import WorkersPage from './pages/WorkersPage'
import SessionDetailPage from './pages/SessionDetailPage'
import ProjectsPage from './pages/ProjectsPage'
import ProjectDetailPage from './pages/ProjectDetailPage'
import TasksPage from './pages/TasksPage'
import TaskDetailPage from './pages/TaskDetailPage'
import ContextPage from './pages/ContextPage'
import SkillsPage from './pages/SkillsPage'
import NotificationsPage from './pages/NotificationsPage'
import PRsPage from './pages/PRsPage'
import SettingsPage from './pages/SettingsPage'

function ThemeApplicator({ children }: { children: ReactNode }) {
  useTheme()
  return <>{children}</>
}

export default function App() {
  return (
    <ErrorBoundary>
      <NotificationProvider>
        <SettingsProvider>
        <ThemeApplicator>
        <AppProvider>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:id" element={<ProjectDetailPage />} />
              <Route path="/tasks" element={<TasksPage />} />
              <Route path="/tasks/:id" element={<TaskDetailPage />} />
              <Route path="/prs" element={<PRsPage />} />
              <Route path="/workers" element={<WorkersPage />} />
              <Route path="/workers/rdevs" element={<WorkersPage />} />
              <Route path="/workers/:id" element={<SessionDetailPage />} />
              <Route path="/context" element={<ContextPage />} />
              <Route path="/skills" element={<SkillsPage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </AppProvider>
        </ThemeApplicator>
        </SettingsProvider>
      </NotificationProvider>
    </ErrorBoundary>
  )
}
