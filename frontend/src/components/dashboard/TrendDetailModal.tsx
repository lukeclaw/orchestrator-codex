import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import Modal from '../common/Modal'
import { api, openUrl } from '../../api/client'
import type {
  TrendDetailSelection,
  ThroughputDetailItem,
  WorkerHoursDetailItem,
  HeatmapDetailItem,
  HumanHoursDetailItem,
  PrMergeItem,
} from '../../api/types'
import './TrendDetailModal.css'

interface Props {
  selection: TrendDetailSelection | null
  range: string
  onClose: () => void
  prDetailByDay?: Record<string, PrMergeItem[]>
}

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

const TZ_SHORT = Intl.DateTimeFormat(undefined, { timeZoneName: 'short' })
  .formatToParts(new Date())
  .find(p => p.type === 'timeZoneName')?.value || ''

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function formatTime(ts: string): string {
  const d = new Date(ts)
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function formatHour(h: number): string {
  if (h === 0) return '12 AM'
  if (h < 12) return `${h} AM`
  if (h === 12) return '12 PM'
  return `${h - 12} PM`
}

/** Convert a UTC day_of_week (0=Sun) + hour to the client's local equivalent. */
function utcToLocal(dayOfWeek: number, hour: number): { dayOfWeek: number; hour: number } {
  // Pick a known Sunday (Jan 5, 2025) then add dayOfWeek days and set UTC hour
  const d = new Date(Date.UTC(2025, 0, 5 + dayOfWeek, hour))
  return { dayOfWeek: d.getDay(), hour: d.getHours() }
}

function getTitle(selection: TrendDetailSelection): string {
  if (selection.chart === 'throughput') {
    return `Completed — ${formatDate(selection.date)}`
  }
  if (selection.chart === 'worker_hours') {
    const tz = TZ_SHORT ? ` (${TZ_SHORT})` : ''
    return `Hours — ${formatDate(selection.date)}${tz}`
  }
  if (selection.chart === 'human_hours') {
    const tz = TZ_SHORT ? ` (${TZ_SHORT})` : ''
    return `Your Hours — ${formatDate(selection.date)}${tz}`
  }
  const local = utcToLocal(selection.day_of_week, selection.hour)
  const dayName = DAY_NAMES[local.dayOfWeek]
  const tz = TZ_SHORT ? ` ${TZ_SHORT}` : ''
  return `Activity — ${dayName}s at ${formatHour(local.hour)}${tz}`
}

export function buildDetailQuery(selection: TrendDetailSelection, range: string): string {
  const params = new URLSearchParams({ chart: selection.chart })
  if (selection.chart === 'throughput' || selection.chart === 'worker_hours' || selection.chart === 'human_hours') {
    params.set('date', selection.date)
  } else {
    params.set('day_of_week', String(selection.day_of_week))
    params.set('hour', String(selection.hour))
    params.set('range', range)
  }
  return `/api/trends/detail?${params}`
}

export default function TrendDetailModal({ selection, range, onClose, prDetailByDay }: Props) {
  const [loading, setLoading] = useState(false)
  // Keep chart type paired with its items so a stale render never
  // dispatches to the wrong content component.
  const [result, setResult] = useState<{ chart: string; items: unknown[] } | null>(null)
  const [humanItems, setHumanItems] = useState<HumanHoursDetailItem[]>([])

  useEffect(() => {
    if (!selection) return
    setLoading(true)
    setHumanItems([])

    const mainFetch = api<{ items: unknown[] }>(buildDetailQuery(selection, range))
      .then(res => setResult({ chart: selection.chart, items: res.items }))
      .catch(() => setResult({ chart: selection.chart, items: [] }))

    // When opening worker_hours detail, also fetch human hours for the same date
    if (selection.chart === 'worker_hours') {
      const humanFetch = api<{ items: HumanHoursDetailItem[] }>(
        `/api/trends/detail?chart=human_hours&date=${selection.date}`
      )
        .then(res => setHumanItems(res.items))
        .catch(() => setHumanItems([]))

      Promise.all([mainFetch, humanFetch]).finally(() => setLoading(false))
    } else {
      mainFetch.finally(() => setLoading(false))
    }
  }, [selection, range])

  if (!selection) return null

  const showLoading = loading || !result || result.chart !== selection.chart

  return (
    <Modal open={!!selection} onClose={onClose} title={getTitle(selection)} wide>
      <div className="trend-detail-body">
        {showLoading ? (
          <p className="trend-detail-empty">Loading...</p>
        ) : result.items.length === 0 && humanItems.length === 0 ? (
          <p className="trend-detail-empty">No data for this selection</p>
        ) : result.chart === 'throughput' ? (
          <ThroughputContent
            items={result.items as ThroughputDetailItem[]}
            prItems={selection.chart === 'throughput' ? prDetailByDay?.[selection.date] : undefined}
          />
        ) : result.chart === 'worker_hours' ? (
          <WorkerHoursContent items={result.items as WorkerHoursDetailItem[]} humanItems={humanItems} />
        ) : result.chart === 'human_hours' ? (
          <HumanHoursContent items={result.items as HumanHoursDetailItem[]} />
        ) : (
          <HeatmapContent items={result.items as HeatmapDetailItem[]} />
        )}
      </div>
    </Modal>
  )
}

function ThroughputContent({ items, prItems }: { items: ThroughputDetailItem[]; prItems?: PrMergeItem[] }) {
  const tasks = items.filter(i => !i.is_subtask)
  const subtasks = items.filter(i => i.is_subtask)
  const prs = prItems || []

  const parts: string[] = []
  if (tasks.length > 0) parts.push(`${tasks.length} task${tasks.length !== 1 ? 's' : ''}`)
  if (subtasks.length > 0) parts.push(`${subtasks.length} subtask${subtasks.length !== 1 ? 's' : ''}`)
  if (prs.length > 0) parts.push(`${prs.length} PR${prs.length !== 1 ? 's' : ''} merged`)

  return (
    <>
      <p className="trend-detail-summary">{parts.join(', ')}</p>

      {tasks.length > 0 && (
        <div className="trend-detail-section">
          <h4 className="trend-detail-section-title">Tasks</h4>
          {tasks.map(item => (
            <div key={item.entity_id} className="trend-detail-row">
              <span className="trend-detail-type-badge badge-task">task</span>
              <Link to={`/tasks/${item.entity_id}`} className="trend-detail-key" onClick={e => e.stopPropagation()}>
                {item.task_key || item.entity_id.slice(0, 8)}
              </Link>
              <span className="trend-detail-title">{item.title}</span>
              <span className="trend-detail-time">{formatTime(item.timestamp)}</span>
            </div>
          ))}
        </div>
      )}

      {subtasks.length > 0 && (
        <div className="trend-detail-section">
          <h4 className="trend-detail-section-title">Subtasks</h4>
          {subtasks.map(item => (
            <div key={item.entity_id} className="trend-detail-row">
              <span className="trend-detail-type-badge badge-subtask">subtask</span>
              <Link to={`/tasks/${item.entity_id}`} className="trend-detail-key" onClick={e => e.stopPropagation()}>
                {item.task_key || item.entity_id.slice(0, 8)}
              </Link>
              <span className="trend-detail-title">{item.title}</span>
              {item.parent_task_key && (
                <span className="trend-detail-parent">
                  <Link to={`/tasks/${item.parent_task_id}`} onClick={e => e.stopPropagation()}>
                    {item.parent_task_key}
                  </Link>
                </span>
              )}
              <span className="trend-detail-time">{formatTime(item.timestamp)}</span>
            </div>
          ))}
        </div>
      )}

      {prs.length > 0 && (
        <div className="trend-detail-section">
          <h4 className="trend-detail-section-title">PRs Merged</h4>
          {prs.map(pr => (
            <div key={pr.url} className="trend-detail-row">
              <span className="trend-detail-type-badge badge-pr">PR</span>
              <a
                href={pr.url}
                className="trend-detail-key"
                onClick={e => { e.preventDefault(); e.stopPropagation(); openUrl(pr.url) }}
              >
                {pr.repo}#{pr.number}
              </a>
              <span className="trend-detail-title">{pr.title}</span>
              <span className="trend-detail-diff">
                <span className="diff-add">+{pr.additions}</span>
                <span className="diff-del">-{pr.deletions}</span>
              </span>
              <span className="trend-detail-time">{formatTime(pr.merged_at)}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

function WorkerHoursContent({ items, humanItems = [] }: { items: WorkerHoursDetailItem[]; humanItems?: HumanHoursDetailItem[] }) {
  const totalWorkerHours = items.reduce((s, i) => s + i.total_hours, 0)
  const totalHumanHours = humanItems.reduce((s, i) => s + i.hours, 0)
  const hasHuman = humanItems.length > 0

  const summaryParts: string[] = []
  summaryParts.push(`${items.length} worker${items.length !== 1 ? 's' : ''}: ${totalWorkerHours.toFixed(1)}h`)

  return (
    <>
      <div className="worker-hours-table">
        {/* Shared time axis with summary in the label column */}
        <div className="worker-hours-axis-row">
          <div className="worker-hours-label-col">
            <span className="worker-hours-summary">
              {summaryParts.join(' · ')}
            </span>
          </div>
          <div className="worker-hours-timeline-col">
            <div className="shared-axis">
              {[0, 6, 12, 18, 24].map(h => (
                <span key={h} className="shared-axis-label" style={{ left: `${(h / 24) * 100}%` }}>
                  {h}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Human hours row on top */}
        {hasHuman && (
          <div className="worker-hours-row">
            <div className="worker-hours-label-col">
              <div className="worker-hours-label">
                <span className="worker-hours-name" style={{ color: 'var(--accent)', cursor: 'default' }}>You</span>
                <span className="worker-hours-hours">{totalHumanHours.toFixed(1)}h</span>
              </div>
            </div>
            <div className="worker-hours-timeline-col">
              <TimelineBar intervals={humanItems} variant="human" />
            </div>
          </div>
        )}

        {/* Worker rows */}
        {items.map(item => (
          <div key={item.session_id} className="worker-hours-row">
            <div className="worker-hours-label-col">
              <div className="worker-hours-label">
                {item.deleted ? (
                  <span className="worker-hours-name deleted">{item.session_name}</span>
                ) : (
                  <Link to={`/workers/${item.session_id}`} className="worker-hours-name" onClick={e => e.stopPropagation()}>
                    {item.session_name}
                  </Link>
                )}
                <span className="worker-hours-hours">{item.total_hours.toFixed(1)}h</span>
              </div>
              {item.current_task && (
                <span className="worker-hours-task-inline">
                  <Link to={`/tasks/${item.current_task.id}`} onClick={e => e.stopPropagation()}>
                    {item.current_task.title}
                  </Link>
                </span>
              )}
            </div>
            <div className="worker-hours-timeline-col">
              <TimelineBar intervals={item.intervals} />
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

function HumanHoursContent({ items }: { items: HumanHoursDetailItem[] }) {
  const totalHours = items.reduce((s, i) => s + i.hours, 0)

  return (
    <>
      <p className="trend-detail-summary">
        {totalHours.toFixed(1)}h active, {items.length} session{items.length !== 1 ? 's' : ''}
      </p>
      <div className="worker-hours-table">
        <div className="worker-hours-axis-row">
          <div className="worker-hours-label-col">
            <span className="worker-hours-summary">
              {totalHours.toFixed(1)}h total
            </span>
          </div>
          <div className="worker-hours-timeline-col">
            <div className="shared-axis">
              {[0, 6, 12, 18, 24].map(h => (
                <span key={h} className="shared-axis-label" style={{ left: `${(h / 24) * 100}%` }}>
                  {h}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="worker-hours-row">
          <div className="worker-hours-label-col">
            <div className="worker-hours-label">
              <span className="worker-hours-name" style={{ color: 'var(--accent)', cursor: 'default' }}>You</span>
              <span className="worker-hours-hours">{totalHours.toFixed(1)}h</span>
            </div>
          </div>
          <div className="worker-hours-timeline-col">
            <TimelineBar intervals={items} variant="human" />
          </div>
        </div>
      </div>
    </>
  )
}

const TASK_COLORS = [
  'var(--green)',
  '#a78bfa',   // purple
  '#f59e0b',   // amber
  '#06b6d4',   // cyan
  '#f472b6',   // pink
  '#34d399',   // emerald
  '#fb923c',   // orange
  '#60a5fa',   // blue
]

function hashTaskColor(taskId: string): string {
  let h = 0
  for (let i = 0; i < taskId.length; i++) h = ((h << 5) - h + taskId.charCodeAt(i)) | 0
  return TASK_COLORS[Math.abs(h) % TASK_COLORS.length]
}

function TimelineBar({ intervals, variant }: { intervals: { start: string; end: string; task_id?: string; task_title?: string }[]; variant?: 'human' }) {
  // Collect unique task IDs to decide whether to color-code
  const taskIds = new Set(intervals.map(iv => iv.task_id).filter(Boolean))
  const useTaskColors = variant !== 'human' && taskIds.size > 1

  return (
    <div className="timeline-bar">
      <div className="timeline-track" />
      {[6, 12, 18].map(h => (
        <div key={h} className="timeline-gridline" style={{ left: `${(h / 24) * 100}%` }} />
      ))}
      {intervals.map((iv, i) => {
        const start = new Date(iv.start)
        const end = new Date(iv.end)
        const startHour = start.getHours() + start.getMinutes() / 60
        const durationHours = (end.getTime() - start.getTime()) / (1000 * 60 * 60)
        const left = (startHour / 24) * 100
        const width = Math.max((durationHours / 24) * 100, 0.5)

        const startTime = start.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
        const endTime = end.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
        const tooltipParts = [`${startTime} – ${endTime} (${durationHours.toFixed(1)}h)`]
        if (iv.task_title) tooltipParts.push(iv.task_title)

        const bg = variant === 'human'
          ? undefined
          : useTaskColors && iv.task_id
            ? hashTaskColor(iv.task_id)
            : undefined

        return (
          <div
            key={i}
            className={variant === 'human' ? 'timeline-segment timeline-segment-human' : 'timeline-segment'}
            style={{ left: `${left}%`, width: `${width}%`, ...(bg ? { background: bg } : {}) }}
            title={tooltipParts.join('\n')}
          />
        )
      })}
    </div>
  )
}

function HeatmapContent({ items }: { items: HeatmapDetailItem[] }) {
  // Group by date, then by worker within each date
  const byDate = new Map<string, Map<string, HeatmapDetailItem[]>>()
  for (const item of items) {
    let dateMap = byDate.get(item.date)
    if (!dateMap) {
      dateMap = new Map()
      byDate.set(item.date, dateMap)
    }
    const existing = dateMap.get(item.session_id)
    if (existing) existing.push(item)
    else dateMap.set(item.session_id, [item])
  }
  const dates = Array.from(byDate.keys())

  return (
    <>
      <div className="heatmap-table">
        {dates.map(date => {
          const workers = byDate.get(date)!
          return (
            <div key={date} className="heatmap-date-group">
              <div className="heatmap-date-header">
                <span>{formatDate(date)}</span>
                <span className="heatmap-date-count">{items.length} event{items.length !== 1 ? 's' : ''}</span>
              </div>
              {Array.from(workers.entries()).map(([sessionId, events]) => (
                <div key={sessionId} className="heatmap-worker-group">
                  <div className="heatmap-worker-header">
                    <Link to={`/workers/${sessionId}`} className="heatmap-worker-name" onClick={e => e.stopPropagation()}>
                      {events[0].session_name}
                    </Link>
                    {events.length > 1 && (
                      <span className="heatmap-worker-count">×{events.length}</span>
                    )}
                  </div>
                  <div className="heatmap-timestamps">
                    {events.map((ev, i) => (
                      <span key={i} className="heatmap-time-chip">{formatTime(ev.timestamp)}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )
        })}
      </div>
    </>
  )
}
