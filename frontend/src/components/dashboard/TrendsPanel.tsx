import { useTrends } from '../../hooks/useTrends'
import ThroughputChart from './ThroughputChart'
import WorkerHeatmap from './WorkerHeatmap'
import WorkerHoursChart from './WorkerHoursChart'
import './TrendsPanel.css'

const RANGES = ['7d', '30d', '90d'] as const

export default function TrendsPanel() {
  const { data, loading, range, setRange } = useTrends()

  const hasData = data && (
    data.throughput.length > 0 ||
    data.heatmap.length > 0 ||
    data.worker_hours.length > 0
  )

  return (
    <section className="trends-panel panel">
      <div className="panel-header">
        <h2>Trends</h2>
        <div className="toggle-group toggle-sm">
          {RANGES.map(r => (
            <button
              key={r}
              className={`toggle-btn${range === r ? ' active' : ''}`}
              onClick={() => setRange(r)}
            >
              {r}
            </button>
          ))}
        </div>
      </div>
      {loading ? (
        <p className="empty-state">Loading trends...</p>
      ) : !hasData ? (
        <p className="empty-state">No activity data yet.</p>
      ) : (
        <div className="trends-body">
          <div className="trends-grid">
            <ThroughputChart data={data!.throughput} range={range} />
            <WorkerHeatmap data={data!.heatmap} />
            <WorkerHoursChart data={data!.worker_hours} range={range} />
          </div>
        </div>
      )}
    </section>
  )
}
