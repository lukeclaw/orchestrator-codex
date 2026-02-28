import type { Skill } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import './SkillCard.css'

interface Props {
  skill: Skill
  onClick: () => void
  onToggleEnabled: () => void
}

export default function SkillCard({ skill, onClick, onToggleEnabled }: Props) {
  return (
    <div className={`skill-card ${!skill.enabled ? 'disabled' : ''}`} onClick={onClick}>
      <div className="skill-card-header">
        <span className="skill-card-name">/{skill.name}</span>
        <span className={`skill-card-badge ${skill.type === 'built_in' ? 'built-in' : 'custom'}`}>
          {skill.type === 'built_in' ? 'BUILT-IN' : 'CUSTOM'}
        </span>
        <button
          className={`skill-card-toggle ${skill.enabled ? 'on' : ''}`}
          onClick={e => { e.stopPropagation(); onToggleEnabled() }}
          title={skill.enabled ? 'Disable skill' : 'Enable skill'}
        >
          <span className="skill-card-toggle-knob" />
        </button>
      </div>
      <div className="skill-card-body">
        {skill.description ? (
          <p className="skill-card-desc">{skill.description}</p>
        ) : (
          <p className="skill-card-desc empty">No description</p>
        )}
        {!skill.enabled && skill.type === 'built_in' && (
          <div className="skill-card-warning">
            Disabling built-in skills may break core functionality
          </div>
        )}
      </div>
      <div className="skill-card-footer">
        <span className="skill-card-meta">{skill.line_count} lines</span>
        <span className="skill-card-meta skill-card-time">{timeAgo(skill.updated_at)}</span>
      </div>
    </div>
  )
}
