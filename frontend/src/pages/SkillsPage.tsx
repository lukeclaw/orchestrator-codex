import { useState, useMemo } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useSkills } from '../hooks/useSkills'
import type { Skill } from '../api/types'
import SkillCard from '../components/skills/SkillCard'
import SkillModal from '../components/skills/SkillModal'
import './SkillsPage.css'

export default function SkillsPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const activeTab = location.pathname === '/skills/worker' ? 'worker' : 'brain'

  const { items, loading, fetch, getItem, create, update, remove } = useSkills({ target: activeTab })

  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null)
  const [showNewSkill, setShowNewSkill] = useState(false)

  const sorted = useMemo(() => {
    const builtIn = items.filter(s => s.type === 'built_in')
    const custom = items.filter(s => s.type === 'custom')
    return [...builtIn, ...custom]
  }, [items])

  async function handleCardClick(skill: Skill) {
    const full = await getItem(skill)
    setSelectedSkill(full)
  }

  async function handleSave(body: { id?: string; name: string; target: string; content: string; description?: string }) {
    if (body.id) {
      const { id, ...rest } = body
      await update(id, rest as Parameters<typeof update>[1])
    } else {
      await create(body as Parameters<typeof create>[0])
    }
    fetch()
  }

  async function handleDelete(id: string) {
    await remove(id)
  }

  return (
    <div className="skills-page">
      <div className="page-header">
        <h1>Skills</h1>
        <div className="page-header-actions">
          <div className="skills-tabs">
            <button
              className={`skills-tab ${activeTab === 'brain' ? 'active' : ''}`}
              onClick={() => navigate('/skills')}
            >
              Brain
            </button>
            <button
              className={`skills-tab ${activeTab === 'worker' ? 'active' : ''}`}
              onClick={() => navigate('/skills/worker')}
            >
              Worker
            </button>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => setShowNewSkill(true)}>
            + New Skill
          </button>
        </div>
      </div>

      {loading ? (
        <p className="empty-state">Loading...</p>
      ) : sorted.length === 0 ? (
        <p className="empty-state">No {activeTab} skills yet.</p>
      ) : (
        <div className="skills-grid">
          {sorted.map(skill => (
            <SkillCard
              key={skill.id}
              skill={skill}
              onClick={() => handleCardClick(skill)}
            />
          ))}
        </div>
      )}

      <SkillModal
        skill={selectedSkill}
        isNew={showNewSkill}
        defaultTarget={activeTab}
        onClose={() => {
          setSelectedSkill(null)
          setShowNewSkill(false)
        }}
        onSave={handleSave}
        onDelete={handleDelete}
      />
    </div>
  )
}
