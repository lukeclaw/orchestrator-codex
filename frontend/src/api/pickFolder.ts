/**
 * Open a native OS folder picker dialog via Tauri IPC (rfd).
 * Returns the selected absolute path, or null if the user cancelled
 * or Tauri is not available (e.g. running in a plain browser).
 */
export async function pickFolder(): Promise<string | null> {
  try {
    const invoke = (window as any).__TAURI__?.core?.invoke
    if (!invoke) return null
    const path: string | null = await invoke('pick_folder')
    return path ?? null
  } catch {
    return null
  }
}
