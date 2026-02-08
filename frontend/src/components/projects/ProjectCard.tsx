import { Link } from 'react-router-dom'
import type { Project, Task } from '../../api/types'
import ProgressBar from '../common/ProgressBar'
import './ProjectCard.css'

interface Props {
  project: Project
  tasks?: Task[]
}

export default function ProjectCard({ project, tasks = [] }: Props) {
  const done = tasks.filter(t => t.status === 'done').length
  const total = tasks.length

  return (
    <Link to={`/projects/${project.id}`} className="project-card">
      <div className="pc-header">
        <span className="pc-name">{project.name}</span>
        <span className={`status-badge ${project.status}`}>{project.status}</span>
      </div>
      {project.description && (
        <p className="pc-desc">{project.description}</p>
      )}
      {total > 0 && (
        <div className="pc-progress">
          <ProgressBar done={done} total={total} />
        </div>
      )}
      <div className="pc-footer">
        <span className="pc-tasks">{total} task{total !== 1 ? 's' : ''}</span>
        {project.target_date && (
          <span className="pc-date">Due {new Date(project.target_date).toLocaleDateString()}</span>
        )}
      </div>
    </Link>
  )
}
