import './ProgressBar.css'

interface Props {
  done: number
  total: number
}

export default function ProgressBar({ done, total }: Props) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div className="progress-bar">
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="progress-label">{done}/{total} ({pct}%)</span>
    </div>
  )
}
