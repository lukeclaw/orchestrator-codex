import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import Modal from '../common/Modal'
import { api } from '../../api/client'
import { pickFolder } from '../../api/pickFolder'
import { useApp } from '../../context/AppContext'
import { useSettings } from '../../context/SettingsContext'
import { IconRefresh, IconServer, IconSessions, IconLaptop } from '../common/Icons'
import SlidingTabs from '../common/SlidingTabs'
import {
  DEFAULT_PROVIDER_ID,
  CAPABILITY_REMOTE_SESSIONS,
  getCapabilityDisabledReason,
  type ProviderRegistryResponse,
  type ProviderOption,
  useProviderRegistry,
} from '../../hooks/useProviderRegistry'
import './AddSessionModal.css'

interface Props {
  open: boolean
  onClose: () => void
}

interface RdevInstance {
  name: string
  state: string
  cluster: string
  created: string
  last_accessed: string
  in_use: boolean
  worker_name?: string
}

// Generate a memorable default worker name like "swift-fox"
const ADJECTIVES = [
  'swift', 'bold', 'calm', 'keen', 'warm', 'cool', 'fair', 'wise',
  'bright', 'quick', 'sharp', 'crisp', 'fresh', 'vivid', 'lucid',
  'noble', 'brisk', 'deft', 'prime', 'grand',
]
const NOUNS = [
  'fox', 'owl', 'elk', 'jay', 'ram', 'lynx', 'hawk', 'dove',
  'wolf', 'bear', 'hare', 'wren', 'lark', 'crow', 'deer',
  'seal', 'ibis', 'moth', 'puma', 'yak',
]
function generateWorkerName(): string {
  const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)]
  const noun = NOUNS[Math.floor(Math.random() * NOUNS.length)]
  return `${adj}-${noun}`
}

const WORKER_BASE_DIR = '/tmp/orchestrator/workers'

const WORKER_TYPE_DESCRIPTIONS: Record<string, string> = {
  rdev: 'Connect to a LinkedIn rdev virtual machine instance.',
  ssh: 'Connect to any remote machine via SSH.',
  local: 'Run a worker on your local machine.',
}

export function getRemoteWorkerTypeDisabledReason(
  registry: ProviderRegistryResponse,
  providerId: string,
): string | null {
  return getCapabilityDisabledReason(registry, providerId, CAPABILITY_REMOTE_SESSIONS)
}

export function buildWorkerCreatePayload(params: {
  workerType: 'rdev' | 'ssh' | 'local'
  provider: string
  name: string
  selectedRdev: string
  sshHost: string
  sshWorkDir: string
  mpPath: string
}): Record<string, unknown> | null {
  const sanitizedName = params.name.trim().replace(/[/\\]/g, '_')

  if (params.workerType === 'rdev') {
    if (!params.selectedRdev) return null
    return {
      name: params.selectedRdev.replace(/[/\\]/g, '_'),
      host: params.selectedRdev,
      provider: params.provider,
    }
  }

  if (params.workerType === 'ssh') {
    if (!params.sshHost.trim()) return null
    return {
      name: sanitizedName,
      host: params.sshHost.trim(),
      work_dir: params.sshWorkDir.trim() || null,
      provider: params.provider,
    }
  }

  return {
    name: sanitizedName,
    host: 'localhost',
    work_dir: params.mpPath.trim() || null,
    provider: params.provider,
  }
}

export default function AddSessionModal({ open, onClose }: Props) {
  const { refresh } = useApp()
  const { getValue, loading: settingsLoading } = useSettings()
  const { registry, providerOptions } = useProviderRegistry()
  const [workerType, setWorkerType] = useState<'rdev' | 'ssh' | 'local'>('local')
  const [provider, setProvider] = useState(DEFAULT_PROVIDER_ID)

  // Local worker state
  const [localName, setLocalName] = useState(generateWorkerName)
  const [mpPath, setMpPath] = useState('')

  // Rdev worker state
  const [selectedRdev, setSelectedRdev] = useState<string>('')

  // SSH worker state
  const [sshName, setSshName] = useState(generateWorkerName)
  const [sshHost, setSshHost] = useState('')
  const [sshWorkDir, setSshWorkDir] = useState('')
  const [sshNameManual, setSshNameManual] = useState(false)

  // Shared state
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)

  // Track which fields have been touched for validation
  const [touchedFields, setTouchedFields] = useState<Set<string>>(new Set())

  // Rdev list state
  const [rdevs, setRdevs] = useState<RdevInstance[]>([])
  const [loadingRdevs, setLoadingRdevs] = useState(false)
  const [rdevError, setRdevError] = useState('')

  // Get current name based on worker type (not used for rdev)
  const name = workerType === 'ssh' ? sshName : localName
  const setName = workerType === 'ssh' ? setSshName : setLocalName
  const remoteDisabledReason = getRemoteWorkerTypeDisabledReason(registry, provider)
  const remoteWorkersEnabled = !remoteDisabledReason

  // Reset state when modal is closed
  useEffect(() => {
    if (!open) {
      setWorkerType('local')
      setProvider(DEFAULT_PROVIDER_ID)
      setLocalName(generateWorkerName())
      setMpPath('')
      setSelectedRdev('')
      setSshName(generateWorkerName())
      setSshHost('')
      setSshWorkDir('')
      setSshNameManual(false)
      setError('')
      setRdevError('')
      setTouchedFields(new Set())
    }
  }, [open])

  // Sync default provider from settings when opening the modal.
  useEffect(() => {
    if (!open || settingsLoading) return
    const workerDefaultProvider = String(getValue('worker.default_provider') || DEFAULT_PROVIDER_ID)
    setProvider(workerDefaultProvider)
  }, [open, settingsLoading, getValue])

  // Codex MVP only supports local workers. If the provider changes to one that
  // does not allow remote sessions, keep the form in a valid local-only state.
  useEffect(() => {
    if (!remoteWorkersEnabled && workerType !== 'local') {
      setWorkerType('local')
    }
  }, [remoteWorkersEnabled, workerType])

  // Auto-derive SSH name from host
  useEffect(() => {
    if (workerType === 'ssh' && !sshNameManual && sshHost.trim()) {
      // Extract hostname: "user@host.example.com" → "host", "host.example.com" → "host"
      const hostPart = sshHost.includes('@') ? sshHost.split('@')[1] : sshHost
      const shortName = hostPart.split('.')[0]
      if (shortName) {
        setSshName(shortName)
      }
    }
  }, [sshHost, workerType, sshNameManual])

  // Mark field as touched
  const touchField = (field: string) => {
    setTouchedFields(prev => new Set(prev).add(field))
  }

  // Validation helper
  const validateForm = () => {
    if (workerType === 'rdev') {
      return !!selectedRdev
    }
    const currentName = workerType === 'ssh' ? sshName : localName
    const nameValid = !!currentName.trim() && !/[^a-zA-Z0-9_\-]/.test(currentName)
    if (workerType === 'ssh') {
      return nameValid && !!sshHost.trim()
    }
    return nameValid
  }

  // Validate worker name: only ASCII letters, digits, hyphens, underscores
  const hasNonAscii = /[^a-zA-Z0-9_\-]/.test(name)

  // Error messages (shown when field is touched and invalid)
  const nameError = touchedFields.has('name') && !name.trim()
    ? 'Worker name is required'
    : touchedFields.has('name') && hasNonAscii
      ? 'Only English letters, numbers, hyphens, and underscores are allowed'
      : ''
  const rdevError2 = touchedFields.has('rdev') && workerType === 'rdev' && !selectedRdev ? 'Please select an rdev instance' : ''
  const sshHostError = touchedFields.has('sshHost') && workerType === 'ssh' && !sshHost.trim() ? 'SSH host is required' : ''
  const providerTabs = providerOptions.map((option: ProviderOption) => ({
    value: option.value,
    label: option.label,
  }))

  // Form is valid when all required fields are filled
  const isFormValid = validateForm()

  // Compute disabled reason for tooltip
  const disabledReason = !isFormValid
    ? workerType === 'rdev'
      ? 'Select an rdev instance to continue'
      : workerType === 'ssh'
        ? !sshHost.trim()
          ? 'Enter an SSH host to continue'
          : !name.trim() || hasNonAscii
            ? 'Enter a valid worker name to continue'
            : ''
        : !name.trim() || hasNonAscii
          ? 'Enter a valid worker name to continue'
          : ''
    : ''

  // Fetch rdev list (forceRefresh bypasses server cache)
  const fetchRdevs = useCallback(async (forceRefresh = false) => {
    setLoadingRdevs(true)
    setRdevError('')
    try {
      const url = forceRefresh ? '/api/rdevs?refresh=true' : '/api/rdevs'
      const data = await api<RdevInstance[]>(url)
      setRdevs(data)
    } catch (e) {
      setRdevError(e instanceof Error ? e.message : 'Failed to fetch rdev list')
    } finally {
      setLoadingRdevs(false)
    }
  }, [])

  // Sort rdevs into sections: available, in-use, stopped
  const sortedRdevs = useMemo(() => {
    const available: RdevInstance[] = []
    const inUse: RdevInstance[] = []
    const stopped: RdevInstance[] = []
    for (const rdev of rdevs) {
      if (rdev.state === 'RUNNING' && !rdev.in_use) {
        available.push(rdev)
      } else if (rdev.in_use) {
        inUse.push(rdev)
      } else {
        stopped.push(rdev)
      }
    }
    const byName = (a: RdevInstance, b: RdevInstance) => a.name.localeCompare(b.name)
    available.sort(byName)
    inUse.sort(byName)
    stopped.sort(byName)
    return { available, inUse, stopped }
  }, [rdevs])

  // Fetch rdevs when modal opens and rdev type is selected
  useEffect(() => {
    if (open && workerType === 'rdev') {
      fetchRdevs()
    }
  }, [open, workerType, fetchRdevs])

  // Update mpPath when name changes (for local workers)
  useEffect(() => {
    if (workerType === 'local' && name) {
      setMpPath(`${WORKER_BASE_DIR}/${name}`)
    }
  }, [name, workerType])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    // Touch all fields to show any remaining errors
    setTouchedFields(new Set(['name', 'rdev', 'sshHost']))
    if (!validateForm()) return
    setError('')
    setCreating(true)

    try {
      const payload = buildWorkerCreatePayload({
        workerType,
        provider,
        name,
        selectedRdev,
        sshHost,
        sshWorkDir,
        mpPath,
      })
      if (!payload) return

      await api('/api/sessions', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      onClose()
      refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create session')
    } finally {
      setCreating(false)
    }
  }

  function renderRdevItem(rdev: RdevInstance) {
    return (
      <div
        key={rdev.name}
        role="option"
        aria-selected={selectedRdev === rdev.name}
        aria-disabled={rdev.in_use || rdev.state !== 'RUNNING'}
        className={`rdev-item ${selectedRdev === rdev.name ? 'selected' : ''} ${rdev.in_use ? 'in-use' : ''} ${rdev.state !== 'RUNNING' ? 'not-running' : ''}`}
        title={
          rdev.in_use
            ? `Already in use by worker "${rdev.worker_name}"`
            : rdev.state !== 'RUNNING'
              ? `Instance is ${rdev.state.toLowerCase()}`
              : ''
        }
        onClick={() => {
          touchField('rdev')
          if (!rdev.in_use && rdev.state === 'RUNNING') {
            setSelectedRdev(rdev.name)
          }
        }}
      >
        <div className="rdev-item-main">
          <span className={`rdev-state ${rdev.state.toLowerCase()}`}>{rdev.state}</span>
          <span className="rdev-name">{rdev.name}</span>
        </div>
        <div className="rdev-item-meta">
          {rdev.in_use ? (
            <span className="rdev-in-use">In use</span>
          ) : (
            <span className="rdev-accessed">{rdev.last_accessed}</span>
          )}
        </div>
      </div>
    )
  }

  return (
    <Modal open={open} onClose={onClose} title="Add New Worker">
      <form onSubmit={handleSubmit} data-testid="add-session-form">
        <div className="modal-body add-worker-body">
          <div className="form-group provider-select-group">
            <label>Provider</label>
            <SlidingTabs
              tabs={providerTabs}
              value={provider}
              onChange={setProvider}
            />
            <div className="field-hint">
              New workers inherit the selected provider. The default comes from Settings.
            </div>
          </div>

          <div className="worker-type-toggle" data-testid="worker-type-toggle">
            <div className="toggle-group">
              <span className="toggle-btn-wrapper" title={!remoteWorkersEnabled ? remoteDisabledReason : undefined}>
                <button
                  type="button"
                  className={`toggle-btn${workerType === 'rdev' ? ' active' : ''}${!remoteWorkersEnabled ? ' disabled' : ''}`}
                  onClick={() => {
                    if (!remoteWorkersEnabled) return
                    setWorkerType('rdev')
                    setSshNameManual(false)
                  }}
                  disabled={!remoteWorkersEnabled}
                >
                  <IconServer size={14} /> rdev VM
                </button>
              </span>
              <span className="toggle-btn-wrapper" title={!remoteWorkersEnabled ? remoteDisabledReason : undefined}>
                <button
                  type="button"
                  className={`toggle-btn${workerType === 'ssh' ? ' active' : ''}${!remoteWorkersEnabled ? ' disabled' : ''}`}
                  onClick={() => {
                    if (!remoteWorkersEnabled) return
                    setWorkerType('ssh')
                    setSshNameManual(false)
                  }}
                  disabled={!remoteWorkersEnabled}
                >
                  <IconSessions size={14} /> SSH
                </button>
              </span>
              <button
                type="button"
                className={`toggle-btn${workerType === 'local' ? ' active' : ''}`}
                onClick={() => setWorkerType('local')}
              >
                <IconLaptop size={14} /> Local
              </button>
            </div>
            <div className="worker-type-description">{WORKER_TYPE_DESCRIPTIONS[workerType]}</div>
          </div>

          {workerType !== 'rdev' && (
            <div className="form-group">
              <label>Worker Name <span className="field-required">*</span></label>
              <input
                type="text"
                data-testid="session-name-input"
                value={name}
                onChange={e => {
                  setName(e.target.value)
                  touchField('name')
                  if (workerType === 'ssh') setSshNameManual(true)
                }}
                onBlur={() => touchField('name')}
                placeholder={workerType === 'ssh' ? 'Auto-derived from host' : 'e.g. api-worker'}
                className={nameError ? 'input-error' : ''}
              />
              {nameError
                ? <div className="field-error">{nameError}</div>
                : <div className="field-hint">Letters, numbers, hyphens, and underscores only</div>
              }
            </div>
          )}

          {workerType === 'rdev' ? (
            <div className="form-group">
              <div className="rdev-list-header">
                <label>Select rdev Instance <span className="field-required">*</span></label>
                <button
                  type="button"
                  className="btn-icon"
                  onClick={() => fetchRdevs(true)}
                  disabled={loadingRdevs}
                  title="Refresh rdev list"
                >
                  <IconRefresh size={14} className={loadingRdevs ? 'spinning' : ''} />
                </button>
              </div>

              {rdevError && (
                <div className="rdev-error">{rdevError}</div>
              )}

              {loadingRdevs ? (
                <div className="rdev-loading">Loading rdev instances...</div>
              ) : rdevs.length === 0 ? (
                <div className="rdev-empty">No rdev instances found.<br /><Link to="/workers/rdevs" className="rdev-manage-link" onClick={onClose}>Create and manage rdevs →</Link></div>
              ) : (
                <>
                <div className="rdev-list" role="listbox" aria-label="rdev instances">
                  {sortedRdevs.available.length > 0 && (
                    <>
                      <div className="rdev-section-header">Available ({sortedRdevs.available.length})</div>
                      {sortedRdevs.available.map(renderRdevItem)}
                    </>
                  )}
                  {sortedRdevs.inUse.length > 0 && (
                    <>
                      <div className="rdev-section-header">In Use ({sortedRdevs.inUse.length})</div>
                      {sortedRdevs.inUse.map(renderRdevItem)}
                    </>
                  )}
                  {sortedRdevs.stopped.length > 0 && (
                    <>
                      <div className="rdev-section-header">Stopped ({sortedRdevs.stopped.length})</div>
                      {sortedRdevs.stopped.map(renderRdevItem)}
                    </>
                  )}
                </div>
                  {rdevError2 && <div className="field-error">{rdevError2}</div>}
                  <div className="rdev-manage-hint"><Link to="/workers/rdevs" onClick={onClose}>Manage rdevs →</Link></div>
                </>
              )}
            </div>
          ) : workerType === 'ssh' ? (
            <>
              <div className="form-group">
                <label>SSH Host <span className="field-required">*</span></label>
                <input
                  type="text"
                  data-testid="session-ssh-host-input"
                  value={sshHost}
                  onChange={e => {
                    setSshHost(e.target.value)
                    touchField('sshHost')
                  }}
                  onBlur={() => touchField('sshHost')}
                  placeholder="user@hostname"
                  className={sshHostError ? 'input-error' : ''}
                />
                {sshHostError && <div className="field-error">{sshHostError}</div>}
                <div className="field-hint">Uses your ~/.ssh/config for keys and proxy settings</div>
              </div>
              <div className="form-group">
                <label>Working Directory <span className="field-optional">(optional)</span></label>
                <input
                  type="text"
                  data-testid="session-ssh-workdir-input"
                  value={sshWorkDir}
                  onChange={e => setSshWorkDir(e.target.value)}
                  placeholder="e.g. /home/user/project"
                />
              </div>
            </>
          ) : (
            <>
              <div className="form-group">
                <label>Working Directory <span className="field-optional">(optional)</span></label>
                <div className="input-with-browse">
                  <input
                    type="text"
                    data-testid="session-path-input"
                    value={mpPath}
                    onChange={e => setMpPath(e.target.value)}
                    placeholder="e.g. /src/my-project"
                  />
                  <button
                    type="button"
                    className="browse-btn"
                    title="Browse for folder"
                    onClick={async () => {
                      const path = await pickFolder()
                      if (path) setMpPath(path)
                    }}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                    </svg>
                  </button>
                </div>
                <div className="field-hint">Auto-generated from worker name</div>
              </div>
            </>
          )}

          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button
              type="submit"
              className="btn btn-primary"
              data-testid="create-session-btn"
              disabled={creating || !isFormValid}
              title={disabledReason || undefined}
            >
            {creating ? <><IconRefresh size={14} className="spinning" /> Creating...</> : 'Create Worker'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
