import { useState, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useSettings } from '../context/SettingsContext'
import { useBackup } from '../hooks/useBackup'
import { useUpdate } from '../hooks/useUpdate'
import { useNotify } from '../context/NotificationContext'
import { useApp } from '../context/AppContext'
import { pickFolder } from '../api/pickFolder'
import ConfirmPopover from '../components/common/ConfirmPopover'
import SlidingTabs from '../components/common/SlidingTabs'
import type { ThemeMode } from '../hooks/useTheme'
import {
  DEFAULT_PROVIDER_ID,
  CAPABILITY_HOOKS,
  CAPABILITY_MODEL_SELECTION,
  CAPABILITY_EFFORT_SELECTION,
  CAPABILITY_SKIP_PERMISSIONS,
  CAPABILITY_HEARTBEAT_LOOP,
  getSharedCapabilityDisabledReason,
  getCapabilityDisabledReason,
  type ProviderRegistryResponse,
  useProviderRegistry,
} from '../hooks/useProviderRegistry'
import './SettingsPage.css'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatTimestamp(ts: string): string {
  // Parse "2026-03-04T06-58-35Z" → Date → local string
  const iso = ts.replace(/T(\d{2})-(\d{2})-(\d{2})Z$/, 'T$1:$2:$3Z')
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ts
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

const SCHEDULE_OPTIONS = [
  { value: 0, label: 'Disabled' },
  { value: 6, label: 'Every 6 hours' },
  { value: 12, label: 'Every 12 hours' },
  { value: 24, label: 'Every 24 hours' },
  { value: 48, label: 'Every 48 hours' },
]

const BACKUPS_PER_PAGE = 10

type SettingsTab = 'updates' | 'preferences' | 'backup'

export interface SettingsCapabilityState {
  updateBeforeStartDisabledReason: string | null
  skipPermissionsDisabledReason: string | null
  defaultModelDisabledReason: string | null
  defaultEffortDisabledReason: string | null
  brainHeartbeatDisabledReason: string | null
}

export function getSettingsCapabilityState(
  registry: ProviderRegistryResponse,
  workerProviderId: string,
  brainProviderId: string,
): SettingsCapabilityState {
  return {
    updateBeforeStartDisabledReason: getSharedCapabilityDisabledReason(
      registry,
      [workerProviderId, brainProviderId],
      CAPABILITY_HOOKS,
    ),
    skipPermissionsDisabledReason: getSharedCapabilityDisabledReason(
      registry,
      [workerProviderId, brainProviderId],
      CAPABILITY_SKIP_PERMISSIONS,
    ),
    defaultModelDisabledReason: getSharedCapabilityDisabledReason(
      registry,
      [workerProviderId, brainProviderId],
      CAPABILITY_MODEL_SELECTION,
    ),
    defaultEffortDisabledReason: getSharedCapabilityDisabledReason(
      registry,
      [workerProviderId, brainProviderId],
      CAPABILITY_EFFORT_SELECTION,
    ),
    brainHeartbeatDisabledReason: getCapabilityDisabledReason(
      registry,
      brainProviderId,
      CAPABILITY_HEARTBEAT_LOOP,
    ),
  }
}

export default function SettingsPage() {
  const { loading, getValue, save } = useSettings()
  const { registry, providerOptions, getCapabilityDisabledReason } = useProviderRegistry()
  const notify = useNotify()
  const { setUpdateAvailable } = useApp()
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = (searchParams.get('tab') as SettingsTab) || 'updates'
  const {
    info: updateInfo,
    checking: updateChecking,
    installStatus,
    installError,
    check: checkUpdate,
    openRelease,
    installUpdate,
  } = useUpdate()

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

  const [claudeUpdateBeforeStart, setClaudeUpdateBeforeStart] = useState(false)
  const [preserveFilters, setPreserveFilters] = useState(false)
  const [skipPermissions, setSkipPermissions] = useState(false)
  const [defaultModel, setDefaultModel] = useState('opus')
  const [defaultEffort, setDefaultEffort] = useState('high')
  const [workerDefaultProvider, setWorkerDefaultProvider] = useState(DEFAULT_PROVIDER_ID)
  const [brainDefaultProvider, setBrainDefaultProvider] = useState(DEFAULT_PROVIDER_ID)
  const [theme, setTheme] = useState<ThemeMode>('dark')
  const [brainHeartbeat, setBrainHeartbeat] = useState('off')
  const [heartbeatInput, setHeartbeatInput] = useState('')
  const [heartbeatFocused, setHeartbeatFocused] = useState(false)
  const [heartbeatSaved, setHeartbeatSaved] = useState(false)

  // Sync settings from DB
  useEffect(() => {
    if (!loading) {
      setClaudeUpdateBeforeStart(Boolean(getValue('claude.update_before_start')))
      setPreserveFilters(Boolean(getValue('ui.preserve_filters')))
      setSkipPermissions(Boolean(getValue('claude.skip_permissions')))
      setDefaultModel(String(getValue('claude.default_model') || 'opus'))
      setDefaultEffort(String(getValue('claude.default_effort') || 'high'))
      setWorkerDefaultProvider(String(getValue('worker.default_provider') || DEFAULT_PROVIDER_ID))
      setBrainDefaultProvider(String(getValue('brain.default_provider') || DEFAULT_PROVIDER_ID))
      setTheme((getValue('ui.theme') as ThemeMode) || 'dark')
      const hb = String(getValue('brain.heartbeat') || 'off')
      setBrainHeartbeat(hb)
      setHeartbeatInput(hb === 'off' ? '' : hb)
    }
  }, [loading, getValue])

  const handleClaudeUpdateToggle = async () => {
    const newValue = !claudeUpdateBeforeStart
    setClaudeUpdateBeforeStart(newValue)
    await save({ 'claude.update_before_start': newValue })
  }

  const handlePreserveFiltersToggle = async () => {
    const newValue = !preserveFilters
    setPreserveFilters(newValue)
    await save({ 'ui.preserve_filters': newValue })
  }

  const handleWorkerDefaultProviderChange = async (value: string) => {
    setWorkerDefaultProvider(value)
    await save({ 'worker.default_provider': value })
  }

  const handleBrainDefaultProviderChange = async (value: string) => {
    setBrainDefaultProvider(value)
    await save({ 'brain.default_provider': value })
  }

  const handleSkipPermissionsToggle = async () => {
    const newValue = !skipPermissions
    setSkipPermissions(newValue)
    await save({ 'claude.skip_permissions': newValue })
  }

  const handleDefaultModelChange = async (value: string) => {
    setDefaultModel(value)
    await save({ 'claude.default_model': value })
  }

  const handleDefaultEffortChange = async (value: string) => {
    setDefaultEffort(value)
    await save({ 'claude.default_effort': value })
  }

  const handleThemeChange = async (value: string) => {
    const v = value as ThemeMode
    setTheme(v)
    await save({ 'ui.theme': v })
  }

  const HEARTBEAT_PRESETS = [
    'Every 30 minutes',
    'Every hour',
    'Every 4 hours',
    'Weekdays at 9 AM',
  ]

  const handleBrainHeartbeatToggle = async () => {
    const newValue = brainHeartbeat === 'off' ? 'Every hour' : 'off'
    setBrainHeartbeat(newValue)
    setHeartbeatInput(newValue === 'off' ? '' : newValue)
    await save({ 'brain.heartbeat': newValue })
  }

  const flashHeartbeatSaved = () => {
    setHeartbeatSaved(true)
    setTimeout(() => setHeartbeatSaved(false), 1500)
  }

  const selectHeartbeatPreset = async (preset: string) => {
    setBrainHeartbeat(preset)
    setHeartbeatInput(preset)
    setHeartbeatFocused(false)
    await save({ 'brain.heartbeat': preset })
    flashHeartbeatSaved()
  }

  const commitHeartbeatInput = async () => {
    const trimmed = heartbeatInput.trim()
    if (!trimmed) {
      setHeartbeatInput(brainHeartbeat)
      return
    }
    setBrainHeartbeat(trimmed)
    setHeartbeatInput(trimmed)
    await save({ 'brain.heartbeat': trimmed })
    flashHeartbeatSaved()
  }

  const [backupDir, setBackupDir] = useState('')
  const [backupPassword, setBackupPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [retentionCount, setRetentionCount] = useState(5)
  const [scheduleHours, setScheduleHours] = useState(0)
  const [backupDirty, setBackupDirty] = useState(false)
  const [backupPage, setBackupPage] = useState(0)

  useEffect(() => {
    if (!loading) {
      checkUpdate()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading])

  // Sync sidebar badge with latest update check result
  useEffect(() => {
    if (updateInfo) {
      setUpdateAvailable(updateInfo.update_available)
    }
  }, [updateInfo, setUpdateAvailable])

  useEffect(() => {
    if (backupSettings) {
      setBackupDir(backupSettings.directory || '')
      setRetentionCount(backupSettings.retention_count)
      setScheduleHours(backupSettings.schedule_hours || 0)
      setBackupDirty(false)
    }
  }, [backupSettings])

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

  const providerTabs = providerOptions.map(option => ({
    value: option.value,
    label: option.label,
  }))
  const {
    updateBeforeStartDisabledReason,
    skipPermissionsDisabledReason,
    defaultModelDisabledReason,
    defaultEffortDisabledReason,
    brainHeartbeatDisabledReason,
  } = getSettingsCapabilityState(registry, workerDefaultProvider, brainDefaultProvider)

  // Pagination
  const totalPages = Math.max(1, Math.ceil(backups.length / BACKUPS_PER_PAGE))
  const paginatedBackups = useMemo(() => {
    const start = backupPage * BACKUPS_PER_PAGE
    return backups.slice(start, start + BACKUPS_PER_PAGE)
  }, [backups, backupPage])

  // Reset page if backups change
  useEffect(() => {
    if (backupPage >= totalPages) setBackupPage(0)
  }, [backups.length, totalPages, backupPage])

  return (
    <div className="settings-page page-scroll-layout">
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      {/* Tab bar */}
      <SlidingTabs
        tabs={[
          {
            value: 'updates' as const,
            label: <>
              <svg className="settings-tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Updates
              {updateInfo?.update_available && <span className="settings-tab-dot" />}
            </>,
          },
          {
            value: 'preferences' as const,
            label: <>
              <svg className="settings-tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
              Preferences
            </>,
          },
          {
            value: 'backup' as const,
            label: <>
              <svg className="settings-tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                <polyline points="17 21 17 13 7 13 7 21" />
                <polyline points="7 3 7 8 15 8" />
              </svg>
              Backup
            </>,
          },
        ]}
        value={activeTab}
        onChange={(tab) => {
          const newParams = new URLSearchParams(searchParams)
          if (tab === 'updates') newParams.delete('tab')
          else newParams.set('tab', tab)
          setSearchParams(newParams)
        }}
      />

      <div className="page-content">
      {/* ── Updates Tab ── */}
      {activeTab === 'updates' && (<>
        <div className="settings-content panel">
          <div className="panel-header">
            <h2>Software Update</h2>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => checkUpdate(true)}
              disabled={updateChecking}
            >
              {updateChecking ? (
                <>
                  <svg className="update-spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                  </svg>
                  Checking…
                </>
              ) : 'Check for Updates'}
            </button>
          </div>

          <div className="panel-body">
            {/* Update available */}
            {updateInfo?.update_available && (
              <div className="update-card">
                <div className="update-card-icon">
                  <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" y1="15" x2="12" y2="3" />
                  </svg>
                </div>
                <div className="update-card-body">
                  <div className="update-card-title">
                    Orchestrator v{updateInfo.latest_version}
                  </div>
                  <div className="update-card-meta">
                    Current: v{updateInfo.current_version}
                    {updateInfo.pub_date && (
                      <> · {new Date(updateInfo.pub_date).toLocaleDateString()}</>
                    )}
                  </div>
                  {updateInfo.release_notes && (
                    <p className="update-card-notes">{updateInfo.release_notes}</p>
                  )}
                  <div className="update-card-actions">
                    <button
                      className="btn btn-primary"
                      onClick={installUpdate}
                      disabled={installStatus === 'downloading' || installStatus === 'installing'}
                    >
                      {installStatus === 'downloading' ? 'Downloading…'
                        : installStatus === 'installing' ? 'Installing…'
                        : 'Update Now'}
                    </button>
                    <button
                      className="btn btn-secondary"
                      onClick={() => openRelease('https://github.com/yudongqiu/orchestrator/releases')}
                    >
                      Release Notes
                    </button>
                  </div>
                  {installError && (
                    <div className="update-error">
                      Auto-install failed: {installError}
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
              </div>
            )}

            {/* Up to date */}
            {updateInfo && !updateInfo.update_available && !updateInfo.error && (
              <div className="update-up-to-date">
                <div className="update-check-icon">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 6L9 17l-5-5" />
                  </svg>
                </div>
                <div className="update-up-to-date-title">Orchestrator is up to date</div>
                <div className="update-up-to-date-version">Version {updateInfo.current_version}</div>
              </div>
            )}

            {/* Error */}
            {updateInfo?.error && (
              <div className="update-up-to-date">
                <div className="update-error-icon">
                  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                </div>
                <div className="update-up-to-date-title">Unable to check for updates</div>
                <div className="update-up-to-date-version">Version {updateInfo.current_version} · Could not reach update server</div>
              </div>
            )}

            {/* Loading / initial */}
            {!updateInfo && !updateChecking && (
              <div className="update-up-to-date">
                <div className="update-up-to-date-version">Checking for updates…</div>
              </div>
            )}
          </div>
        </div>

      </>)}

      {/* ── Preferences Tab ── */}
      {activeTab === 'preferences' && (<>
        <div className="settings-content panel">
          <div className="panel-header">
            <h2>Appearance</h2>
          </div>
          <div className="panel-body">
            <div className="settings-toggle-row">
              <div>
                <div className="settings-toggle-label">Theme</div>
                <div className="settings-toggle-desc">
                  Choose your preferred color scheme
                </div>
                <div className="settings-toggle-hint">
                  Tip: run <code>/theme</code> in a worker session to switch Claude Code's terminal theme
                </div>
              </div>
              <SlidingTabs
                tabs={[
                  { value: 'dark' as const, label: 'Dark' },
                  { value: 'light' as const, label: 'Light' },
                  { value: 'system' as const, label: 'System' },
                ]}
                value={theme}
                onChange={handleThemeChange}
              />
            </div>
          </div>
        </div>

        <div className="settings-content panel">
          <div className="panel-header">
            <h2>Provider Defaults</h2>
          </div>
          <div className="panel-body">
            <div className="settings-toggle-row">
              <div>
                <div className="settings-toggle-label">Worker provider</div>
                <div className="settings-toggle-desc">
                  Default provider for new worker sessions
                </div>
              </div>
              <SlidingTabs
                tabs={providerTabs}
                value={workerDefaultProvider}
                onChange={handleWorkerDefaultProviderChange}
              />
            </div>

            <div className="settings-toggle-row">
              <div>
                <div className="settings-toggle-label">Brain provider</div>
                <div className="settings-toggle-desc">
                  Default provider for the brain session
                </div>
              </div>
              <SlidingTabs
                tabs={providerTabs}
                value={brainDefaultProvider}
                onChange={handleBrainDefaultProviderChange}
              />
            </div>
          </div>
        </div>

        <div className="settings-content panel">
          <div className="panel-header">
            <h2>Claude Code</h2>
          </div>
          <div className="panel-body">
            <div className="settings-toggle-row" title={updateBeforeStartDisabledReason || undefined}>
              <div>
                <div className="settings-toggle-label">Update before start</div>
                <div className="settings-toggle-desc">
                  Run <code>claude update</code> before each launch
                </div>
              </div>
              <div
                className={`sd-toggle-switch ${claudeUpdateBeforeStart ? 'on' : ''}${updateBeforeStartDisabledReason ? ' disabled' : ''}`}
                onClick={updateBeforeStartDisabledReason ? undefined : handleClaudeUpdateToggle}
                role="switch"
                aria-checked={claudeUpdateBeforeStart}
                aria-disabled={!!updateBeforeStartDisabledReason}
              >
                <div className="sd-toggle-knob" />
              </div>
            </div>

            <div className="settings-toggle-row" title={skipPermissionsDisabledReason || undefined}>
              <div>
                <div className="settings-toggle-label">Skip permission prompts</div>
                <div className="settings-toggle-desc">
                  Launch with <code>--dangerously-skip-permissions</code>, bypassing
                  confirmation prompts for file edits and command execution
                </div>
              </div>
              <div
                className={`sd-toggle-switch ${skipPermissions ? 'on' : ''}${skipPermissionsDisabledReason ? ' disabled' : ''}`}
                onClick={skipPermissionsDisabledReason ? undefined : handleSkipPermissionsToggle}
                role="switch"
                aria-checked={skipPermissions}
                aria-disabled={!!skipPermissionsDisabledReason}
              >
                <div className="sd-toggle-knob" />
              </div>
            </div>
            {skipPermissions && !skipPermissionsDisabledReason && (
              <div className="settings-warning-note">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                  <line x1="12" y1="9" x2="12" y2="13" />
                  <line x1="12" y1="17" x2="12.01" y2="17" />
                </svg>
                <span>
                  Claude will execute commands and modify files without asking for
                  confirmation. Only enable this if you trust the environment and
                  understand the risks. Takes effect on next brain/worker launch.
                </span>
              </div>
            )}

            <div className="settings-toggle-row" title={defaultModelDisabledReason || undefined}>
              <div>
                <div className="settings-toggle-label">Default model</div>
                <div className="settings-toggle-desc">
                  Claude model used when launching new workers and brain
                </div>
              </div>
              <div className={defaultModelDisabledReason ? 'settings-tabs-disabled' : ''}>
                <SlidingTabs
                  tabs={[
                    { value: 'opus' as const, label: 'Opus' },
                    { value: 'sonnet' as const, label: 'Sonnet' },
                    { value: 'haiku' as const, label: 'Haiku' },
                  ]}
                  value={defaultModel}
                  onChange={handleDefaultModelChange}
                />
              </div>
            </div>

            <div className="settings-toggle-row" title={defaultEffortDisabledReason || undefined}>
              <div>
                <div className="settings-toggle-label">Default effort</div>
                <div className="settings-toggle-desc">
                  Reasoning effort level for new workers and brain
                </div>
              </div>
              <div className={defaultEffortDisabledReason ? 'settings-tabs-disabled' : ''}>
                <SlidingTabs
                  tabs={[
                    { value: 'high' as const, label: 'High' },
                    { value: 'medium' as const, label: 'Medium' },
                    { value: 'low' as const, label: 'Low' },
                  ]}
                  value={defaultEffort}
                  onChange={handleDefaultEffortChange}
                />
              </div>
            </div>
          </div>
        </div>

        <div className="settings-content panel">
          <div className="panel-header">
            <h2>Brain</h2>
          </div>
          <div className="panel-body">
            <div className="settings-toggle-row" title={brainHeartbeatDisabledReason || undefined}>
              <div>
                <div className="settings-toggle-label">
                  Auto-monitoring <span className="settings-beta-badge">Beta</span>
                </div>
                <div className="settings-toggle-desc">
                  Brain periodically checks workers, sends "continue" to idle ones,
                  nudges PR reviews, and investigates stuck workers
                </div>
              </div>
              <div
                className={`sd-toggle-switch ${brainHeartbeat !== 'off' ? 'on' : ''}${brainHeartbeatDisabledReason ? ' disabled' : ''}`}
                onClick={brainHeartbeatDisabledReason ? undefined : handleBrainHeartbeatToggle}
                role="switch"
                aria-checked={brainHeartbeat !== 'off'}
                aria-disabled={!!brainHeartbeatDisabledReason}
              >
                <div className="sd-toggle-knob" />
              </div>
            </div>
            {brainHeartbeat !== 'off' && (
              <>
                {!brainHeartbeatDisabledReason && (
                  <div className="settings-warning-note">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                      <line x1="12" y1="9" x2="12" y2="13" />
                      <line x1="12" y1="17" x2="12.01" y2="17" />
                    </svg>
                    <span>
                      The brain will autonomously check workers, send "continue" to idle
                      ones, investigate stuck workers, and mark completed tasks done.
                      Takes effect on next brain start.
                    </span>
                  </div>
                )}
                <div className="settings-toggle-row" style={{ marginTop: 8 }} title={brainHeartbeatDisabledReason || undefined}>
                  <div>
                    <div className="settings-toggle-label">Check interval</div>
                    <div className="settings-toggle-desc">
                      How often the brain reviews workers. Pick a preset or type any schedule.
                    </div>
                  </div>
                  <div className="brain-heartbeat-combo">
                    <div className="brain-heartbeat-input-wrap">
                      <input
                        type="text"
                        value={heartbeatInput}
                        onChange={e => setHeartbeatInput(e.target.value)}
                        onFocus={() => setHeartbeatFocused(true)}
                        onBlur={() => { setHeartbeatFocused(false); commitHeartbeatInput() }}
                        onKeyDown={e => { if (e.key === 'Enter') { commitHeartbeatInput(); (e.target as HTMLInputElement).blur() } }}
                        placeholder="Every hour"
                        spellCheck={false}
                        disabled={!!brainHeartbeatDisabledReason}
                      />
                      {heartbeatSaved && (
                        <svg className="brain-heartbeat-saved" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--green)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      )}
                    </div>
                    {heartbeatFocused && !brainHeartbeatDisabledReason && (
                      <div className="brain-heartbeat-suggestions">
                        {HEARTBEAT_PRESETS.map(preset => (
                          <button
                            key={preset}
                            className={`brain-heartbeat-suggestion ${preset === brainHeartbeat ? 'active' : ''}`}
                            onMouseDown={e => {
                              e.preventDefault()
                              selectHeartbeatPreset(preset)
                            }}
                          >
                            {preset}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        <div className="settings-content panel">
          <div className="panel-header">
            <h2>Navigation</h2>
          </div>
          <div className="panel-body">
            <div className="settings-toggle-row">
              <div>
                <div className="settings-toggle-label">Preserve filters on navigation</div>
                <div className="settings-toggle-desc">
                  Restore last-used filters when clicking sidebar links
                </div>
              </div>
              <div
                className={`sd-toggle-switch ${preserveFilters ? 'on' : ''}`}
                onClick={handlePreserveFiltersToggle}
                role="switch"
                aria-checked={preserveFilters}
              >
                <div className="sd-toggle-knob" />
              </div>
            </div>
          </div>
        </div>
      </>)}

      {/* ── Backup Tab ── */}
      {activeTab === 'backup' && (
        <div className="backup-layout">
          {backupLoading && <p className="settings-hint">Loading backup settings...</p>}

          {!backupLoading && (
            <>
              {/* Left — Configuration */}
              <div className="backup-config panel">
                <div className="panel-header">
                  <h2>Configuration</h2>
                  {scheduleHours > 0 && isBackupConfigured && (
                    <span className="backup-schedule-badge">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="12" cy="12" r="10" />
                        <polyline points="12 6 12 12 16 14" />
                      </svg>
                      every {scheduleHours}h
                    </span>
                  )}
                </div>
                <div className="panel-body">
                  <div className="form-group">
                    <label>Directory</label>
                    <div className="input-with-browse">
                      <input
                        type="text"
                        value={backupDir}
                        onChange={e => { setBackupDir(e.target.value); setBackupDirty(true) }}
                        placeholder="/path/to/backups"
                      />
                      <button
                        type="button"
                        className="browse-btn"
                        title="Browse for folder"
                        onClick={async () => {
                          const path = await pickFolder()
                          if (path) { setBackupDir(path); setBackupDirty(true) }
                        }}
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                        </svg>
                      </button>
                    </div>
                  </div>

                  <div className="form-group">
                    <label>
                      Password
                      {backupSettings?.has_password && (
                        <span className="backup-password-set">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M20 6L9 17l-5-5" />
                          </svg>
                          saved
                        </span>
                      )}
                    </label>
                    <div className="password-input-wrap">
                      <input
                        type={showPassword ? 'text' : 'password'}
                        value={backupPassword}
                        onChange={e => { setBackupPassword(e.target.value); setBackupDirty(true) }}
                        placeholder={backupSettings?.has_password ? '••••••••' : 'Enter password'}
                      />
                      <button
                        type="button"
                        className="password-toggle-btn"
                        onClick={() => setShowPassword(v => !v)}
                        tabIndex={-1}
                        title={showPassword ? 'Hide password' : 'Show password'}
                      >
                        {showPassword ? (
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
                            <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
                            <line x1="1" y1="1" x2="23" y2="23" />
                          </svg>
                        ) : (
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                            <circle cx="12" cy="12" r="3" />
                          </svg>
                        )}
                      </button>
                    </div>
                  </div>

                  <div className="backup-inline-fields">
                    <div className="form-group">
                      <label>Keep</label>
                      <input
                        type="number"
                        value={retentionCount}
                        onChange={e => { setRetentionCount(Number(e.target.value)); setBackupDirty(true) }}
                        min={1}
                        max={100}
                      />
                    </div>
                    <div className="form-group">
                      <label>Schedule</label>
                      <select
                        value={scheduleHours}
                        onChange={e => { setScheduleHours(Number(e.target.value)); setBackupDirty(true) }}
                      >
                        {SCHEDULE_OPTIONS.map(opt => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div className="backup-form-actions">
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
                        ? <>Saved: <strong>{lastResult.filename}</strong> ({formatBytes(lastResult.size_bytes)})</>
                        : <>Failed: {lastResult.error}</>
                      }
                    </div>
                  )}
                </div>
              </div>

              {/* Right — History */}
              <div className="backup-history panel">
                <div className="panel-header">
                  <h2>History</h2>
                  {backups.length > 0 && (
                    <span className="backup-count">{backups.length} backups</span>
                  )}
                </div>
                <div className="panel-body backup-history-body">
                  {backups.length === 0 ? (
                    <div className="backup-empty">
                      <svg className="backup-empty-icon" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                        <polyline points="17 21 17 13 7 13 7 21" />
                        <polyline points="7 3 7 8 15 8" />
                      </svg>
                      <p>No backups yet</p>
                    </div>
                  ) : (
                    <>
                      <div className="backup-list">
                        {paginatedBackups.map((b, i) => (
                          <div
                            key={b.filename}
                            className={`backup-entry ${backupPage === 0 && i === 0 ? 'backup-entry-latest' : ''}`}
                          >
                            <span className="backup-entry-date">
                              {formatTimestamp(b.timestamp)}
                              {backupPage === 0 && i === 0 && (
                                <span className="backup-latest-badge">Latest</span>
                              )}
                            </span>
                            <span className="backup-entry-size">{formatBytes(b.size_bytes)}</span>
                            <ConfirmPopover
                              message="Restore database from this backup? Current data will be replaced."
                              confirmLabel="Restore"
                              onConfirm={() => handleRestore(b.filename)}
                              variant="warning"
                            >
                              {({ onClick }) => (
                                <button
                                  className="backup-restore-btn"
                                  onClick={onClick}
                                  disabled={restoring}
                                >
                                  Restore
                                </button>
                              )}
                            </ConfirmPopover>
                          </div>
                        ))}
                      </div>

                      {totalPages > 1 && (
                        <div className="backup-pagination">
                          <div className="backup-pagination-controls">
                            <button
                              className="backup-page-btn"
                              onClick={() => setBackupPage(p => p - 1)}
                              disabled={backupPage === 0}
                            >
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="15 18 9 12 15 6" />
                              </svg>
                            </button>
                            {Array.from({ length: totalPages }, (_, i) => (
                              <button
                                key={i}
                                className={`backup-page-btn ${i === backupPage ? 'active' : ''}`}
                                onClick={() => setBackupPage(i)}
                              >
                                {i + 1}
                              </button>
                            ))}
                            <button
                              className="backup-page-btn"
                              onClick={() => setBackupPage(p => p + 1)}
                              disabled={backupPage === totalPages - 1}
                            >
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="9 18 15 12 9 6" />
                              </svg>
                            </button>
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}
      </div>
    </div>
  )
}
