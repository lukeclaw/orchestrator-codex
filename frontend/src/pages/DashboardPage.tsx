import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { useProjects } from '../hooks/useProjects'
import StatsBar from '../components/layout/StatsBar'
import WorkerCardCompact from '../components/workers/WorkerCardCompact'
import AddSessionModal from '../components/sessions/AddSessionModal'
import ProjectForm from '../components/projects/ProjectForm'
import ProjectsTable from '../components/projects/ProjectsTable'
import RecentActivity from '../components/dashboard/RecentActivity'
import TrendsPanel from '../components/dashboard/TrendsPanel'
import CollapsiblePanel from '../components/dashboard/CollapsiblePanel'
import { IconSessions, IconProjects } from '../components/common/Icons'
import './DashboardPage.css'

export default function DashboardPage() {
  const { projects, workers, tasks, loading, refresh: refreshApp } = useApp()
  const { create: createProject } = useProjects()
  const [showAddWorker, setShowAddWorker] = useState(false)
  const [showAddProject, setShowAddProject] = useState(false)
  const activeProjects = projects.filter(p => p.status === 'active')

  // Build session_id -> task lookup
  const taskBySession = new Map(
    tasks
      .filter(t => t.assigned_session_id)
      .map(t => [t.assigned_session_id!, t])
  )

  // Sort workers by last_viewed_at (most recent first), fallback to created_at
  const sortedWorkers = [...workers].sort((a, b) => {
    const aViewed = new Date(a.last_viewed_at || a.created_at).getTime()
    const bViewed = new Date(b.last_viewed_at || b.created_at).getTime()
    return bViewed - aViewed
  })

  // Hide RDEV badge if all workers are on rdevs (badge adds no info)
  const allRdev = workers.length > 0 && workers.every(w => w.host.includes('/'))

  return (
    <>
      <StatsBar />

      {/* Recent Activity */}
      <RecentActivity workers={workers} tasks={tasks} />

      {/* Trends */}
      <TrendsPanel />

      {/* Active Projects */}
      <CollapsiblePanel
        id="projects"
        className="dashboard-projects"
        title={<Link to="/projects" className="panel-header-link"><h2>Active Projects</h2></Link>}
      >
        {activeProjects.length > 0 ? (
          <div className="dashboard-projects-scroll scroll-fade">
            <ProjectsTable projects={activeProjects} hiddenColumns={['status', 'created']} />
          </div>
        ) : (
          <div className="dashboard-empty-state">
            <IconProjects size={48} />
            <h3>No active projects</h3>
            <p>Create a project to organize tasks and track progress.</p>
            <button className="btn btn-primary" onClick={() => setShowAddProject(true)}>+ New Project</button>
          </div>
        )}
      </CollapsiblePanel>

      {/* Workers */}
      <CollapsiblePanel
        id="workers"
        data-testid="sessions-panel"
        title={<Link to="/workers" className="panel-header-link"><h2>Workers</h2></Link>}
      >
        {loading ? (
          <p className="empty-state">Loading workers...</p>
        ) : workers.length === 0 ? (
          <div className="dashboard-empty-state">
            <IconSessions size={48} />
            <h3>No workers yet</h3>
            <p>Add a worker to get started.</p>
            <button className="btn btn-primary" onClick={() => setShowAddWorker(true)}>+ Add Worker</button>
          </div>
        ) : (
          <div className="dashboard-worker-grid" data-testid="session-grid">
            {sortedWorkers.map(s => (
              <WorkerCardCompact
                key={s.id}
                session={s}
                assignedTask={taskBySession.get(s.id) || null}
                allRdev={allRdev}
              />
            ))}
          </div>
        )}
      </CollapsiblePanel>

      <AddSessionModal open={showAddWorker} onClose={() => setShowAddWorker(false)} />
      <ProjectForm
        open={showAddProject}
        onClose={() => setShowAddProject(false)}
        onSubmit={async (body) => { const p = await createProject(body); refreshApp(); return p }}
      />
    </>
  )
}
