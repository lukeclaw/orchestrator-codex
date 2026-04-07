import { describe, it, expect } from 'vitest'
import { parseAnsi } from '../AnsiRenderer'

describe('parseAnsi', () => {
  it('passes plain text through escaped', () => {
    expect(parseAnsi('hello world')).toBe('hello world')
  })

  it('escapes HTML characters', () => {
    expect(parseAnsi('<script>alert(1)</script>')).toBe('&lt;script&gt;alert(1)&lt;/script&gt;')
  })

  it('parses standard foreground color', () => {
    const result = parseAnsi('\x1b[31mred text\x1b[0m')
    expect(result).toContain('class="ansi-fg-red"')
    expect(result).toContain('red text')
  })

  it('parses bold text', () => {
    const result = parseAnsi('\x1b[1mbold\x1b[0m')
    expect(result).toContain('class="ansi-bold"')
    expect(result).toContain('bold')
  })

  it('parses bold + color combo', () => {
    const result = parseAnsi('\x1b[1;31mbold red\x1b[0m')
    expect(result).toContain('ansi-bold')
    expect(result).toContain('ansi-fg-red')
    expect(result).toContain('bold red')
  })

  it('handles reset in middle of styled text', () => {
    const result = parseAnsi('\x1b[31mred\x1b[0m normal')
    expect(result).toContain('ansi-fg-red')
    expect(result).toContain('red')
    expect(result).toContain('</span>')
    expect(result).toContain(' normal')
  })

  it('parses 256-color foreground', () => {
    const result = parseAnsi('\x1b[38;5;196mcolor\x1b[0m')
    expect(result).toContain('style="color:')
    expect(result).toContain('color')
  })

  it('parses 24-bit foreground color', () => {
    const result = parseAnsi('\x1b[38;2;255;128;0morange\x1b[0m')
    expect(result).toContain('style="color:rgb(255,128,0)"')
    expect(result).toContain('orange')
  })

  it('parses bright foreground colors', () => {
    const result = parseAnsi('\x1b[91mbright red\x1b[0m')
    expect(result).toContain('ansi-fg-bright-red')
  })

  it('parses background colors', () => {
    const result = parseAnsi('\x1b[42mgreen bg\x1b[0m')
    expect(result).toContain('ansi-bg-green')
  })

  it('parses italic and underline', () => {
    const result = parseAnsi('\x1b[3;4mitalic underline\x1b[0m')
    expect(result).toContain('ansi-italic')
    expect(result).toContain('ansi-underline')
  })

  it('handles strikethrough', () => {
    const result = parseAnsi('\x1b[9mstruck\x1b[0m')
    expect(result).toContain('ansi-strikethrough')
  })

  it('handles nested style changes', () => {
    const result = parseAnsi('\x1b[31mred \x1b[1mbold red\x1b[0m normal')
    expect(result).toContain('red ')
    expect(result).toContain('bold red')
    expect(result).toContain(' normal')
  })

  it('treats malformed sequences as literal text', () => {
    // Incomplete sequence (no m terminator within 32 chars)
    const result = parseAnsi('before\x1b[after')
    expect(result).toContain('before')
    expect(result).toContain('after')
  })

  it('handles default foreground reset (39)', () => {
    const result = parseAnsi('\x1b[31mred\x1b[39mdefault')
    expect(result).toContain('ansi-fg-red')
    expect(result).toContain('default')
  })

  it('handles multiple parameters in one sequence', () => {
    const result = parseAnsi('\x1b[1;3;4;31mstyled\x1b[0m')
    expect(result).toContain('ansi-bold')
    expect(result).toContain('ansi-italic')
    expect(result).toContain('ansi-underline')
    expect(result).toContain('ansi-fg-red')
  })

  it('handles empty input', () => {
    expect(parseAnsi('')).toBe('')
  })

  it('handles 256-color background', () => {
    const result = parseAnsi('\x1b[48;5;21mbg\x1b[0m')
    expect(result).toContain('style="background-color:')
  })

  it('handles 24-bit background', () => {
    const result = parseAnsi('\x1b[48;2;0;255;0mbg\x1b[0m')
    expect(result).toContain('background-color:rgb(0,255,0)')
  })
})
