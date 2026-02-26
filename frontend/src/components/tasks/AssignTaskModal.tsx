import { useState, useMemo } from 'react'
import { useApp } from '../../context/AppContext'
import { useNotify } from '../../context/NotificationContext'
import { api } from '../../api/client'
import Modal from '../common/Modal'
import './AssignTaskModal.css'

interface Props {
  open: boolean
  onClose: () => void
  sessionId: string
  sessionName: string
}

// Normalize priority values: "high"→"H", "medium"→"M", "low"→"L", passthrough H/M/L
function normalizePriority(p: string): string {
  const lower = p.toLowerCase()
  if (lower === 'high' || lower === 'h') return 'H'
  if (lower === 'medium' || lower === 'med' || lower === 'm') return 'M'
  if (lower === 'low' || lower === 'l') return 'L'
  return 'M'
}

const PRIORITY_ORDER: Record<string, number> = { H: 0, M: 1, L: 2 }

export default function AssignTaskModal({ open, onClose, sessionId, sessionName }: Props) {
  const { tasks, projects, refresh } = useApp()
  const notify = useNotify()
  const [assigning, setAssigning] = useState(false)

  // Filter: unassigned, top-level, todo or in_progress
  const availableTasks = useMemo(() => {
    return tasks
      .filter(t =>
        !t.assigned_session_id &&
        !t.parent_task_id &&
        (t.status === 'todo' || t.status === 'in_progress')
      )
      .sort((a, b) => {
        const pa = PRIORITY_ORDER[normalizePriority(a.priority)] ?? 1
        const pb = PRIORITY_ORDER[normalizePriority(b.priority)] ?? 1
        if (pa !== pb) return pa - pb
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      })
  }, [tasks])

  // Group by project
  const grouped = useMemo(() => {
    const projectMap = new Map(projects.map(p => [p.id, p.name]))
    const groups: { projectId: string; projectName: string; tasks: typeof availableTasks }[] = []
    const byProject = new Map<string, typeof availableTasks>()

    for (const task of availableTasks) {
      const list = byProject.get(task.project_id) || []
      list.push(task)
      byProject.set(task.project_id, list)
    }

    for (const [projectId, projectTasks] of byProject) {
      groups.push({
        projectId,
        projectName: projectMap.get(projectId) || 'Unknown Project',
        tasks: projectTasks,
      })
    }

    return groups
  }, [availableTasks, projects])

  async function handleAssign(taskId: string) {
    if (assigning) return
    setAssigning(true)
    try {
      // Prepare worker for task (non-blocking on failure)
      try {
        await api(`/api/sessions/${sessionId}/prepare-for-task`, { method: 'POST' })
      } catch {
        // Non-blocking — worker may already be ready
      }

      // Assign the task
      await api(`/api/tasks/${taskId}`, {
        method: 'PATCH',
        body: JSON.stringify({ assigned_session_id: sessionId }),
      })

      notify(`Task assigned to ${sessionName}`, 'success')
      refresh()
      onClose()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Failed to assign task', 'error')
    } finally {
      setAssigning(false)
    }
  }

  const priorityLabel: Record<string, string> = { H: 'High', M: 'Med', L: 'Low' }

  return (
    <Modal open={open} onClose={onClose} title={`Assign task to ${sessionName}`}>
      <div className="atm-hint">Pick an available task to assign to this worker</div>
      <div className="atm-body">
        {grouped.length === 0 ? (
          <div className="atm-empty">No available tasks to assign</div>
        ) : (
          grouped.map(group => (
            <div key={group.projectId} className="atm-project-group">
              <div className="atm-project-header">{group.projectName}</div>
              {group.tasks.map(task => {
                const np = normalizePriority(task.priority)
                return (
                  <button
                    key={task.id}
                    className="atm-task-option"
                    onClick={() => handleAssign(task.id)}
                    disabled={assigning}
                  >
                    <span className={`atm-priority priority-${np}`}>
                      {priorityLabel[np]}
                    </span>
                    {task.task_key && <span className="atm-task-key">{task.task_key}</span>}
                    <span className="atm-task-title">{task.title}</span>
                    <span className={`atm-status status-${task.status}`}>{task.status.replace('_', ' ')}</span>
                  </button>
                )
              })}
            </div>
          ))
        )}
      </div>
    </Modal>
  )
}
