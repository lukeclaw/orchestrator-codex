import type { Skill } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './SkillCard.css'

interface Props {
  skill: Skill
  onClick: () => void
}

export default function SkillCard({ skill, onClick }: Props) {
  return (
    <div className="skill-card" onClick={onClick}>
      <div className="skill-card-header">
        <span className="skill-card-name">/{skill.name}</span>
        <span className={`skill-card-badge ${skill.type === 'built_in' ? 'built-in' : 'custom'}`}>
          {skill.type === 'built_in' ? 'BUILT-IN' : 'CUSTOM'}
        </span>
      </div>
      <div className="skill-card-body">
        {skill.description ? (
          <p className="skill-card-desc">{skill.description}</p>
        ) : (
          <p className="skill-card-desc empty">No description</p>
        )}
      </div>
      <div className="skill-card-footer">
        <span className="skill-card-meta">{skill.line_count} lines</span>
        <span className="skill-card-meta">{timeAgo(skill.updated_at)}</span>
      </div>
    </div>
  )
}
