import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Session, Task } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import { IconCheck, IconX, IconArrowRight, IconPlay, IconPause } from '../common/Icons'
import CollapsiblePanel from './CollapsiblePanel'
import './RecentActivity.css'

interface ActivityItem {
  id: string
  icon: ReactNode
  iconClass: string
  text: string
  link?: string
  time: string
  sortTime: number
}

interface Props {
  workers: Session[]
  tasks: Task[]
}

const ICON_SIZE = 12

export default function RecentActivity({ workers, tasks }: Props) {
  const taskItems: ActivityItem[] = []
  const workerGroups: Record<string, { icon: ReactNode; iconClass: string; label: string; workers: { id: string; name: string; ts: number; time: string }[] }> = {}

  // Derive activity from recently updated tasks
  for (const t of tasks) {
    const ts = new Date(t.updated_at).getTime()
    const ago = Date.now() - ts
    if (ago > 7 * 24 * 60 * 60 * 1000) continue // skip older than 7 days

    if (t.status === 'done' || t.status === 'completed') {
      taskItems.push({
        id: `task-done-${t.id}`,
        icon: <IconCheck size={ICON_SIZE} />,
        iconClass: 'done',
        text: `${t.task_key || 'Task'} completed — ${t.title}`,
        link: `/tasks/${t.id}`,
        time: t.updated_at,
        sortTime: ts,
      })
    } else if (t.status === 'in_progress' && t.assigned_session_id) {
      // Only show "picked up" for tasks that were recently created — for older
      // tasks updated_at drifts with note/link edits and the event looks stale.
      const createdTs = new Date(t.created_at).getTime()
      const createdAgo = Date.now() - createdTs
      if (createdAgo <= 48 * 60 * 60 * 1000) {
        const worker = workers.find(w => w.id === t.assigned_session_id)
        const workerName = worker ? worker.name.split('_').pop() : 'worker'
        taskItems.push({
          id: `task-active-${t.id}`,
          icon: <IconArrowRight size={ICON_SIZE} />,
          iconClass: 'info',
          text: `${t.task_key || 'Task'} picked up by ${workerName}`,
          link: `/tasks/${t.id}`,
          time: t.updated_at,
          sortTime: ts,
        })
      }
    }
  }

  // Collect worker status changes for grouping
  for (const w of workers) {
    const ts = w.last_status_changed_at ? new Date(w.last_status_changed_at).getTime() : 0
    if (!ts) continue
    const ago = Date.now() - ts
    if (ago > 24 * 60 * 60 * 1000) continue // only last 24h

    const shortName = w.name.includes('_') ? w.name.split('_').pop()! : w.name

    let groupKey = ''
    if (w.status === 'disconnected') groupKey = 'disconnected'
    else if (w.status === 'waiting') groupKey = 'waiting'
    else if (w.status === 'working') groupKey = 'working'
    else continue

    if (!workerGroups[groupKey]) {
      const cfg: Record<string, { icon: ReactNode; iconClass: string; label: string }> = {
        disconnected: { icon: <IconX size={ICON_SIZE} />, iconClass: 'error', label: 'disconnected' },
        waiting: { icon: <IconPause size={ICON_SIZE} />, iconClass: 'warn', label: 'waiting for input' },
        working: { icon: <IconPlay size={ICON_SIZE} />, iconClass: 'info', label: 'started working' },
      }
      workerGroups[groupKey] = { ...cfg[groupKey], workers: [] }
    }
    workerGroups[groupKey].workers.push({ id: w.id, name: shortName, ts, time: w.last_status_changed_at! })
  }

  // Convert worker groups into display items
  const workerItems: ActivityItem[] = []
  for (const [groupKey, group] of Object.entries(workerGroups)) {
    if (group.workers.length === 1) {
      // Single worker — show individually with link
      const w = group.workers[0]
      workerItems.push({
        id: `worker-${groupKey}-${w.id}`,
        icon: group.icon,
        iconClass: group.iconClass,
        text: `${w.name} ${group.label}`,
        link: `/workers/${w.id}`,
        time: w.time,
        sortTime: w.ts,
      })
    } else {
      // Multiple workers — group into one line
      const newest = group.workers.reduce((a, b) => a.ts > b.ts ? a : b)
      const names = group.workers.map(w => w.name)
      const text = names.length <= 3
        ? `${names.join(', ')} ${group.label}`
        : `${names.length} workers ${group.label}`
      workerItems.push({
        id: `worker-group-${groupKey}`,
        icon: group.icon,
        iconClass: group.iconClass,
        text,
        link: '/workers',
        time: newest.time,
        sortTime: newest.ts,
      })
    }
  }

  // Merge, sort, limit
  const all = [...taskItems, ...workerItems]
  all.sort((a, b) => b.sortTime - a.sortTime)
  const display = all.slice(0, 8)

  if (display.length === 0) return null

  return (
    <CollapsiblePanel id="recent-activity" className="recent-activity" title="Recent Activity">
      <div className="ra-list">
        {display.map(item => (
          <div key={item.id} className="ra-item">
            <span className={`ra-icon ra-icon-${item.iconClass}`}>
              {item.icon}
            </span>
            {item.link ? (
              <Link to={item.link} className="ra-text">{item.text}</Link>
            ) : (
              <span className="ra-text">{item.text}</span>
            )}
            <span className="ra-time">{timeAgo(item.time)}</span>
          </div>
        ))}
      </div>
    </CollapsiblePanel>
  )
}
