import { useState, useMemo } from 'react'
import type { HeatmapCell } from '../../api/types'

interface Props {
  data: HeatmapCell[]
  onCellClick?: (dayOfWeek: number, hour: number) => void
}

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const HOUR_LABELS = [0, 3, 6, 9, 12, 15, 18, 21]

const CELL_SIZE = 18
const GAP = 2
const LABEL_W = 30
const LABEL_H = 16

const TZ_SHORT = Intl.DateTimeFormat(undefined, { timeZoneName: 'short' })
  .formatToParts(new Date())
  .find(p => p.type === 'timeZoneName')?.value || ''

export default function WorkerHeatmap({ data, onCellClick }: Props) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null)

  const { grid, maxCount } = useMemo(() => {
    const g: number[][] = Array.from({ length: 7 }, () => Array(24).fill(0))
    let max = 0
    for (const cell of data) {
      // Convert UTC day/hour to local timezone for display
      const ref = new Date(Date.UTC(2025, 0, 5 + cell.day_of_week, cell.hour))
      const localDay = ref.getDay()
      const localHour = ref.getHours()
      g[localDay][localHour] += cell.count
      if (g[localDay][localHour] > max) max = g[localDay][localHour]
    }
    return { grid: g, maxCount: max }
  }, [data])

  if (maxCount === 0) return null

  const svgW = LABEL_W + 24 * (CELL_SIZE + GAP)
  const svgH = LABEL_H + 7 * (CELL_SIZE + GAP)

  function cellColor(count: number): string {
    if (count === 0) return 'var(--surface-raised)'
    const opacity = 0.2 + 0.8 * (count / maxCount)
    return `rgba(88, 166, 255, ${opacity.toFixed(2)})`
  }

  function dayName(dow: number): string {
    return ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][dow]
  }

  function hourLabel(h: number): string {
    if (h === 0) return '12am'
    if (h < 12) return `${h}am`
    if (h === 12) return '12pm'
    return `${h - 12}pm`
  }

  return (
    <div className="trends-chart" style={{ position: 'relative' }}>
      <div className="trends-chart-header">
        <span className="trends-chart-title">Worker Activity{TZ_SHORT ? ` (${TZ_SHORT})` : ''}</span>
      </div>
      <svg width={svgW} height={svgH} style={{ display: 'block', maxWidth: '100%' }}>
        {/* Hour labels */}
        {HOUR_LABELS.map(h => (
          <text
            key={`h-${h}`}
            x={LABEL_W + h * (CELL_SIZE + GAP) + CELL_SIZE / 2}
            y={LABEL_H - 4}
            textAnchor="middle"
            fill="var(--text-muted)"
            fontSize={10}
          >
            {h}
          </text>
        ))}
        {/* Day labels + cells */}
        {grid.map((row, day) => (
          <g key={`d-${day}`}>
            <text
              x={LABEL_W - 6}
              y={LABEL_H + day * (CELL_SIZE + GAP) + CELL_SIZE / 2 + 4}
              textAnchor="end"
              fill="var(--text-muted)"
              fontSize={10}
            >
              {DAY_LABELS[day]}
            </text>
            {row.map((count, hour) => (
              <rect
                key={`c-${day}-${hour}`}
                x={LABEL_W + hour * (CELL_SIZE + GAP)}
                y={LABEL_H + day * (CELL_SIZE + GAP)}
                width={CELL_SIZE}
                height={CELL_SIZE}
                rx={3}
                fill={cellColor(count)}
                style={{ cursor: count > 0 && onCellClick ? 'pointer' : undefined }}
                onClick={() => {
                  if (count > 0 && onCellClick) {
                    // Convert local day/hour back to UTC for the API
                    const ref = new Date(2025, 0, 5 + day, hour, 0, 0, 0)
                    onCellClick(ref.getUTCDay(), ref.getUTCHours())
                  }
                }}
                onMouseEnter={(e) => {
                  const rect = (e.target as SVGRectElement).getBoundingClientRect()
                  const parent = (e.target as SVGRectElement).closest('.trends-chart')!.getBoundingClientRect()
                  setTooltip({
                    x: rect.left - parent.left + rect.width / 2,
                    y: rect.top - parent.top - 4,
                    text: `${dayName(day)} ${hourLabel(hour)}: ${count} event${count !== 1 ? 's' : ''}`,
                  })
                }}
                onMouseLeave={() => setTooltip(null)}
              />
            ))}
          </g>
        ))}
      </svg>
      {tooltip && (
        <div
          className="heatmap-tooltip"
          style={{
            position: 'absolute',
            left: tooltip.x,
            top: tooltip.y,
            transform: 'translate(-50%, -100%)',
            pointerEvents: 'none',
          }}
        >
          {tooltip.text}
        </div>
      )}
    </div>
  )
}
