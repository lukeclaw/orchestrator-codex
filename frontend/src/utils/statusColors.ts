/** Worker status → hex color mapping for inline styles (charts, filter pills). */
export const WORKER_STATUS_COLORS: Record<string, string> = {
  working: '#58a6ff',
  idle: '#3fb950',
  waiting: '#d29922',
  paused: '#f97316',
  connecting: '#db6d28',
  disconnected: '#f85149',
  error: '#f85149',
}
