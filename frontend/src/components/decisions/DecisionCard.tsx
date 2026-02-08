import type { Decision } from '../../api/types'
import { timeAgo } from '../common/TimeAgo'
import { api } from '../../api/client'
import { useApp } from '../../context/AppContext'
import './DecisionCard.css'

interface Props {
  decision: Decision
}

export default function DecisionCard({ decision }: Props) {
  const { refresh } = useApp()

  const options: string[] = (() => {
    if (!decision.options) return []
    if (Array.isArray(decision.options)) return decision.options
    try {
      return JSON.parse(decision.options as string)
    } catch {
      return []
    }
  })()

  async function respond(response: string) {
    try {
      await api(`/api/decisions/${decision.id}/respond`, {
        method: 'POST',
        body: JSON.stringify({ response, resolved_by: 'user' }),
      })
      refresh()
    } catch (e) {
      console.error('Failed to respond:', e)
    }
  }

  async function dismiss() {
    try {
      await api(`/api/decisions/${decision.id}/dismiss`, { method: 'POST' })
      refresh()
    } catch (e) {
      console.error('Failed to dismiss:', e)
    }
  }

  return (
    <div
      className={`decision-card ${decision.urgency}`}
      data-testid="decision-card"
      data-decision-id={decision.id}
    >
      <div className="dc-question">{decision.question}</div>
      <div className="dc-meta">
        <span className={`urgency-tag ${decision.urgency}`}>{decision.urgency}</span>
        <span className="dc-time">{timeAgo(decision.created_at)}</span>
      </div>
      {decision.context && (
        <div className="dc-context">{decision.context}</div>
      )}
      <div className="dc-actions">
        {options.length > 0
          ? options.map(opt => (
              <button
                key={opt}
                className="btn btn-primary btn-sm"
                data-testid="approve-btn"
                onClick={() => respond(opt)}
              >
                {opt}
              </button>
            ))
          : (
              <button
                className="btn btn-primary btn-sm"
                data-testid="approve-btn"
                onClick={() => respond('Approved')}
              >
                Approve
              </button>
            )}
        <button
          className="btn btn-secondary btn-sm"
          data-testid="dismiss-btn"
          onClick={dismiss}
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
