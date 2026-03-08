import { useState } from 'react'
import { useTrends } from '../../hooks/useTrends'
import ThroughputChart from './ThroughputChart'
import WorkerHeatmap from './WorkerHeatmap'
import WorkerHoursChart from './WorkerHoursChart'
import TrendDetailModal from './TrendDetailModal'
import CollapsiblePanel from './CollapsiblePanel'
import SlidingTabs from '../common/SlidingTabs'
import type { TrendDetailSelection } from '../../api/types'
import './TrendsPanel.css'

const RANGES = ['7d', '30d', '90d'] as const
const RANGE_TABS = RANGES.map(r => ({ value: r, label: r }))

export default function TrendsPanel() {
  const { data, loading, range, setRange } = useTrends()
  const [detailSelection, setDetailSelection] = useState<TrendDetailSelection | null>(null)

  const hasData = data && (
    data.throughput.length > 0 ||
    data.heatmap.length > 0 ||
    data.worker_hours.length > 0
  )

  return (
    <CollapsiblePanel
      id="trends"
      className="trends-panel"
      title="Trends"
      actions={
        <SlidingTabs
          tabs={RANGE_TABS}
          value={range}
          onChange={setRange}
        />
      }
    >
      {loading ? (
        <p className="empty-state">Loading trends...</p>
      ) : !hasData ? (
        <p className="empty-state">No activity data yet.</p>
      ) : (
        <div className="trends-body">
          <ThroughputChart
            data={data!.throughput}
            range={range}
            onBarClick={(date) => setDetailSelection({ chart: 'throughput', date })}
          />
          <div className="trends-bottom-row">
            <WorkerHeatmap
              data={data!.heatmap}
              onCellClick={(day_of_week, hour) => setDetailSelection({ chart: 'heatmap', day_of_week, hour })}
            />
            <WorkerHoursChart
              data={data!.worker_hours}
              range={range}
              onPointClick={(date) => setDetailSelection({ chart: 'worker_hours', date })}
            />
          </div>
          <TrendDetailModal
            selection={detailSelection}
            range={range}
            onClose={() => setDetailSelection(null)}
          />
        </div>
      )}
    </CollapsiblePanel>
  )
}
