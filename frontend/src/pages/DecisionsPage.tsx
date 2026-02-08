import { useState } from 'react'
import { useDecisions } from '../hooks/useDecisions'
import DecisionCard from '../components/decisions/DecisionCard'
import { timeAgo } from '../components/common/TimeAgo'
import './DecisionsPage.css'

export default function DecisionsPage() {
  const [tab, setTab] = useState<'pending' | 'history'>('pending')
  const { decisions: pending, loading: loadingPending } = useDecisions({ status: 'pending' })
  const { decisions: history, loading: loadingHistory } = useDecisions(
    tab === 'history' ? {} : undefined
  )

  const resolved = history.filter(d => d.status !== 'pending')

  // Sort pending by urgency
  const urgencyOrder = { critical: 0, high: 1, normal: 2, low: 3 }
  const sortedPending = [...pending].sort((a, b) =>
    urgencyOrder[a.urgency] - urgencyOrder[b.urgency]
  )

  return (
    <div className="decisions-page">
      <div className="page-header">
        <h1>Decisions</h1>
      </div>

      <div className="tabs">
        <button
          className={`tab ${tab === 'pending' ? 'active' : ''}`}
          onClick={() => setTab('pending')}
        >
          Pending ({pending.length})
        </button>
        <button
          className={`tab ${tab === 'history' ? 'active' : ''}`}
          onClick={() => setTab('history')}
        >
          History
        </button>
      </div>

      {tab === 'pending' ? (
        loadingPending ? (
          <p className="empty-state">Loading...</p>
        ) : sortedPending.length === 0 ? (
          <p className="empty-state">No pending decisions. All clear!</p>
        ) : (
          <div className="decision-list">
            {sortedPending.map(d => (
              <DecisionCard key={d.id} decision={d} />
            ))}
          </div>
        )
      ) : (
        loadingHistory ? (
          <p className="empty-state">Loading history...</p>
        ) : resolved.length === 0 ? (
          <p className="empty-state">No decision history yet</p>
        ) : (
          <div className="decision-history">
            {resolved.map(d => (
              <div key={d.id} className="dh-item">
                <div className="dh-question">{d.question}</div>
                <div className="dh-meta">
                  <span className={`status-badge ${d.status}`}>{d.status}</span>
                  {d.response && <span className="dh-response">{d.response}</span>}
                  <span className="dh-time">{timeAgo(d.created_at)}</span>
                </div>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  )
}
