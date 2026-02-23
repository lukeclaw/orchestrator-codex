import { useState, useEffect } from 'react'
import { useSettings } from '../hooks/useSettings'
import { useBackup } from '../hooks/useBackup'
import { useUpdate } from '../hooks/useUpdate'
import { useNotify } from '../context/NotificationContext'
import ConfirmPopover from '../components/common/ConfirmPopover'
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

const SCHEDULE_OPTIONS = [
  { value: 0, label: 'Disabled' },
  { value: 6, label: 'Every 6 hours' },
  { value: 12, label: 'Every 12 hours' },
  { value: 24, label: 'Every 24 hours' },
  { value: 48, label: 'Every 48 hours' },
]

export default function SettingsPage() {
  const { settings, loading, saving, save, getValue } = useSettings()
  const notify = useNotify()
  const {
    info: updateInfo,
    checking: updateChecking,
    installStatus,
    installError,
    check: checkUpdate,
    openRelease,
    installUpdate,
  } = useUpdate()

  // Local state for general settings
  const [autoUpdateCheck, setAutoUpdateCheck] = useState(true)

  // Backup state
  const {
    settings: backupSettings,
    backups,
    loading: backupLoading,
    saving: backupSaving,
    running: backupRunning,
    restoring,
    lastResult,
    saveSettings: saveBackupSettings,
    runBackup,
    restoreBackup,
  } = useBackup()

  const [backupDir, setBackupDir] = useState('')
  const [backupPassword, setBackupPassword] = useState('')
  const [retentionCount, setRetentionCount] = useState(5)
  const [scheduleHours, setScheduleHours] = useState(0)
  const [backupDirty, setBackupDirty] = useState(false)

  // Sync local state when settings load
  useEffect(() => {
    if (settings.length > 0) {
      const auc = getValue('general.auto_update_check')
      if (auc != null) setAutoUpdateCheck(Boolean(auc))
    }
  }, [settings, getValue])

  // Auto-check for updates on mount
  useEffect(() => {
    if (!loading && autoUpdateCheck) {
      checkUpdate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading])

  // Sync backup settings
  useEffect(() => {
    if (backupSettings) {
      setBackupDir(backupSettings.directory || '')
      setRetentionCount(backupSettings.retention_count)
      setScheduleHours(backupSettings.schedule_hours || 0)
      setBackupDirty(false)
    }
  }, [backupSettings])

  const handleToggleAutoUpdate = () => {
    const next = !autoUpdateCheck
    setAutoUpdateCheck(next)
    save({ 'general.auto_update_check': next })
  }

  const handleBackupSave = async () => {
    const updates: Record<string, unknown> = {}
    if (backupDir) updates.directory = backupDir
    if (backupPassword) updates.password = backupPassword
    updates.retention_count = retentionCount
    updates.schedule_hours = scheduleHours
    await saveBackupSettings(updates)
    setBackupPassword('')
    setBackupDirty(false)
  }

  const handleRestore = async (filename: string) => {
    const result = await restoreBackup(filename)
    if (result.ok) {
      notify('Database restored successfully. Reloading...', 'success')
      setTimeout(() => window.location.reload(), 1500)
    } else {
      notify(`Restore failed: ${result.error}`, 'error')
    }
  }

  const isBackupConfigured = backupSettings?.directory && backupSettings?.has_password

  return (
    <div className="settings-page">
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      <div className="settings-content panel">
        <div className="panel-body">
          <div className="settings-section">
            <h3>App Updates</h3>

            <div className="update-version-row">
              <span className="update-version-label">Current version</span>
              <span className="update-version-value">{updateInfo?.current_version ?? '...'}</span>
            </div>

            {updateInfo?.update_available && (
              <div className="update-banner">
                <div className="update-banner-text">
                  <strong>v{updateInfo.latest_version}</strong> is available
                  {updateInfo.pub_date && (
                    <span className="update-pub-date">
                      {' '}— {new Date(updateInfo.pub_date).toLocaleDateString()}
                    </span>
                  )}
                </div>
                {updateInfo.release_notes && (
                  <p className="update-notes">{updateInfo.release_notes}</p>
                )}
                <div className="update-banner-actions">
                  <button
                    className="btn btn-primary"
                    onClick={installUpdate}
                    disabled={installStatus === 'downloading' || installStatus === 'installing'}
                  >
                    {installStatus === 'downloading' ? 'Downloading...'
                      : installStatus === 'installing' ? 'Installing...'
                      : 'Install Update'}
                  </button>
                  {updateInfo.release_url && (
                    <button
                      className="btn btn-secondary"
                      onClick={() => openRelease(updateInfo.release_url!)}
                    >
                      Release Notes
                    </button>
                  )}
                </div>
                {installError && (
                  <div className="update-error" style={{ marginTop: 8 }}>
                    <span>Auto-install failed: {installError}</span>
                    {updateInfo.dmg_url && (
                      <button
                        className="btn btn-secondary btn-sm"
                        style={{ marginLeft: 8 }}
                        onClick={() => openRelease(updateInfo.dmg_url!)}
                      >
                        Download DMG
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}

            {updateInfo && !updateInfo.update_available && !updateInfo.error && (
              <div className="update-up-to-date">You're on the latest version.</div>
            )}

            {updateInfo?.error && (
              <div className="update-error">Could not reach update server.</div>
            )}

            <div className="update-actions">
              <button
                className="btn btn-secondary"
                onClick={() => checkUpdate(true)}
                disabled={updateChecking}
              >
                {updateChecking ? 'Checking...' : 'Check for Updates'}
              </button>
            </div>

            <div className="approval-rule" style={{ marginTop: 16 }}>
              <div className="rule-info">
                <span className="rule-label">Check automatically on launch</span>
                <span className="rule-hint">Checks GitHub for new releases when the app starts</span>
              </div>
              <button
                className={`toggle ${autoUpdateCheck ? 'on' : 'off'}`}
                onClick={handleToggleAutoUpdate}
                disabled={saving}
              >
                {autoUpdateCheck ? 'ON' : 'OFF'}
              </button>
            </div>
          </div>
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

                <div className="form-group">
                  <label>Automatic Backup Schedule</label>
                  <select
                    value={scheduleHours}
                    onChange={e => { setScheduleHours(Number(e.target.value)); setBackupDirty(true) }}
                  >
                    {SCHEDULE_OPTIONS.map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                  <span className="form-hint">Automatically run backups at a regular interval</span>
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

                {scheduleHours > 0 && isBackupConfigured && (
                  <div className="backup-schedule-info">
                    Automatic backup active: every {scheduleHours}h
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
                          <ConfirmPopover
                            message={`Restore database from this backup? Current data will be replaced.`}
                            confirmLabel="Restore"
                            onConfirm={() => handleRestore(b.filename)}
                            variant="warning"
                          >
                            {({ onClick }) => (
                              <button
                                className="btn btn-sm backup-restore-btn"
                                onClick={onClick}
                                disabled={restoring}
                              >
                                {restoring ? 'Restoring...' : 'Restore'}
                              </button>
                            )}
                          </ConfirmPopover>
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
