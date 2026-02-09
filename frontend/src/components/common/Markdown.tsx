import { useMemo } from 'react'
import './Markdown.css'

interface Props {
  children: string
  className?: string
}

// Token types for the parser
type ListItem = {
  content: string
  children?: ListItem[]
  childOrdered?: boolean
}

type Token =
  | { type: 'heading'; level: number; content: string }
  | { type: 'paragraph'; content: string }
  | { type: 'code_block'; language: string; content: string }
  | { type: 'blockquote'; content: string }
  | { type: 'hr' }
  | { type: 'list'; ordered: boolean; items: ListItem[] }
  | { type: 'table'; headers: string[]; rows: string[][] }

// Parse a list with potential nested items
function parseList(lines: string[], startIndex: number, ordered: boolean): { items: ListItem[], endIndex: number } {
  const items: ListItem[] = []
  let i = startIndex
  const listPattern = ordered ? /^(\d+\.)\s(.*)$/ : /^([-*+])\s(.*)$/
  const indentedListPattern = /^(\s{2,})(\d+\.|[-*+])\s(.*)$/

  while (i < lines.length) {
    const line = lines[i]
    
    // Check for top-level list item
    const match = line.match(listPattern)
    if (match) {
      const content = match[2]
      const item: ListItem = { content }
      items.push(item)
      i++
      
      // Check for nested items (indented)
      const nestedItems: ListItem[] = []
      let nestedOrdered: boolean | undefined
      while (i < lines.length) {
        const nestedMatch = lines[i].match(indentedListPattern)
        if (nestedMatch) {
          const marker = nestedMatch[2]
          nestedOrdered = nestedOrdered ?? /^\d+\.$/.test(marker)
          nestedItems.push({ content: nestedMatch[3] })
          i++
        } else {
          break
        }
      }
      
      if (nestedItems.length > 0) {
        item.children = nestedItems
        item.childOrdered = nestedOrdered
      }
      continue
    }
    
    // No more list items at this level
    break
  }

  return { items, endIndex: i }
}

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
      const { items, endIndex } = parseList(lines, i, false)
      tokens.push({ type: 'list', ordered: false, items })
      i = endIndex
      continue
    }

    // Ordered list
    if (/^\d+\.\s/.test(line)) {
      const { items, endIndex } = parseList(lines, i, true)
      tokens.push({ type: 'list', ordered: true, items })
      i = endIndex
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
  // First, protect escaped characters by replacing with placeholders
  const escapeMap: Record<string, string> = {}
  let escapeIndex = 0
  let processed = text.replace(/\\([\\`*_{}[\]()#+\-.!|])/g, (_, char) => {
    const placeholder = `\x00ESC${escapeIndex++}\x00`
    escapeMap[placeholder] = char
    return placeholder
  })

  processed = processed
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

  // Restore escaped characters
  for (const [placeholder, char] of Object.entries(escapeMap)) {
    processed = processed.replace(placeholder, char)
  }

  return processed
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
        const listItems = token.items.map(item => {
          let html = parseInline(item.content)
          if (item.children && item.children.length > 0) {
            const childTag = item.childOrdered ? 'ol' : 'ul'
            const childItems = item.children.map(c => `<li>${parseInline(c.content)}</li>`).join('')
            html += `<${childTag}>${childItems}</${childTag}>`
          }
          return `<li>${html}</li>`
        }).join('')
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
