import { useState, useEffect } from 'react'
import { useSettings } from '../hooks/useSettings'
import { useBackup } from '../hooks/useBackup'
import './SettingsPage.css'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatTimestamp(ts: string): string {
  // Filename timestamp like "2026-02-19T17-00-00Z" → readable
  return ts.replace(/T/, ' ').replace(/-(\d{2})-(\d{2})Z$/, ':$1:$2 UTC')
}

export default function SettingsPage() {
  const { settings, loading, saving, save, getValue } = useSettings()

  // Local state for general settings
  const [pollingInterval, setPollingInterval] = useState(5)
  const [maxSessions, setMaxSessions] = useState(10)

  // Backup state
  const {
    settings: backupSettings,
    backups,
    loading: backupLoading,
    saving: backupSaving,
    running: backupRunning,
    lastResult,
    saveSettings: saveBackupSettings,
    runBackup,
  } = useBackup()

  const [backupDir, setBackupDir] = useState('')
  const [backupPassword, setBackupPassword] = useState('')
  const [retentionCount, setRetentionCount] = useState(5)
  const [backupDirty, setBackupDirty] = useState(false)

  // Sync local state when settings load
  useEffect(() => {
    if (settings.length > 0) {
      const pi = getValue('general.polling_interval')
      if (pi != null) setPollingInterval(Number(pi))
      const ms = getValue('general.max_sessions')
      if (ms != null) setMaxSessions(Number(ms))
    }
  }, [settings, getValue])

  // Sync backup settings
  useEffect(() => {
    if (backupSettings) {
      setBackupDir(backupSettings.directory || '')
      setRetentionCount(backupSettings.retention_count)
      setBackupDirty(false)
    }
  }, [backupSettings])

  const handleSave = () => {
    save({
      'general.polling_interval': pollingInterval,
      'general.max_sessions': maxSessions,
    })
  }

  const handleBackupSave = async () => {
    const updates: Record<string, unknown> = {}
    if (backupDir) updates.directory = backupDir
    if (backupPassword) updates.password = backupPassword
    updates.retention_count = retentionCount
    await saveBackupSettings(updates)
    setBackupPassword('')
    setBackupDirty(false)
  }

  const isBackupConfigured = backupSettings?.directory && backupSettings?.has_password

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

      <div className="settings-content panel">
        <div className="panel-body">
          {backupLoading && <p className="settings-hint">Loading backup settings...</p>}

          {!backupLoading && (
            <>
              <div className="settings-section">
                <h3>Database Backup</h3>
                <p className="settings-hint">
                  Create encrypted snapshots of the database. Use a cloud-synced folder (Dropbox, iCloud, Google Drive) for automatic offsite backup.
                </p>

                <div className="form-group">
                  <label>Backup Directory</label>
                  <input
                    type="text"
                    value={backupDir}
                    onChange={e => { setBackupDir(e.target.value); setBackupDirty(true) }}
                    placeholder="/Users/you/Dropbox/orchestrator-backups"
                  />
                  <span className="form-hint">Absolute path to the folder where encrypted backups are stored</span>
                </div>

                <div className="form-group">
                  <label>Encryption Password {backupSettings?.has_password && <span className="backup-password-set">(set)</span>}</label>
                  <input
                    type="password"
                    value={backupPassword}
                    onChange={e => { setBackupPassword(e.target.value); setBackupDirty(true) }}
                    placeholder={backupSettings?.has_password ? '••••••••' : 'Enter password'}
                  />
                  <span className="form-hint">AES-256 encryption password for backup zip files</span>
                </div>

                <div className="form-group">
                  <label>Retention Count</label>
                  <input
                    type="number"
                    value={retentionCount}
                    onChange={e => { setRetentionCount(Number(e.target.value)); setBackupDirty(true) }}
                    min={1}
                    max={100}
                  />
                  <span className="form-hint">Number of backup files to keep (oldest are auto-deleted)</span>
                </div>

                <div className="backup-actions">
                  <button
                    className="btn btn-primary"
                    onClick={handleBackupSave}
                    disabled={backupSaving || !backupDirty}
                  >
                    {backupSaving ? 'Saving...' : 'Save Settings'}
                  </button>
                  <button
                    className="btn btn-secondary"
                    onClick={runBackup}
                    disabled={backupRunning || !isBackupConfigured}
                    title={!isBackupConfigured ? 'Configure directory and password first' : ''}
                  >
                    {backupRunning ? 'Backing up...' : 'Backup Now'}
                  </button>
                </div>

                {lastResult && (
                  <div className={`backup-result ${lastResult.ok ? 'success' : 'error'}`}>
                    {lastResult.ok
                      ? <>Backup saved: <strong>{lastResult.filename}</strong> ({formatBytes(lastResult.size_bytes)})</>
                      : <>Backup failed: {lastResult.error}</>
                    }
                  </div>
                )}

                {backupSettings?.last_run && (
                  <div className="backup-last-run">
                    Last backup: {new Date(backupSettings.last_run).toLocaleString()}
                    {backupSettings.last_status && (
                      <span className={`backup-status ${backupSettings.last_status === 'success' ? 'success' : 'error'}`}>
                        {backupSettings.last_status}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {backups.length > 0 && (
                <div className="settings-section backup-list-section">
                  <h3>Backup History</h3>
                  <div className="backup-list">
                    {backups.map(b => (
                      <div key={b.filename} className="backup-entry">
                        <span className="backup-filename">{b.filename}</span>
                        <span className="backup-meta">
                          <span>{formatTimestamp(b.timestamp)}</span>
                          <span className="backup-size">{formatBytes(b.size_bytes)}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
