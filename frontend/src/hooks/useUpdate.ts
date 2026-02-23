import { useState, useCallback } from 'react'
import { api } from '../api/client'

export interface UpdateInfo {
  current_version: string
  latest_version: string | null
  update_available: boolean
  release_url?: string
  dmg_url?: string | null
  release_notes?: string
  pub_date?: string
  error?: string
}

type InstallStatus = 'idle' | 'downloading' | 'installing' | 'done' | 'error'

/** Check if Tauri IPC is available (app is running inside the Tauri webview). */
function hasTauriIPC(): boolean {
  return '__TAURI_INTERNALS__' in window
}

/** Invoke a Tauri plugin command via the IPC bridge. */
async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (window as any).__TAURI_INTERNALS__.invoke(cmd, args)
}

/** Create a Tauri IPC Channel for streaming events (e.g. download progress). */
function createChannel(onMessage?: (event: unknown) => void) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const internals = (window as any).__TAURI_INTERNALS__
  const id = internals.transformCallback(onMessage || (() => {}))
  // Tauri deserializes Channel from a string "__CHANNEL__:<id>" via toJSON()
  return { id, __TAURI_CHANNEL_MARKER__: true, toJSON: () => `__CHANNEL__:${id}` }
}

/** Metadata returned by plugin:updater|check when an update is available. */
interface UpdateMetadata {
  rid: number
  currentVersion: string
  version: string
  date?: string
  body?: string
}

export function useUpdate() {
  const [info, setInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const [installStatus, setInstallStatus] = useState<InstallStatus>('idle')
  const [installError, setInstallError] = useState<string | null>(null)

  /** Check for updates via the Python backend (GitHub Releases API). */
  const check = useCallback(async (force = false) => {
    setChecking(true)
    try {
      const qs = force ? '?force=true' : ''
      const data = await api<UpdateInfo>(`/api/updates/check${qs}`)
      setInfo(data)
      return data
    } catch {
      setInfo(null)
      return null
    } finally {
      setChecking(false)
    }
  }, [])

  /** Open a URL in the system browser. */
  const openRelease = useCallback(async (url: string) => {
    await api('/api/open-url', {
      method: 'POST',
      body: JSON.stringify({ url }),
    })
  }, [])

  /**
   * Download and install the update via Tauri's updater plugin, then restart.
   * Falls back to opening the release page if Tauri IPC is not available.
   */
  const installUpdate = useCallback(async () => {
    if (!hasTauriIPC()) {
      // Fallback: open release page in browser
      if (info?.dmg_url) {
        await openRelease(info.dmg_url)
      } else if (info?.release_url) {
        await openRelease(info.release_url)
      }
      return
    }

    setInstallStatus('downloading')
    setInstallError(null)

    try {
      // Ask the Tauri updater plugin to check (it uses the latest.json endpoint).
      // Returns the update metadata object when available, or null when up-to-date.
      const update = await tauriInvoke<UpdateMetadata | null>(
        'plugin:updater|check'
      )

      if (!update) {
        // Tauri updater says no update (maybe no signed artifacts yet).
        // Fall back to opening the release page.
        setInstallStatus('idle')
        if (info?.dmg_url) {
          await openRelease(info.dmg_url)
        } else if (info?.release_url) {
          await openRelease(info.release_url)
        }
        return
      }

      // Download and install. The rid (resource ID) references the update object.
      // The onEvent channel is required by the plugin for download progress events.
      setInstallStatus('installing')
      const onEvent = createChannel()
      await tauriInvoke('plugin:updater|download_and_install', {
        rid: update.rid,
        onEvent,
      })

      setInstallStatus('done')

      // Restart the app to apply the update
      await tauriInvoke('plugin:process|restart')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error('[update] Install failed:', msg)
      setInstallStatus('error')
      setInstallError(msg)
    }
  }, [info, openRelease])

  return {
    info,
    checking,
    installStatus,
    installError,
    check,
    openRelease,
    installUpdate,
    hasTauri: hasTauriIPC(),
  }
}
