/** Worker status → CSS variable mapping for inline styles (charts, filter pills).
 *  Uses CSS variables so colors automatically adapt to dark/light theme. */
export const WORKER_STATUS_COLORS: Record<string, string> = {
  working: 'var(--status-working)',
  idle: 'var(--status-idle)',
  waiting: 'var(--status-waiting)',
  blocked: 'var(--status-blocked)',
  paused: 'var(--status-paused)',
  connecting: 'var(--status-connecting)',
  disconnected: 'var(--status-disconnected)',
  error: 'var(--status-error)',
}
