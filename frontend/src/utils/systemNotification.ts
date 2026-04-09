/** System notification utilities — Tauri native or Web Notifications API fallback. */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const w = window as any

function hasTauriIPC(): boolean {
  return '__TAURI_INTERNALS__' in window
}

async function tauriInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  return w.__TAURI_INTERNALS__.invoke(cmd, args)
}

/** Throttle: last system notification timestamp. */
let lastSent = 0
const THROTTLE_MS = 5000

/**
 * Request notification permission. Returns true if granted.
 */
export async function requestNotificationPermission(): Promise<boolean> {
  if (hasTauriIPC()) {
    try {
      // Returns true | false | null (null = prompt needed)
      const granted = await tauriInvoke<boolean | null>('plugin:notification|is_permission_granted')
      if (granted === true) return true
      if (granted === false) return false
      // null = prompt state, request permission
      const result = await tauriInvoke<string>('plugin:notification|request_permission')
      return result === 'granted'
    } catch {
      return false
    }
  }
  // Web Notifications API fallback
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  const result = await Notification.requestPermission()
  return result === 'granted'
}

/**
 * Send a system notification. Throttled to at most once per 5 seconds.
 */
export async function sendSystemNotification(title: string, body: string): Promise<void> {
  const now = Date.now()
  if (now - lastSent < THROTTLE_MS) return
  lastSent = now

  if (hasTauriIPC()) {
    try {
      await tauriInvoke('plugin:notification|notify', {
        options: { title, body },
      })
    } catch (e) {
      console.warn('Tauri notification failed:', e)
    }
  } else if ('Notification' in window && Notification.permission === 'granted') {
    new Notification(title, { body })
  }
}
