import { describe, it, expect } from 'vitest'
import { parseNotebook } from '../notebookParser'
import { serializeNotebook } from '../notebookSerializer'

const SAMPLE_NOTEBOOK = JSON.stringify({
  cells: [
    {
      cell_type: 'code',
      execution_count: 1,
      id: 'abc123',
      metadata: {},
      outputs: [
        { output_type: 'stream', name: 'stdout', text: ['hello\n'] },
      ],
      source: ['print("hello")\n'],
    },
    {
      cell_type: 'markdown',
      id: 'def456',
      metadata: {},
      source: ['# Title\n'],
    },
  ],
  metadata: {
    kernelspec: { name: 'python3', display_name: 'Python 3' },
    language_info: { name: 'python' },
  },
  nbformat: 4,
  nbformat_minor: 5,
}, null, ' ') + '\n'

describe('serializeNotebook', () => {
  it('round-trips an unmodified notebook', () => {
    const parsed = parseNotebook(SAMPLE_NOTEBOOK)
    const serialized = serializeNotebook(parsed)
    expect(serialized).toBe(SAMPLE_NOTEBOOK)
  })

  it('serializes a modified cell with sorted keys', () => {
    const parsed = parseNotebook(SAMPLE_NOTEBOOK)
    // Modify the first cell
    parsed.cells[0] = { ...parsed.cells[0], source: 'print("world")\n', _raw: undefined }
    const serialized = serializeNotebook(parsed)
    const reparsed = JSON.parse(serialized)

    expect(reparsed.cells[0].source).toEqual(['print("world")\n'])
    // Keys should be alphabetically sorted
    const keys = Object.keys(reparsed.cells[0])
    expect(keys).toEqual([...keys].sort())
  })

  it('preserves unmodified cells as raw', () => {
    const parsed = parseNotebook(SAMPLE_NOTEBOOK)
    // Modify only the second cell
    parsed.cells[1] = { ...parsed.cells[1], source: '# New Title\n', _raw: undefined }
    const serialized = serializeNotebook(parsed)
    const reparsed = JSON.parse(serialized)

    // First cell should be exactly the same as original
    expect(reparsed.cells[0]).toEqual(JSON.parse(SAMPLE_NOTEBOOK).cells[0])
  })

  it('splits source into line arrays', () => {
    const parsed = parseNotebook(SAMPLE_NOTEBOOK)
    parsed.cells[0] = { ...parsed.cells[0], source: 'line1\nline2\nline3', _raw: undefined }
    const serialized = serializeNotebook(parsed)
    const reparsed = JSON.parse(serialized)

    expect(reparsed.cells[0].source).toEqual(['line1\n', 'line2\n', 'line3'])
  })

  it('handles empty source', () => {
    const parsed = parseNotebook(SAMPLE_NOTEBOOK)
    parsed.cells[0] = { ...parsed.cells[0], source: '', _raw: undefined }
    const serialized = serializeNotebook(parsed)
    const reparsed = JSON.parse(serialized)

    expect(reparsed.cells[0].source).toEqual([''])
  })

  it('detects and uses original indentation', () => {
    const twoSpace = JSON.stringify({
      cells: [], metadata: {}, nbformat: 4, nbformat_minor: 0,
    }, null, '  ') + '\n'
    const parsed = parseNotebook(twoSpace)
    expect(parsed._originalIndent).toBe('  ')
    const serialized = serializeNotebook(parsed)
    expect(serialized).toBe(twoSpace)
  })

  it('adds trailing newline', () => {
    const parsed = parseNotebook(SAMPLE_NOTEBOOK)
    const serialized = serializeNotebook(parsed)
    expect(serialized.endsWith('\n')).toBe(true)
  })

  it('serializes empty notebook', () => {
    const nb = JSON.stringify({ cells: [], metadata: {}, nbformat: 4, nbformat_minor: 0 }, null, ' ') + '\n'
    const parsed = parseNotebook(nb)
    const serialized = serializeNotebook(parsed)
    expect(serialized).toBe(nb)
  })
})
