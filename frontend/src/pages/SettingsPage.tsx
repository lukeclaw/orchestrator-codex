import { useState, useEffect } from 'react'
import { useSettings } from '../hooks/useSettings'
import './SettingsPage.css'

const TABS = ['General', 'Auto-Approve'] as const
type Tab = typeof TABS[number]

const AUTO_APPROVE_RULES = [
  {
    key: 'auto_approve.tool_calls',
    label: 'Tool Calls',
    hint: 'Auto-approve Claude Code tool calls (Read, Write, Bash, etc.)',
  },
  {
    key: 'auto_approve.continue_work',
    label: 'Continue Prompts',
    hint: 'Auto-approve "continue?" and "shall I proceed?" prompts',
  },
  {
    key: 'auto_approve.completed_check',
    label: 'Completion Checks',
    hint: 'Auto-approve "Has this been completed?" prompts',
  },
  {
    key: 'auto_approve.yes_no_prompts',
    label: 'Generic Y/N',
    hint: 'Auto-approve generic (y/n) prompts',
  },
]

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>('General')
  const { settings, loading, saving, save, getValue } = useSettings()

  // Local state for general settings
  const [pollingInterval, setPollingInterval] = useState(5)
  const [maxSessions, setMaxSessions] = useState(10)

  // Sync local state when settings load
  useEffect(() => {
    if (settings.length > 0) {
      const pi = getValue('general.polling_interval')
      if (pi != null) setPollingInterval(Number(pi))
      const ms = getValue('general.max_sessions')
      if (ms != null) setMaxSessions(Number(ms))
    }
  }, [settings, getValue])

  const handleSaveGeneral = () => {
    save({
      'general.polling_interval': pollingInterval,
      'general.max_sessions': maxSessions,
    })
  }

  const handleToggleRule = (key: string) => {
    const current = getValue(key)
    const enabled = current === true || current === 'true'
    save({ [key]: !enabled })
  }

  const isRuleEnabled = (key: string): boolean => {
    const v = getValue(key)
    return v === true || v === 'true'
  }

  return (
    <div className="settings-page">
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      <div className="tabs">
        {TABS.map(t => (
          <button
            key={t}
            className={`tab ${tab === t ? 'active' : ''}`}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="settings-content panel">
        <div className="panel-body">
          {loading && <p className="settings-hint">Loading settings...</p>}

          {!loading && tab === 'General' && (
            <div className="settings-section">
              <h3>General Configuration</h3>
              <div className="form-group">
                <label>Polling Interval (seconds)</label>
                <input
                  type="number"
                  value={pollingInterval}
                  onChange={e => setPollingInterval(Number(e.target.value))}
                  min={2}
                  max={60}
                />
                <span className="form-hint">How often the monitor checks terminal state</span>
              </div>
              <div className="form-group">
                <label>Max Concurrent Sessions</label>
                <input
                  type="number"
                  value={maxSessions}
                  onChange={e => setMaxSessions(Number(e.target.value))}
                  min={1}
                  max={50}
                />
                <span className="form-hint">Maximum number of worker sessions</span>
              </div>
              <button
                className="btn btn-primary"
                onClick={handleSaveGeneral}
                disabled={saving}
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          )}

          {!loading && tab === 'Auto-Approve' && (
            <div className="settings-section">
              <h3>Auto-Approve Rules</h3>
              <p className="settings-hint">
                When enabled, the orchestrator automatically responds to matching
                prompts so workers don't block waiting for you.
              </p>
              <div className="approval-rules">
                {AUTO_APPROVE_RULES.map(rule => (
                  <div key={rule.key} className="approval-rule">
                    <div className="rule-info">
                      <span className="rule-label">{rule.label}</span>
                      <span className="rule-hint">{rule.hint}</span>
                    </div>
                    <button
                      className={`toggle ${isRuleEnabled(rule.key) ? 'on' : 'off'}`}
                      onClick={() => handleToggleRule(rule.key)}
                      disabled={saving}
                    >
                      {isRuleEnabled(rule.key) ? 'ON' : 'OFF'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
