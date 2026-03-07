import React from 'react'

const URL_RE = /(https?:\/\/[^\s<]+[^\s<.,;:!?'"\)\]])/g

export function linkifyText(text: string, onLinkClick: (url: string) => void): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  URL_RE.lastIndex = 0
  while ((match = URL_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    const url = match[1]
    parts.push(
      <a
        key={match.index}
        href={url}
        className="np-inline-link"
        onClick={e => { e.preventDefault(); e.stopPropagation(); onLinkClick(url) }}
      >
        {url}
      </a>
    )
    lastIndex = URL_RE.lastIndex
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }
  return parts
}
