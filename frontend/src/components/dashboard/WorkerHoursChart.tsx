import { useMemo } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import type { WorkerHoursDay } from '../../api/types'

interface Props {
  data: WorkerHoursDay[]
  range: string
  onPointClick?: (date: string) => void
}

/** Format a Date as YYYY-MM-DD in local timezone. */
function localYMD(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Fill missing days with zeros, newest first (reversed).
 *  See ThroughputChart for rationale on reversed order. */
function fillDays(data: WorkerHoursDay[], rangeDays: number): WorkerHoursDay[] {
  const map = new Map(data.map(d => [d.date, d]))
  const result: WorkerHoursDay[] = []
  const now = new Date()
  for (let i = 0; i < rangeDays; i++) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    const key = localYMD(d)
    result.push(map.get(key) || { date: key, hours: 0 })
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

export default function WorkerHoursChart({ data, range, onPointClick }: Props) {
  const rangeDays = range === '90d' ? 90 : range === '30d' ? 30 : 7
  const filled = useMemo(() => fillDays(data, rangeDays), [data, rangeDays])

  const hasData = data.length > 0

  // Daily average over selected range
  const avg = useMemo(() => {
    const total = filled.reduce((s, d) => s + d.hours, 0)
    return (total / filled.length).toFixed(1)
  }, [filled])

  if (!hasData) return null

  return (
    <div className="trends-chart">
      <div className="trends-chart-header">
        <span className="trends-chart-title">Worker-Hours</span>
        <span className="trends-chart-stat">{avg}h/day avg ({range})</span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart
          data={filled}
          margin={{ top: 4, right: 4, bottom: 0, left: -20 }}
          onClick={(state: any) => {
            if (onPointClick && state?.activeLabel) {
              onPointClick(String(state.activeLabel))
            }
          }}
          style={{ cursor: onPointClick ? 'pointer' : undefined }}
        >
          <defs>
            <linearGradient id="workerHoursGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--green)" stopOpacity={0.3} />
              <stop offset="95%" stopColor="var(--green)" stopOpacity={0} />
            </linearGradient>
          </defs>
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
            tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => `${v}h`}
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
            formatter={(value) => [`${Number(value).toFixed(1)}h`, 'Hours']}
            cursor={{ stroke: 'var(--border)' }}
          />
          <Area
            type="monotone"
            dataKey="hours"
            stroke="var(--green)"
            strokeWidth={2}
            fill="url(#workerHoursGradient)"
            animationDuration={400}
            animationEasing="ease-out"
            activeDot={onPointClick ? ((props: any) => {
              const { cx, cy, payload } = props
              return (
                <circle
                  cx={cx}
                  cy={cy}
                  r={5}
                  stroke="var(--green)"
                  strokeWidth={2}
                  fill="var(--surface)"
                  style={{ cursor: 'pointer' }}
                  onClick={() => onPointClick(payload.date)}
                />
              )
            }) : undefined}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
