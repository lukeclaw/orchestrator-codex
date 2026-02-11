import { useState, useEffect } from 'react'
import { useSettings } from '../hooks/useSettings'
import './SettingsPage.css'

export default function SettingsPage() {
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

  const handleSave = () => {
    save({
      'general.polling_interval': pollingInterval,
      'general.max_sessions': maxSessions,
    })
  }

  return (
    <div className="settings-page">
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      <div className="settings-content panel">
        <div className="panel-body">
          {loading && <p className="settings-hint">Loading settings...</p>}

          {!loading && (
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
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
