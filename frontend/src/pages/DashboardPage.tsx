import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import StatsBar from '../components/layout/StatsBar'
import SessionGrid from '../components/sessions/SessionGrid'
import AddSessionModal from '../components/sessions/AddSessionModal'
import ActivityTimeline from '../components/activity/ActivityTimeline'
import ProjectCard from '../components/projects/ProjectCard'
import './DashboardPage.css'

export default function DashboardPage() {
  const { projects } = useApp()
  const [showAddModal, setShowAddModal] = useState(false)

  const activeProjects = projects.filter(p => p.status === 'active')

  return (
    <>
      <StatsBar />

      {/* Active Projects */}
      {activeProjects.length > 0 && (
        <section className="dashboard-projects">
          <div className="dp-header">
            <h2>Active Projects</h2>
            <Link to="/projects" className="btn btn-secondary btn-sm">View All</Link>
          </div>
          <div className="dp-scroll">
            {activeProjects.map(p => (
              <ProjectCard key={p.id} project={p} />
            ))}
          </div>
        </section>
      )}

      {/* Workers + Activity */}
      <div className="grid-2">
        <section className="panel" data-testid="sessions-panel">
          <div className="panel-header">
            <h2>Workers</h2>
            <button
              className="btn btn-primary btn-sm"
              data-testid="add-session-btn"
              onClick={() => setShowAddModal(true)}
            >
              + Add Worker
            </button>
          </div>
          <SessionGrid />
        </section>

        <section className="panel" data-testid="activity-panel">
          <div className="panel-header">
            <h2>Recent Activity</h2>
          </div>
          <ActivityTimeline />
        </section>
      </div>

      <AddSessionModal open={showAddModal} onClose={() => setShowAddModal(false)} />
    </>
  )
}
