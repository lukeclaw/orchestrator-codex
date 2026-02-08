export function timeAgo(dateStr: string | null): string {
  if (!dateStr) return 'never'
  const d = new Date(dateStr.replace(' ', 'T'))
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export function shortTime(dateStr: string | null): string {
  if (!dateStr) return ''
  const d = new Date(dateStr.replace(' ', 'T'))
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
