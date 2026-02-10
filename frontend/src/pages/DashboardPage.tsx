import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApp } from '../context/AppContext'
import StatsBar from '../components/layout/StatsBar'
import SessionGrid from '../components/sessions/SessionGrid'
import AddSessionModal from '../components/sessions/AddSessionModal'
import ProjectsTable from '../components/projects/ProjectsTable'
import './DashboardPage.css'

export default function DashboardPage() {
  const { projects } = useApp()
  const [showAddModal, setShowAddModal] = useState(false)

  const activeProjects = projects.filter(p => p.status === 'active')

  return (
    <>
      <StatsBar />

      {/* Active Projects - Table View */}
      {activeProjects.length > 0 && (
        <section className="dashboard-projects panel">
          <div className="panel-header">
            <h2>Active Projects</h2>
            <Link to="/projects" className="btn btn-secondary btn-sm">View All</Link>
          </div>
          <div className="dashboard-projects-scroll">
            <ProjectsTable projects={activeProjects} />
          </div>
        </section>
      )}

      {/* Workers */}
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

      <AddSessionModal open={showAddModal} onClose={() => setShowAddModal(false)} />
    </>
  )
}
