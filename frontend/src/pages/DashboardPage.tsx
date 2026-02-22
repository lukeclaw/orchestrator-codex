import { useState, useRef, useEffect, useCallback } from 'react'
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
import './DashboardPage.css'

export default function DashboardPage() {
  const { projects, workers, tasks, loading, refresh: refreshApp } = useApp()
  const { create: createProject } = useProjects()
  const [showAddWorker, setShowAddWorker] = useState(false)
  const [showAddProject, setShowAddProject] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [hasOverflow, setHasOverflow] = useState(false)

  const activeProjects = projects.filter(p => p.status === 'active')

  const checkOverflow = useCallback(() => {
    const el = scrollRef.current
    if (el) setHasOverflow(el.scrollHeight > el.clientHeight)
  }, [])

  useEffect(() => {
    checkOverflow()
  }, [activeProjects.length, checkOverflow])

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
        actions={
          <button className="btn btn-primary btn-sm" onClick={() => setShowAddProject(true)}>
            + New Project
          </button>
        }
      >
        {activeProjects.length > 0 ? (
          <div className={`dashboard-projects-scroll-wrapper${hasOverflow ? ' has-overflow' : ''}`}>
            <div className="dashboard-projects-scroll" ref={scrollRef}>
              <ProjectsTable projects={activeProjects} hiddenColumns={['status', 'created']} />
            </div>
          </div>
        ) : (
          <p className="empty-state">No active projects.</p>
        )}
      </CollapsiblePanel>

      {/* Workers */}
      <CollapsiblePanel
        id="workers"
        data-testid="sessions-panel"
        title={<Link to="/workers" className="panel-header-link"><h2>Workers</h2></Link>}
        actions={
          <button className="btn btn-primary btn-sm" data-testid="add-session-btn" onClick={() => setShowAddWorker(true)}>
            + Add Worker
          </button>
        }
      >
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
