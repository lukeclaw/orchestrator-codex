export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

/**
 * Open a URL in the system browser (Tauri) or a new tab (browser).
 * Uses the backend /api/open-url endpoint which handles platform detection.
 * Falls back to window.open for when running outside the desktop app.
 */
export function openUrl(url: string): void {
  fetch('/api/open-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  }).catch(() => {
    window.open(url, '_blank', 'noopener')
  })
}

/**
 * Open a terminal with `gh auth login` for GitHub re-authentication.
 */
export async function ghAuth(): Promise<void> {
  await fetch('/api/gh-auth', { method: 'POST' })
}

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new ApiError(res.status, `API ${res.status}: ${text}`)
  }
  return res.json()
}
