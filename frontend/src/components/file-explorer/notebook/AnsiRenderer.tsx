import { escapeHtml } from '../../common/Markdown'

// Standard 16-color palette (xterm defaults)
const COLORS_16 = [
  '#1d1f21', '#cc6666', '#b5bd68', '#f0c674', '#81a2be', '#b294bb', '#8abeb7', '#c5c8c6', // standard
  '#969896', '#de935f', '#a3be8c', '#ebcb8b', '#7cafc2', '#ba8baf', '#86c1b9', '#ffffff', // bright
]

// 6x6x6 color cube (indices 16-231)
function cubeColor(index: number): string {
  const i = index - 16
  const r = Math.floor(i / 36)
  const g = Math.floor((i % 36) / 6)
  const b = i % 6
  const toVal = (c: number) => c === 0 ? 0 : 55 + c * 40
  return `rgb(${toVal(r)},${toVal(g)},${toVal(b)})`
}

// Grayscale ramp (indices 232-255)
function grayColor(index: number): string {
  const v = 8 + (index - 232) * 10
  return `rgb(${v},${v},${v})`
}

function color256(index: number): string {
  if (index < 16) return COLORS_16[index]
  if (index < 232) return cubeColor(index)
  return grayColor(index)
}

const COLOR_NAMES = ['black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'] as const

interface AnsiState {
  bold: boolean
  italic: boolean
  underline: boolean
  strikethrough: boolean
  fg: string | null // class name or inline color
  bg: string | null
  fgInline: string | null // for 256/24-bit colors
  bgInline: string | null
}

function resetState(): AnsiState {
  return { bold: false, italic: false, underline: false, strikethrough: false, fg: null, bg: null, fgInline: null, bgInline: null }
}

function buildSpanOpen(s: AnsiState): string {
  const classes: string[] = []
  const styles: string[] = []

  if (s.bold) classes.push('ansi-bold')
  if (s.italic) classes.push('ansi-italic')
  if (s.underline) classes.push('ansi-underline')
  if (s.strikethrough) classes.push('ansi-strikethrough')
  if (s.fg) classes.push(s.fg)
  if (s.bg) classes.push(s.bg)
  if (s.fgInline) styles.push(`color:${s.fgInline}`)
  if (s.bgInline) styles.push(`background-color:${s.bgInline}`)

  if (classes.length === 0 && styles.length === 0) return ''

  let tag = '<span'
  if (classes.length > 0) tag += ` class="${classes.join(' ')}"`
  if (styles.length > 0) tag += ` style="${styles.join(';')}"`
  tag += '>'
  return tag
}

function parseParams(seq: string): number[] {
  if (!seq) return [0]
  return seq.split(';').map(s => parseInt(s, 10) || 0)
}

export function parseAnsi(text: string): string {
  let result = ''
  let state = resetState()
  let spanOpen = false
  let i = 0

  while (i < text.length) {
    // Check for ESC sequence
    if (text[i] === '\x1b' && text[i + 1] === '[') {
      // Find the 'm' terminator
      let j = i + 2
      while (j < text.length && text[j] !== 'm' && j - i < 32) j++
      if (j < text.length && text[j] === 'm') {
        const paramStr = text.slice(i + 2, j)
        const params = parseParams(paramStr)

        let k = 0
        while (k < params.length) {
          const p = params[k]
          switch (p) {
            case 0: state = resetState(); break
            case 1: state.bold = true; break
            case 3: state.italic = true; break
            case 4: state.underline = true; break
            case 9: state.strikethrough = true; break
            case 22: state.bold = false; break
            case 23: state.italic = false; break
            case 24: state.underline = false; break
            case 29: state.strikethrough = false; break
            case 39: state.fg = null; state.fgInline = null; break
            case 49: state.bg = null; state.bgInline = null; break
            default:
              if (p >= 30 && p <= 37) {
                state.fg = `ansi-fg-${COLOR_NAMES[p - 30]}`; state.fgInline = null
              } else if (p >= 40 && p <= 47) {
                state.bg = `ansi-bg-${COLOR_NAMES[p - 40]}`; state.bgInline = null
              } else if (p >= 90 && p <= 97) {
                state.fg = `ansi-fg-bright-${COLOR_NAMES[p - 90]}`; state.fgInline = null
              } else if (p >= 100 && p <= 107) {
                state.bg = `ansi-bg-bright-${COLOR_NAMES[p - 100]}`; state.bgInline = null
              } else if ((p === 38 || p === 48) && k + 1 < params.length) {
                const mode = params[k + 1]
                if (mode === 5 && k + 2 < params.length) {
                  // 256-color
                  const color = color256(params[k + 2])
                  if (p === 38) { state.fgInline = color; state.fg = null }
                  else { state.bgInline = color; state.bg = null }
                  k += 2
                } else if (mode === 2 && k + 4 < params.length) {
                  // 24-bit color
                  const color = `rgb(${params[k + 2]},${params[k + 3]},${params[k + 4]})`
                  if (p === 38) { state.fgInline = color; state.fg = null }
                  else { state.bgInline = color; state.bg = null }
                  k += 4
                }
              }
          }
          k++
        }

        // Close current span if open, open new one if needed
        if (spanOpen) { result += '</span>'; spanOpen = false }
        i = j + 1
        continue
      }
    }

    // Regular character — open span if needed
    if (!spanOpen) {
      const open = buildSpanOpen(state)
      if (open) {
        result += open
        spanOpen = true
      }
    }
    result += escapeHtml(text[i])
    i++
  }

  if (spanOpen) result += '</span>'
  return result
}

export default function AnsiRenderer({ text }: { text: string }) {
  return <pre className="nb-ansi" dangerouslySetInnerHTML={{ __html: parseAnsi(text) }} />
}
