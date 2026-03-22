import { useMemo } from 'react'
import { ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import type { ThroughputDay } from '../../api/types'

interface Props {
  data: ThroughputDay[]
  range: string
  onBarClick?: (date: string) => void
}

/** Format a Date as YYYY-MM-DD in local timezone. */
function localYMD(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Fill missing days in the range with zeros, newest first (reversed).
 *  Reversed order keeps recent-day indices stable across range switches,
 *  so Recharts animates new (older) bars in at the end of the array.
 *  The XAxis `reversed` prop flips the visual back to chronological order. */
function fillDays(data: ThroughputDay[], rangeDays: number): ThroughputDay[] {
  const map = new Map(data.map(d => [d.date, d]))
  const result: ThroughputDay[] = []
  const now = new Date()
  for (let i = 0; i < rangeDays; i++) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    const key = localYMD(d)
    result.push(map.get(key) || { date: key, tasks: 0, subtasks: 0, prs: 0 })
  }
  return result
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function formatDateWithWeekday(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

export default function ThroughputChart({ data, range, onBarClick }: Props) {
  const rangeDays = range === '90d' ? 90 : range === '30d' ? 30 : 7
  const filled = useMemo(() => fillDays(data, rangeDays), [data, rangeDays])

  const hasData = data.length > 0
  const hasPrs = filled.some(d => (d.prs ?? 0) > 0)

  const avgTasks = useMemo(() => {
    const total = filled.reduce((s, d) => s + d.tasks + d.subtasks, 0)
    return (total / filled.length).toFixed(1)
  }, [filled])

  const avgPrs = useMemo(() => {
    const total = filled.reduce((s, d) => s + (d.prs ?? 0), 0)
    return (total / filled.length).toFixed(1)
  }, [filled])

  if (!hasData) return null

  return (
    <div className="trends-chart">
      <div className="trends-chart-header">
        <span className="trends-chart-title">Task Throughput</span>
        <span className="trends-chart-stat">
          {avgTasks} tasks/day{hasPrs ? `, ${avgPrs} PRs/day` : ''} avg ({range})
        </span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart
          data={filled}
          margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
          onClick={(state: any) => {
            if (onBarClick && state?.activeLabel) {
              onBarClick(String(state.activeLabel))
            }
          }}
          style={{ cursor: onBarClick ? 'pointer' : undefined }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="date"
            reversed
            tickFormatter={formatDate}
            tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
            axisLine={{ stroke: 'var(--border)' }}
            tickLine={false}
            interval={rangeDays > 14 ? Math.floor(rangeDays / 7) - 1 : 0}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: '6px',
              fontSize: '12px',
              color: 'var(--text-primary)',
            }}
            labelFormatter={(label) => formatDateWithWeekday(String(label))}
            cursor={{ fill: 'var(--surface-hover)' }}
          />
          <Bar
            dataKey="tasks"
            stackId="a"
            fill="var(--fill-accent)"
            name="Tasks"
            radius={[0, 0, 0, 0]}
            animationDuration={400}
            animationEasing="ease-out"
            onClick={(data: any) => onBarClick?.(data?.payload?.date)}
            style={{ cursor: onBarClick ? 'pointer' : undefined }}
          />
          <Bar
            dataKey="subtasks"
            stackId="a"
            fill="var(--fill-purple)"
            name="Subtasks"
            radius={[2, 2, 0, 0]}
            animationDuration={400}
            animationEasing="ease-out"
            onClick={(data: any) => onBarClick?.(data?.payload?.date)}
            style={{ cursor: onBarClick ? 'pointer' : undefined }}
          />
          {hasPrs && (
            <Line
              dataKey="prs"
              name="PRs Merged"
              type="monotone"
              stroke="var(--fill-green)"
              strokeWidth={2}
              dot={{ r: 3, fill: 'var(--fill-green)', strokeWidth: 0 }}
              activeDot={{ r: 5, fill: 'var(--fill-green)', strokeWidth: 2, stroke: 'var(--surface)' }}
              animationDuration={400}
              animationEasing="ease-out"
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
