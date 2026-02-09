import { useMemo } from 'react'
import './Markdown.css'

interface Props {
  children: string
  className?: string
}

// Token types for the parser
type Token =
  | { type: 'heading'; level: number; content: string }
  | { type: 'paragraph'; content: string }
  | { type: 'code_block'; language: string; content: string }
  | { type: 'blockquote'; content: string }
  | { type: 'hr' }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'table'; headers: string[]; rows: string[][] }

// Parse markdown into tokens
function tokenize(text: string): Token[] {
  const tokens: Token[] = []
  const lines = text.split('\n')
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Fenced code block
    const codeMatch = line.match(/^```(\w*)/)
    if (codeMatch) {
      const language = codeMatch[1] || ''
      const codeLines: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      tokens.push({ type: 'code_block', language, content: codeLines.join('\n') })
      i++ // skip closing ```
      continue
    }

    // Horizontal rule
    if (/^(-{3,}|_{3,}|\*{3,})$/.test(line.trim())) {
      tokens.push({ type: 'hr' })
      i++
      continue
    }

    // Heading
    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/)
    if (headingMatch) {
      tokens.push({ type: 'heading', level: headingMatch[1].length, content: headingMatch[2] })
      i++
      continue
    }

    // Table (starts with |)
    if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
      const tableLines: string[] = [line]
      i++
      while (i < lines.length && lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|')) {
        tableLines.push(lines[i])
        i++
      }
      
      if (tableLines.length >= 2) {
        const parseRow = (row: string) => 
          row.split('|').slice(1, -1).map(cell => cell.trim())
        
        const headers = parseRow(tableLines[0])
        // Skip separator row (index 1)
        const rows = tableLines.slice(2).map(parseRow)
        tokens.push({ type: 'table', headers, rows })
      }
      continue
    }

    // Blockquote
    if (line.startsWith('>')) {
      const quoteLines: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) {
        quoteLines.push(lines[i].replace(/^>\s?/, ''))
        i++
      }
      tokens.push({ type: 'blockquote', content: quoteLines.join('\n') })
      continue
    }

    // Unordered list
    if (/^[-*+]\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[-*+]\s/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*+]\s/, ''))
        i++
      }
      tokens.push({ type: 'list', ordered: false, items })
      continue
    }

    // Ordered list
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s/, ''))
        i++
      }
      tokens.push({ type: 'list', ordered: true, items })
      continue
    }

    // Empty line
    if (!line.trim()) {
      i++
      continue
    }

    // Paragraph - collect consecutive non-empty lines
    const paraLines: string[] = [line]
    i++
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].startsWith('#') &&
      !lines[i].startsWith('```') &&
      !lines[i].startsWith('>') &&
      !/^[-*+]\s/.test(lines[i]) &&
      !/^\d+\.\s/.test(lines[i]) &&
      !/^(-{3,}|_{3,}|\*{3,})$/.test(lines[i].trim()) &&
      !(lines[i].trim().startsWith('|') && lines[i].trim().endsWith('|'))
    ) {
      paraLines.push(lines[i])
      i++
    }
    tokens.push({ type: 'paragraph', content: paraLines.join('\n') })
  }

  return tokens
}

// Parse inline markdown (bold, italic, code, links)
function parseInline(text: string): string {
  return text
    // Escape HTML
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Code (must be before bold/italic to avoid conflicts)
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
    // Bold + italic
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/___(.+?)___/g, '<strong><em>$1</em></strong>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__(.+?)__/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/_(.+?)_/g, '<em>$1</em>')
    // Strikethrough
    .replace(/~~(.+?)~~/g, '<del>$1</del>')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    // Line breaks within paragraphs
    .replace(/\n/g, '<br />')
}

// Escape HTML for code blocks (no inline parsing)
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// Render tokens to HTML
function renderTokens(tokens: Token[]): string {
  return tokens.map(token => {
    switch (token.type) {
      case 'heading':
        const tag = `h${token.level}`
        return `<${tag}>${parseInline(token.content)}</${tag}>`
      
      case 'paragraph':
        return `<p>${parseInline(token.content)}</p>`
      
      case 'code_block':
        return `<pre class="code-block${token.language ? ` language-${token.language}` : ''}"><code>${escapeHtml(token.content)}</code></pre>`
      
      case 'blockquote':
        return `<blockquote>${parseInline(token.content)}</blockquote>`
      
      case 'hr':
        return '<hr />'
      
      case 'list':
        const listTag = token.ordered ? 'ol' : 'ul'
        const listItems = token.items.map(item => `<li>${parseInline(item)}</li>`).join('')
        return `<${listTag}>${listItems}</${listTag}>`
      
      case 'table':
        const headerCells = token.headers.map(h => `<th>${parseInline(h)}</th>`).join('')
        const headerRow = `<tr>${headerCells}</tr>`
        const bodyRows = token.rows.map(row => {
          const cells = row.map(cell => `<td>${parseInline(cell)}</td>`).join('')
          return `<tr>${cells}</tr>`
        }).join('')
        return `<div class="table-wrapper"><table><thead>${headerRow}</thead><tbody>${bodyRows}</tbody></table></div>`
      
      default:
        return ''
    }
  }).join('\n')
}

export default function Markdown({ children, className }: Props) {
  const html = useMemo(() => {
    const tokens = tokenize(children)
    return renderTokens(tokens)
  }, [children])

  return (
    <div 
      className={`markdown-content ${className || ''}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
