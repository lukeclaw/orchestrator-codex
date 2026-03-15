import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import type { Task } from '../../api/types'
import { IconExternalLink, IconPlus } from '../common/Icons'
import { useNotify } from '../../context/NotificationContext'

const formatStatus = (s: string) => {
  switch (s) {
    case 'todo': return 'To Do'
    case 'in_progress': return 'In Progress'
    case 'done': return 'Done'
    case 'blocked': return 'Blocked'
    default: return s
  }
}

interface TaskSubtasksCardProps {
  task: Task
  isEditable: boolean
  refresh: () => void
}

export default function TaskSubtasksCard({ task, isEditable, refresh }: TaskSubtasksCardProps) {
  const notify = useNotify()
  const [subtasks, setSubtasks] = useState<Task[]>([])
  const [subtasksExpanded, setSubtasksExpanded] = useState(true)
  const [subtaskFilter, setSubtaskFilter] = useState<string>('all')
  const [showAddSubtask, setShowAddSubtask] = useState(false)
  const [newSubtaskTitle, setNewSubtaskTitle] = useState('')
  const [creatingSubtask, setCreatingSubtask] = useState(false)

  useEffect(() => {
    api<Task[]>(`/api/tasks?parent_task_id=${task.id}&include_subtask_stats=false`)
      .then(setSubtasks)
      .catch(() => setSubtasks([]))
  }, [task.id, task.updated_at])

  const handleCreateSubtask = async () => {
    if (!newSubtaskTitle.trim()) return
    setCreatingSubtask(true)
    try {
      await api('/api/tasks', {
        method: 'POST',
        body: JSON.stringify({
          project_id: task.project_id,
          parent_task_id: task.id,
          title: newSubtaskTitle.trim(),
          status: 'todo',
          priority: 'M'
        })
      })
      setNewSubtaskTitle('')
      setShowAddSubtask(false)
      const updated = await api<Task[]>(`/api/tasks?parent_task_id=${task.id}&include_subtask_stats=false`)
      setSubtasks(updated)
      refresh()
    } catch (err) {
      console.error('Failed to create subtask:', err)
      notify('Failed to create subtask', 'error')
    } finally {
      setCreatingSubtask(false)
    }
  }

  const doneSubtasks = subtasks.filter(st => st.status === 'done').length
  const activeSubtasks = subtasks.filter(st => st.status === 'in_progress').length
  const blockedSubtasks = subtasks.filter(st => st.status === 'blocked').length
  const totalSubtasks = subtasks.length
  const filteredSubtasks = subtaskFilter === 'all' ? subtasks : subtasks.filter(st => st.status === subtaskFilter)

  return (
    <div className="tdp-card tdp-subtasks-card">
      <div className="tdp-card-header">
        <h3 className="clickable" onClick={() => setSubtasksExpanded(!subtasksExpanded)}>
          <span className={`expand-icon ${subtasksExpanded ? 'expanded' : ''}`}>&#9654;</span>
          Subtasks
          {subtasks.length > 0 && (
            <>
              <span className="count">({doneSubtasks}/{totalSubtasks})</span>
              <div className="tdp-progress-inline">
                {doneSubtasks > 0 && <div className="seg done" style={{ width: `${(doneSubtasks / totalSubtasks) * 100}%` }} />}
                {activeSubtasks > 0 && <div className="seg active" style={{ width: `${(activeSubtasks / totalSubtasks) * 100}%` }} />}
                {blockedSubtasks > 0 && <div className="seg blocked" style={{ width: `${(blockedSubtasks / totalSubtasks) * 100}%` }} />}
              </div>
            </>
          )}
        </h3>
        {isEditable && !showAddSubtask && (
          <button className="tdp-edit-btn" onClick={() => setShowAddSubtask(true)}><IconPlus size={12} /> Add</button>
        )}
      </div>
      {showAddSubtask && (
        <div className="tdp-subtask-form">
          <input
            type="text"
            placeholder="Subtask title..."
            value={newSubtaskTitle}
            onChange={e => setNewSubtaskTitle(e.target.value)}
            autoFocus
            onKeyDown={e => {
              if (e.key === 'Enter' && newSubtaskTitle.trim()) handleCreateSubtask()
              if (e.key === 'Escape') { setShowAddSubtask(false); setNewSubtaskTitle('') }
            }}
          />
          <div className="tdp-inline-actions">
            <button
              className="tdp-action-btn save"
              onClick={handleCreateSubtask}
              disabled={!newSubtaskTitle.trim() || creatingSubtask}
              title="Create"
            >
              &#10003;
            </button>
            <button
              className="tdp-action-btn cancel"
              onClick={() => { setShowAddSubtask(false); setNewSubtaskTitle('') }}
              title="Cancel"
            >
              &#10005;
            </button>
          </div>
        </div>
      )}
      {subtasksExpanded && subtasks.length > 0 && (
        <>
          {subtasks.length > 1 && (
            <div className="tdp-subtask-filters">
              {[
                { value: 'all', label: 'All', count: totalSubtasks },
                { value: 'todo', label: 'To Do', count: subtasks.filter(st => st.status === 'todo').length },
                { value: 'in_progress', label: 'Active', count: activeSubtasks },
                { value: 'done', label: 'Done', count: doneSubtasks },
                { value: 'blocked', label: 'Blocked', count: blockedSubtasks },
              ].filter(f => f.value === 'all' || f.count > 0).map(f => (
                <button
                  key={f.value}
                  className={`tdp-subtask-filter-pill ${f.value !== 'all' ? `status-${f.value}` : ''} ${subtaskFilter === f.value ? 'active' : ''}`}
                  onClick={() => setSubtaskFilter(f.value)}
                >
                  {f.label}
                  <span className="pill-count">{f.count}</span>
                </button>
              ))}
            </div>
          )}
          <div className="tdp-subtasks-list">
            {filteredSubtasks.map(st => (
              <div key={st.id} className="tdp-subtask-row">
                <Link to={`/tasks/${st.id}`} className="tdp-subtask-item">
                  <span className={`subtask-status status-${st.status}`} />
                  <span className="subtask-key">{st.task_key}</span>
                  <span className="subtask-title">{st.title}</span>
                </Link>
                {st.links && st.links.length > 0 && (
                  <a
                    href={st.links[0].url}
                    className="subtask-link-btn"
                    onClick={e => { e.stopPropagation() }}
                    title={st.links.length > 1 ? `${st.links[0].url} (+${st.links.length - 1} more)` : st.links[0].url}
                  >
                    <IconExternalLink size={13} />{st.links.length > 1 && <span className="link-more">...</span>}
                  </a>
                )}
              </div>
            ))}
            {filteredSubtasks.length === 0 && (
              <p className="tdp-empty-text">No {formatStatus(subtaskFilter).toLowerCase()} subtasks</p>
            )}
          </div>
        </>
      )}
      {subtasksExpanded && subtasks.length === 0 && !showAddSubtask && (
        <div className="tdp-links-empty">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 11l3 3L22 4" />
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
          <span>No subtasks yet</span>
        </div>
      )}
    </div>
  )
}
