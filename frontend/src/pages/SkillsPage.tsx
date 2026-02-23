import { useState, useMemo } from 'react'
import { useSkills } from '../hooks/useSkills'
import type { Skill } from '../api/types'
import SkillCard from '../components/skills/SkillCard'
import SkillModal from '../components/skills/SkillModal'
import './SkillsPage.css'

export default function SkillsPage() {
  const { items, loading, fetch, getItem, create, update, remove, toggleEnabled } = useSkills()

  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null)
  const [showNewSkill, setShowNewSkill] = useState(false)

  const { brainBuiltIn, brainCustom, workerBuiltIn, workerCustom } = useMemo(() => {
    const bb: Skill[] = []
    const bc: Skill[] = []
    const wb: Skill[] = []
    const wc: Skill[] = []
    for (const s of items) {
      if (s.target === 'brain') {
        if (s.type === 'built_in') bb.push(s)
        else bc.push(s)
      } else {
        if (s.type === 'built_in') wb.push(s)
        else wc.push(s)
      }
    }
    return { brainBuiltIn: bb, brainCustom: bc, workerBuiltIn: wb, workerCustom: wc }
  }, [items])

  const brainSkills = [...brainBuiltIn, ...brainCustom]
  const workerSkills = [...workerBuiltIn, ...workerCustom]

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
          <button className="btn btn-primary btn-sm" onClick={() => setShowNewSkill(true)}>
            + New Skill
          </button>
        </div>
      </div>

      <p className="skills-note">Skill changes take effect on new sessions only.</p>

      {loading ? (
        <p className="empty-state">Loading...</p>
      ) : items.length === 0 ? (
        <p className="empty-state">No skills yet.</p>
      ) : (
        <>
          {brainSkills.length > 0 && (
            <div className="skills-section">
              <h2 className="skills-section-title">Brain</h2>
              <div className="skills-grid">
                {brainSkills.map(skill => (
                  <SkillCard
                    key={skill.id}
                    skill={skill}
                    onClick={() => handleCardClick(skill)}
                    onToggleEnabled={() => toggleEnabled(skill)}
                  />
                ))}
              </div>
            </div>
          )}

          {workerSkills.length > 0 && (
            <div className="skills-section">
              <h2 className="skills-section-title">Worker</h2>
              <div className="skills-grid">
                {workerSkills.map(skill => (
                  <SkillCard
                    key={skill.id}
                    skill={skill}
                    onClick={() => handleCardClick(skill)}
                    onToggleEnabled={() => toggleEnabled(skill)}
                  />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <SkillModal
        skill={selectedSkill}
        isNew={showNewSkill}
        defaultTarget="worker"
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
