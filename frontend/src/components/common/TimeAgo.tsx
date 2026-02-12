/** Parse a date string, treating timezone-naive strings as UTC */
export function parseDate(dateStr: string): Date {
  let s = dateStr.replace(' ', 'T')
  // If no timezone info, assume UTC
  if (!s.endsWith('Z') && !s.includes('+') && !/T\d{2}:\d{2}:\d{2}[+-]/.test(s)) {
    s += 'Z'
  }
  return new Date(s)
}

export function timeAgo(dateStr: string | null): string {
  if (!dateStr) return 'never'
  const d = parseDate(dateStr)
  const secs = Math.floor((Date.now() - d.getTime()) / 1000)
  if (secs < 0) return 'just now'
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export function shortTime(dateStr: string | null): string {
  if (!dateStr) return ''
  const d = parseDate(dateStr)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
