import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import Modal from '../common/Modal'
import { api } from '../../api/client'
import type {
  TrendDetailSelection,
  ThroughputDetailItem,
  WorkerHoursDetailItem,
  HeatmapDetailItem,
} from '../../api/types'
import './TrendDetailModal.css'

interface Props {
  selection: TrendDetailSelection | null
  range: string
  onClose: () => void
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
    return `Worker Hours — ${formatDate(selection.date)}${tz}`
  }
  const local = utcToLocal(selection.day_of_week, selection.hour)
  const dayName = DAY_NAMES[local.dayOfWeek]
  const tz = TZ_SHORT ? ` ${TZ_SHORT}` : ''
  return `Activity — ${dayName}s at ${formatHour(local.hour)}${tz}`
}

export function buildDetailQuery(selection: TrendDetailSelection, range: string): string {
  const params = new URLSearchParams({ chart: selection.chart })
  if (selection.chart === 'throughput' || selection.chart === 'worker_hours') {
    params.set('date', selection.date)
  } else {
    params.set('day_of_week', String(selection.day_of_week))
    params.set('hour', String(selection.hour))
    params.set('range', range)
  }
  return `/api/trends/detail?${params}`
}

export default function TrendDetailModal({ selection, range, onClose }: Props) {
  const [loading, setLoading] = useState(false)
  // Keep chart type paired with its items so a stale render never
  // dispatches to the wrong content component.
  const [result, setResult] = useState<{ chart: string; items: unknown[] } | null>(null)

  useEffect(() => {
    if (!selection) return
    setLoading(true)
    api<{ items: unknown[] }>(buildDetailQuery(selection, range))
      .then(res => setResult({ chart: selection.chart, items: res.items }))
      .catch(() => setResult({ chart: selection.chart, items: [] }))
      .finally(() => setLoading(false))
  }, [selection, range])

  if (!selection) return null

  const showLoading = loading || !result || result.chart !== selection.chart

  return (
    <Modal open={!!selection} onClose={onClose} title={getTitle(selection)} wide>
      <div className="trend-detail-body">
        {showLoading ? (
          <p className="trend-detail-empty">Loading...</p>
        ) : result.items.length === 0 ? (
          <p className="trend-detail-empty">No data for this selection</p>
        ) : result.chart === 'throughput' ? (
          <ThroughputContent items={result.items as ThroughputDetailItem[]} />
        ) : result.chart === 'worker_hours' ? (
          <WorkerHoursContent items={result.items as WorkerHoursDetailItem[]} />
        ) : (
          <HeatmapContent items={result.items as HeatmapDetailItem[]} />
        )}
      </div>
    </Modal>
  )
}

function ThroughputContent({ items }: { items: ThroughputDetailItem[] }) {
  const tasks = items.filter(i => !i.is_subtask)
  const subtasks = items.filter(i => i.is_subtask)

  return (
    <>
      <p className="trend-detail-summary">
        {tasks.length} task{tasks.length !== 1 ? 's' : ''}, {subtasks.length} subtask{subtasks.length !== 1 ? 's' : ''}
      </p>

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
    </>
  )
}

function WorkerHoursContent({ items }: { items: WorkerHoursDetailItem[] }) {
  const totalHours = items.reduce((s, i) => s + i.total_hours, 0)

  return (
    <>
      <div className="worker-hours-table">
        {/* Shared time axis with summary in the label column */}
        <div className="worker-hours-axis-row">
          <div className="worker-hours-label-col">
            <span className="worker-hours-summary">
              {totalHours.toFixed(1)}h total, {items.length} worker{items.length !== 1 ? 's' : ''}
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

        {/* Worker rows */}
        {items.map(item => (
          <div key={item.session_id} className="worker-hours-row">
            <div className="worker-hours-label-col">
              <div className="worker-hours-label">
                <Link to={`/workers/${item.session_id}`} className="worker-hours-name" onClick={e => e.stopPropagation()}>
                  {item.session_name}
                </Link>
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

function TimelineBar({ intervals }: { intervals: { start: string; end: string }[] }) {
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
        const endHour = end.getHours() + end.getMinutes() / 60
        const left = (startHour / 24) * 100
        const width = Math.max(((endHour - startHour) / 24) * 100, 0.5)
        return (
          <div
            key={i}
            className="timeline-segment"
            style={{ left: `${left}%`, width: `${width}%` }}
          />
        )
      })}
    </div>
  )
}

function HeatmapContent({ items }: { items: HeatmapDetailItem[] }) {
  // Group by date
  const byDate = new Map<string, HeatmapDetailItem[]>()
  for (const item of items) {
    const existing = byDate.get(item.date)
    if (existing) existing.push(item)
    else byDate.set(item.date, [item])
  }
  const dates = Array.from(byDate.keys())

  return (
    <>
      <p className="trend-detail-summary">
        {items.length} event{items.length !== 1 ? 's' : ''}
      </p>

      {dates.map(date => (
        <div key={date} className="heatmap-date-group">
          <h4 className="heatmap-date-header">{formatDate(date)}</h4>
          {byDate.get(date)!.map((item, i) => (
            <div key={i} className="trend-detail-row">
              <Link to={`/workers/${item.session_id}`} className="trend-detail-key" onClick={e => e.stopPropagation()}>
                {item.session_name}
              </Link>
              <span className="trend-detail-time">{formatTime(item.timestamp)}</span>
            </div>
          ))}
        </div>
      ))}
    </>
  )
}
