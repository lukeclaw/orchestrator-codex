import { useMemo, useState, useCallback } from 'react'
import { IconCopy, IconCheck, IconEye } from './Icons'
import './Markdown.css'

interface Props {
  children: string
  className?: string
  expandable?: boolean
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
// Handles "loose lists" where items are separated by blank lines
function parseList(lines: string[], startIndex: number, ordered: boolean): { items: ListItem[], endIndex: number } {
  const items: ListItem[] = []
  let i = startIndex
  const listPattern = ordered ? /^(\d+\.)\s(.*)$/ : /^([-*+])\s(.*)$/
  const indentedListPattern = /^(\s{2,})(\d+\.|[-*+])\s(.*)$/

  while (i < lines.length) {
    const line = lines[i]
    
    // Skip blank lines within the list (loose list support)
    if (!line.trim()) {
      // Look ahead to see if there's another list item coming
      let nextNonEmpty = i + 1
      while (nextNonEmpty < lines.length && !lines[nextNonEmpty].trim()) {
        nextNonEmpty++
      }
      // If next non-empty line is a list item of the same type, continue
      if (nextNonEmpty < lines.length && listPattern.test(lines[nextNonEmpty])) {
        i = nextNonEmpty
        continue
      }
      // Otherwise, end the list
      break
    }
    
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

// Parse inline markdown (bold, italic, code, links, images)
function parseInline(text: string): string {
  // First, protect escaped characters by replacing with placeholders
  const escapeMap: Record<string, string> = {}
  let escapeIndex = 0
  let processed = text.replace(/\\([\\`*_{}[\]()#+\-.!|])/g, (_, char) => {
    const placeholder = `\x00ESC${escapeIndex++}\x00`
    escapeMap[placeholder] = char
    return placeholder
  })

  // Protect code spans first (they should not have any inline processing)
  const codeMap: Record<string, string> = {}
  let codeIndex = 0
  // Handle double backticks first (can contain single backticks)
  processed = processed.replace(/``([^`]|`[^`])+``/g, (match) => {
    const placeholder = `\x00CODE${codeIndex++}\x00`
    const content = match.slice(2, -2).trim()
    codeMap[placeholder] = `<code class="inline-code">${escapeHtml(content)}</code>`
    return placeholder
  })
  // Handle single backticks
  processed = processed.replace(/`([^`]+)`/g, (_, content) => {
    const placeholder = `\x00CODE${codeIndex++}\x00`
    codeMap[placeholder] = `<code class="inline-code">${escapeHtml(content)}</code>`
    return placeholder
  })

  processed = processed
    // Escape HTML (after code extraction to preserve code content)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Images (must be before links since syntax is similar)
    .replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g, '<img src="$2" alt="$1" title="$3" />')
    // Links
    .replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g, (_, text, href, title) => {
      if (href.startsWith('#')) {
        return `<a href="${href}" class="anchor-link"${title ? ` title="${title}"` : ''}>${text}</a>`
      }
      return `<a href="${href}" target="_blank" rel="noopener noreferrer"${title ? ` title="${title}"` : ''}>${text}</a>`
    })
    // Autolinks (bare URLs) - match http/https URLs not already in href/src
    .replace(/(?<![">])(https?:\/\/[^\s<]+[^\s<.,;:!?'")\]])/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>')
    // Bold + italic with asterisks (can work intraword)
    .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
    // Bold + italic with underscores (only at word boundaries per GFM spec)
    .replace(/(^|[\s\p{P}])___([^_]+)___([\s\p{P}]|$)/gu, '$1<strong><em>$2</em></strong>$3')
    // Bold with asterisks (can work intraword)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    // Bold with underscores (only at word boundaries per GFM spec)
    .replace(/(^|[\s\p{P}])__([^_]+)__([\s\p{P}]|$)/gu, '$1<strong>$2</strong>$3')
    // Italic with asterisks (can work intraword)
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    // Italic with underscores (only at word boundaries per GFM spec)
    // The underscore must be preceded by whitespace/punctuation/start and followed by whitespace/punctuation/end
    .replace(/(^|[\s\p{P}])_([^_]+)_([\s\p{P}]|$)/gu, '$1<em>$2</em>$3')
    // Strikethrough
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    // Line breaks within paragraphs
    .replace(/\n/g, '<br />')

  // Restore code spans
  for (const [placeholder, html] of Object.entries(codeMap)) {
    processed = processed.replace(placeholder, html)
  }

  // Restore escaped characters
  for (const [placeholder, char] of Object.entries(escapeMap)) {
    processed = processed.replace(placeholder, char)
  }

  return processed
}

// Generate a URL-friendly slug from heading text
function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .trim()
}

// Escape HTML for code blocks (no inline parsing)
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// ---------------------------------------------------------------------------
// Lightweight syntax highlighting
// ---------------------------------------------------------------------------

type HlRule = { pattern: RegExp; className: string }

const KEYWORDS_PYTHON = 'and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield'
const KEYWORDS_JAVA = 'abstract|assert|boolean|break|byte|case|catch|char|class|const|continue|default|do|double|else|enum|extends|final|finally|float|for|goto|if|implements|import|instanceof|int|interface|long|native|new|package|private|protected|public|return|short|static|strictfp|super|switch|synchronized|this|throw|throws|transient|try|void|volatile|while'
const KEYWORDS_JS = 'abstract|arguments|async|await|break|case|catch|class|const|continue|debugger|default|delete|do|else|enum|export|extends|finally|for|from|function|if|implements|import|in|instanceof|interface|let|new|of|package|private|protected|public|return|static|super|switch|this|throw|try|typeof|var|void|while|with|yield'
const KEYWORDS_GO = 'break|case|chan|const|continue|default|defer|else|fallthrough|for|func|go|goto|if|import|interface|map|package|range|return|select|struct|switch|type|var'
const KEYWORDS_RUST = 'as|async|await|break|const|continue|crate|dyn|else|enum|extern|fn|for|if|impl|in|let|loop|match|mod|move|mut|pub|ref|return|self|static|struct|super|trait|type|unsafe|use|where|while'
const KEYWORDS_SQL = 'ADD|ALL|ALTER|AND|AS|ASC|BETWEEN|BY|CASE|CHECK|COLUMN|CONSTRAINT|CREATE|CROSS|DATABASE|DEFAULT|DELETE|DESC|DISTINCT|DROP|ELSE|END|EXCEPT|EXISTS|FOREIGN|FROM|FULL|GROUP|HAVING|IF|IN|INDEX|INNER|INSERT|INTERSECT|INTO|IS|JOIN|KEY|LEFT|LIKE|LIMIT|NOT|NULL|OFFSET|ON|OR|ORDER|OUTER|PRIMARY|REFERENCES|RIGHT|SELECT|SET|TABLE|THEN|UNION|UNIQUE|UPDATE|VALUES|WHEN|WHERE|WITH'
const KEYWORDS_SHELL = 'if|then|else|elif|fi|for|while|do|done|case|esac|in|function|return|local|export|unset|readonly|shift|break|continue|exit|trap|source|eval|exec|set'
const KEYWORDS_C = 'auto|break|case|char|const|continue|default|do|double|else|enum|extern|float|for|goto|if|inline|int|long|register|restrict|return|short|signed|sizeof|static|struct|switch|typedef|union|unsigned|void|volatile|while'

const BUILTINS_PYTHON = 'True|False|None|self|cls|print|len|range|int|str|float|list|dict|set|tuple|bool|type|super|isinstance|hasattr|getattr|setattr|open|map|filter|zip|enumerate|sorted|reversed|any|all|min|max|sum|abs|round|input|format|property|classmethod|staticmethod'
const BUILTINS_JS = 'true|false|null|undefined|NaN|Infinity|console|window|document|Math|JSON|Promise|Array|Object|String|Number|Boolean|Map|Set|Error|RegExp|Date|parseInt|parseFloat|isNaN'
const BUILTINS_RUST = 'true|false|Some|None|Ok|Err|Self|println|eprintln|format|vec|Box|Vec|String|Option|Result|HashMap|HashSet|Rc|Arc|Cell|RefCell'
const BUILTINS_GO = 'true|false|nil|iota|append|cap|close|copy|delete|len|make|new|panic|print|println|recover|error|string|bool|int|int8|int16|int32|int64|uint|uint8|uint16|uint32|uint64|float32|float64|byte|rune|complex64|complex128'

function buildRules(lang: string): HlRule[] | null {
  switch (lang) {
    case 'python':
    case 'py':
      return [
        { pattern: /(#.*)$/gm, className: 'hl-comment' },
        { pattern: /("""[\s\S]*?"""|'''[\s\S]*?''')/g, className: 'hl-string' },
        { pattern: /(f?"(?:\\.|[^"\\])*"|f?'(?:\\.|[^'\\])*')/g, className: 'hl-string' },
        { pattern: /\b(\d+\.?\d*(?:e[+-]?\d+)?)\b/gi, className: 'hl-number' },
        { pattern: new RegExp(`(?<![.\\w])@(\\w+)`, 'g'), className: 'hl-decorator' },
        { pattern: new RegExp(`\\b(${BUILTINS_PYTHON})\\b`, 'g'), className: 'hl-builtin' },
        { pattern: new RegExp(`\\b(${KEYWORDS_PYTHON})\\b`, 'g'), className: 'hl-keyword' },
      ]
    case 'java':
      return [
        { pattern: /(\/\/.*$)/gm, className: 'hl-comment' },
        { pattern: /(\/\*[\s\S]*?\*\/)/g, className: 'hl-comment' },
        { pattern: /("(?:\\.|[^"\\])*")/g, className: 'hl-string' },
        { pattern: /('(?:\\.|[^'\\])')/g, className: 'hl-string' },
        { pattern: /\b(\d+\.?\d*[fFdDlL]?)\b/g, className: 'hl-number' },
        { pattern: /(@\w+)/g, className: 'hl-decorator' },
        { pattern: new RegExp(`\\b(${KEYWORDS_JAVA})\\b`, 'g'), className: 'hl-keyword' },
      ]
    case 'javascript':
    case 'typescript':
    case 'js':
    case 'ts':
    case 'jsx':
    case 'tsx':
      return [
        { pattern: /(\/\/.*$)/gm, className: 'hl-comment' },
        { pattern: /(\/\*[\s\S]*?\*\/)/g, className: 'hl-comment' },
        { pattern: /(`(?:\\.|[^`\\])*`)/g, className: 'hl-string' },
        { pattern: /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/g, className: 'hl-string' },
        { pattern: /\b(\d+\.?\d*(?:e[+-]?\d+)?n?)\b/gi, className: 'hl-number' },
        { pattern: new RegExp(`\\b(${BUILTINS_JS})\\b`, 'g'), className: 'hl-builtin' },
        { pattern: new RegExp(`\\b(${KEYWORDS_JS})\\b`, 'g'), className: 'hl-keyword' },
      ]
    case 'go':
    case 'golang':
      return [
        { pattern: /(\/\/.*$)/gm, className: 'hl-comment' },
        { pattern: /(\/\*[\s\S]*?\*\/)/g, className: 'hl-comment' },
        { pattern: /(`[^`]*`)/g, className: 'hl-string' },
        { pattern: /("(?:\\.|[^"\\])*")/g, className: 'hl-string' },
        { pattern: /\b(\d+\.?\d*(?:e[+-]?\d+)?i?)\b/gi, className: 'hl-number' },
        { pattern: new RegExp(`\\b(${BUILTINS_GO})\\b`, 'g'), className: 'hl-builtin' },
        { pattern: new RegExp(`\\b(${KEYWORDS_GO})\\b`, 'g'), className: 'hl-keyword' },
      ]
    case 'rust':
    case 'rs':
      return [
        { pattern: /(\/\/.*$)/gm, className: 'hl-comment' },
        { pattern: /(\/\*[\s\S]*?\*\/)/g, className: 'hl-comment' },
        { pattern: /("(?:\\.|[^"\\])*")/g, className: 'hl-string' },
        { pattern: /\b(\d+\.?\d*(?:e[+-]?\d+)?(?:_\d+)*[fiu]?\d*)\b/gi, className: 'hl-number' },
        { pattern: /(#!?\[[\w:]+\])/g, className: 'hl-decorator' },
        { pattern: new RegExp(`\\b(${BUILTINS_RUST})\\b`, 'g'), className: 'hl-builtin' },
        { pattern: new RegExp(`\\b(${KEYWORDS_RUST})\\b`, 'g'), className: 'hl-keyword' },
      ]
    case 'sql':
      return [
        { pattern: /(--.*$)/gm, className: 'hl-comment' },
        { pattern: /(\/\*[\s\S]*?\*\/)/g, className: 'hl-comment' },
        { pattern: /('(?:''|[^'])*')/g, className: 'hl-string' },
        { pattern: /\b(\d+\.?\d*)\b/g, className: 'hl-number' },
        { pattern: new RegExp(`\\b(${KEYWORDS_SQL})\\b`, 'gi'), className: 'hl-keyword' },
      ]
    case 'bash':
    case 'sh':
    case 'shell':
    case 'zsh':
      return [
        { pattern: /(#.*$)/gm, className: 'hl-comment' },
        { pattern: /("(?:\\.|[^"\\])*")/g, className: 'hl-string' },
        { pattern: /('(?:[^'\\]|\\.)*')/g, className: 'hl-string' },
        { pattern: /(\$\{?\w+\}?)/g, className: 'hl-builtin' },
        { pattern: /\b(\d+)\b/g, className: 'hl-number' },
        { pattern: new RegExp(`\\b(${KEYWORDS_SHELL})\\b`, 'g'), className: 'hl-keyword' },
      ]
    case 'c':
    case 'cpp':
    case 'c++':
    case 'h':
    case 'hpp':
      return [
        { pattern: /(\/\/.*$)/gm, className: 'hl-comment' },
        { pattern: /(\/\*[\s\S]*?\*\/)/g, className: 'hl-comment' },
        { pattern: /("(?:\\.|[^"\\])*")/g, className: 'hl-string' },
        { pattern: /('(?:\\.|[^'\\])')/g, className: 'hl-string' },
        { pattern: /(#\s*\w+)/gm, className: 'hl-decorator' },
        { pattern: /\b(\d+\.?\d*[fFlLuU]*)\b/g, className: 'hl-number' },
        { pattern: new RegExp(`\\b(${KEYWORDS_C}|class|namespace|template|typename|using|virtual|override|nullptr|new|delete|true|false|throw|catch|try)\\b`, 'g'), className: 'hl-keyword' },
      ]
    default:
      return null
  }
}

/**
 * Apply regex-based syntax highlighting to code.
 *
 * Works by running rules in order, each time replacing matched spans with
 * placeholders so later rules cannot re-match inside already-highlighted
 * regions.
 */
function highlightCode(code: string, language: string): string {
  const rules = buildRules(language)
  if (!rules) return escapeHtml(code)

  // Placeholder map: we replace matched regions with \x00F{idx}\x00 so later
  // rules don't match inside them.  The "F" prefix prevents the number-
  // highlighting regex (\b\d+\b) from matching the numeric index inside a
  // placeholder — "F" is a word char, so there's no \b before the digits.
  const fragments: string[] = []

  let text = code
  for (const rule of rules) {
    text = text.replace(rule.pattern, (match) => {
      // Don't re-highlight placeholders
      if (match.includes('\x00')) return match
      const idx = fragments.length
      fragments.push(`<span class="${rule.className}">${escapeHtml(match)}</span>`)
      return `\x00F${idx}\x00`
    })
  }

  // Split text by placeholders, escape the gaps, restore spans
  const parts = text.split(/\x00F(\d+)\x00/)
  let result = ''
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      // Plain text — escape it
      result += escapeHtml(parts[i])
    } else {
      // Fragment index — restore the span
      result += fragments[parseInt(parts[i], 10)]
    }
  }
  return result
}

// Render tokens to HTML
function renderTokens(tokens: Token[]): string {
  return tokens.map(token => {
    switch (token.type) {
      case 'heading':
        const tag = `h${token.level}`
        const id = slugify(token.content)
        return `<${tag} id="${id}">${parseInline(token.content)}</${tag}>`
      
      case 'paragraph':
        return `<p>${parseInline(token.content)}</p>`
      
      case 'code_block':
        return `<pre class="code-block${token.language ? ` language-${token.language}` : ''}"><code>${highlightCode(token.content, token.language)}</code></pre>`
      
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

// Export for testing
export { tokenize, renderTokens }

export default function Markdown({ children, className, expandable }: Props) {
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const html = useMemo(() => {
    const tokens = tokenize(children)
    return renderTokens(tokens)
  }, [children])

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(children)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (err) {
      console.error('Failed to copy:', err)
    }
  }, [children])

  const handleAnchorClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const anchor = (e.target as HTMLElement).closest('a')
    if (!anchor) return
    const rawHref = anchor.getAttribute('href')
    if (rawHref?.startsWith('#')) {
      e.preventDefault()
      const targetId = rawHref.slice(1)
      const target = (e.currentTarget as HTMLElement).querySelector(`[id="${targetId}"]`)
      if (target) {
        target.scrollIntoView({ behavior: 'smooth' })
      }
    }
    // External links are handled by the global click handler in main.tsx
    // which intercepts <a href> clicks and opens them via openUrl().
  }, [])

  return (
    <div className={`markdown-wrapper ${className || ''}`}>
      <div className="markdown-btn-group">
        {expandable && (
          <button
            className="markdown-action-btn"
            onClick={() => setExpanded(true)}
            title="Preview"
          >
            <IconEye size={14} />
          </button>
        )}
        <button
          className={`markdown-action-btn ${copied ? 'copied' : ''}`}
          onClick={handleCopy}
          title={copied ? 'Copied!' : 'Copy content'}
        >
          {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
        </button>
      </div>
      <div
        className="markdown-content"
        dangerouslySetInnerHTML={{ __html: html }}
        onClick={handleAnchorClick}
      />
      {expanded && (
        <div className="markdown-preview-overlay" onClick={() => setExpanded(false)}>
          <div className="markdown-preview-modal" onClick={e => e.stopPropagation()}>
            <div className="markdown-preview-header">
              <span className="markdown-preview-title">Preview</span>
              <div className="markdown-preview-actions">
                <button
                  className={`markdown-action-btn ${copied ? 'copied' : ''}`}
                  onClick={handleCopy}
                  title={copied ? 'Copied!' : 'Copy content'}
                >
                  {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
                </button>
                <button className="markdown-preview-close" onClick={() => setExpanded(false)}>×</button>
              </div>
            </div>
            <div className="markdown-preview-body">
              <div
                className="markdown-content"
                dangerouslySetInnerHTML={{ __html: html }}
                onClick={handleAnchorClick}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
