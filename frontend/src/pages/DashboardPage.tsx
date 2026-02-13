import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import { useProjects } from '../hooks/useProjects'
import StatsBar from '../components/layout/StatsBar'
import WorkerCardCompact from '../components/workers/WorkerCardCompact'
import AddSessionModal from '../components/sessions/AddSessionModal'
import ProjectForm from '../components/projects/ProjectForm'
import ProjectsTable from '../components/projects/ProjectsTable'
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

  return (
    <>
      <StatsBar />

      {/* Active Projects */}
      <section className="dashboard-projects panel">
        <div className="panel-header">
          <Link to="/projects" className="panel-header-link"><h2>Active Projects</h2></Link>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => setShowAddProject(true)}
          >
            + New Project
          </button>
        </div>
        {activeProjects.length > 0 ? (
          <div className="dashboard-projects-scroll">
            <ProjectsTable projects={activeProjects} />
          </div>
        ) : (
          <p className="empty-state">No active projects.</p>
        )}
      </section>

      {/* Workers */}
      <section className="panel" data-testid="sessions-panel">
        <div className="panel-header">
          <Link to="/workers" className="panel-header-link"><h2>Workers</h2></Link>
          <button
            className="btn btn-primary btn-sm"
            data-testid="add-session-btn"
            onClick={() => setShowAddWorker(true)}
          >
            + Add Worker
          </button>
        </div>
        {loading ? (
          <p className="empty-state">Loading workers...</p>
        ) : workers.length === 0 ? (
          <p className="empty-state">No workers yet.</p>
        ) : (
          <div className="dashboard-worker-grid" data-testid="session-grid">
            {sortedWorkers.map(s => (
              <WorkerCardCompact
                key={s.id}
                session={s}
                assignedTask={taskBySession.get(s.id) || null}
              />
            ))}
          </div>
        )}
      </section>

      <AddSessionModal open={showAddWorker} onClose={() => setShowAddWorker(false)} />
      <ProjectForm
        open={showAddProject}
        onClose={() => setShowAddProject(false)}
        onSubmit={async (body) => { const p = await createProject(body); refreshApp(); return p }}
      />
    </>
  )
}
