import { describe, it, expect } from 'vitest'
import { parseNotebook } from '../notebookParser'

const VALID_NOTEBOOK = JSON.stringify({
  nbformat: 4,
  nbformat_minor: 5,
  metadata: {
    kernelspec: { name: 'python3', display_name: 'Python 3' },
    language_info: { name: 'python', version: '3.11.0' },
  },
  cells: [
    {
      cell_type: 'code',
      id: 'abc123',
      source: ['import pandas as pd\n', 'df = pd.read_csv("data.csv")'],
      execution_count: 5,
      outputs: [
        { output_type: 'stream', name: 'stdout', text: ['Hello\n'] },
      ],
      metadata: {},
    },
    {
      cell_type: 'markdown',
      id: 'def456',
      source: '# Title\nSome text',
      metadata: {},
    },
    {
      cell_type: 'raw',
      id: 'ghi789',
      source: ['Raw text'],
      metadata: {},
    },
  ],
}, null, ' ')

describe('parseNotebook', () => {
  it('parses a valid v4 notebook', () => {
    const result = parseNotebook(VALID_NOTEBOOK)
    expect(result.parseErrors).toHaveLength(0)
    expect(result.metadata.kernelName).toBe('Python 3')
    expect(result.metadata.language).toBe('python')
    expect(result.metadata.nbformat).toBe(4)
    expect(result.cells).toHaveLength(3)
  })

  it('parses code cells with array source (joined with "")', () => {
    const result = parseNotebook(VALID_NOTEBOOK)
    const codeCell = result.cells[0]
    expect(codeCell.type).toBe('code')
    expect(codeCell.source).toBe('import pandas as pd\ndf = pd.read_csv("data.csv")')
    expect(codeCell.executionCount).toBe(5)
    expect(codeCell.id).toBe('abc123')
  })

  it('parses markdown cells with string source', () => {
    const result = parseNotebook(VALID_NOTEBOOK)
    const mdCell = result.cells[1]
    expect(mdCell.type).toBe('markdown')
    expect(mdCell.source).toBe('# Title\nSome text')
  })

  it('parses stream outputs', () => {
    const result = parseNotebook(VALID_NOTEBOOK)
    const outputs = result.cells[0].outputs
    expect(outputs).toHaveLength(1)
    expect(outputs[0].outputType).toBe('stream')
    expect(outputs[0].stream).toBe('stdout')
    expect(outputs[0].text).toBe('Hello\n')
  })

  it('handles missing metadata gracefully', () => {
    const nb = JSON.stringify({ nbformat: 4, nbformat_minor: 0, cells: [], metadata: {} })
    const result = parseNotebook(nb)
    expect(result.metadata.kernelName).toBeNull()
    expect(result.metadata.language).toBe('python')
    expect(result.parseErrors).toHaveLength(0)
  })

  it('returns error for invalid JSON', () => {
    const result = parseNotebook('not json{')
    expect(result.parseErrors.length).toBeGreaterThan(0)
    expect(result.parseErrors[0]).toContain('Invalid JSON')
    expect(result.cells).toHaveLength(0)
  })

  it('rejects nbformat < 4', () => {
    const nb = JSON.stringify({ nbformat: 3, cells: [], metadata: {} })
    const result = parseNotebook(nb)
    expect(result.parseErrors.length).toBeGreaterThan(0)
    expect(result.parseErrors[0]).toContain('Unsupported notebook format (v3)')
  })

  it('truncates at 500 cells', () => {
    const cells = Array.from({ length: 600 }, (_, i) => ({
      cell_type: 'code', id: `c${i}`, source: '', execution_count: null, outputs: [], metadata: {},
    }))
    const nb = JSON.stringify({ nbformat: 4, nbformat_minor: 0, cells, metadata: {} })
    const result = parseNotebook(nb)
    expect(result.cells).toHaveLength(500)
    expect(result.parseErrors.some(e => e.includes('truncated'))).toBe(true)
  })

  it('skips unknown cell types with warning', () => {
    const nb = JSON.stringify({
      nbformat: 4, nbformat_minor: 0, metadata: {},
      cells: [
        { cell_type: 'code', source: 'x = 1', outputs: [], metadata: {} },
        { cell_type: 'unknown_type', source: '', metadata: {} },
      ],
    })
    const result = parseNotebook(nb)
    expect(result.cells).toHaveLength(1)
    expect(result.parseErrors.some(e => e.includes('unknown type'))).toBe(true)
  })

  it('generates cell IDs when missing', () => {
    const nb = JSON.stringify({
      nbformat: 4, nbformat_minor: 0, metadata: {},
      cells: [{ cell_type: 'code', source: 'x = 1', outputs: [], metadata: {} }],
    })
    const result = parseNotebook(nb)
    expect(result.cells[0].id).toBeTruthy()
    expect(result.cells[0].id.length).toBeGreaterThan(0)
  })

  it('parses display_data outputs with MIME bundle', () => {
    const nb = JSON.stringify({
      nbformat: 4, nbformat_minor: 0, metadata: {},
      cells: [{
        cell_type: 'code', source: '', execution_count: 1, metadata: {},
        outputs: [{
          output_type: 'display_data',
          data: { 'image/png': 'base64data', 'text/plain': ['<Figure>'] },
          metadata: {},
        }],
      }],
    })
    const result = parseNotebook(nb)
    const output = result.cells[0].outputs[0]
    expect(output.outputType).toBe('display_data')
    expect(output.data?.['image/png']).toBe('base64data')
    expect(output.data?.['text/plain']).toBe('<Figure>')
  })

  it('parses error outputs', () => {
    const nb = JSON.stringify({
      nbformat: 4, nbformat_minor: 0, metadata: {},
      cells: [{
        cell_type: 'code', source: '', execution_count: 1, metadata: {},
        outputs: [{
          output_type: 'error',
          ename: 'ValueError',
          evalue: 'bad input',
          traceback: ['\x1b[31mValueError: bad input\x1b[0m'],
        }],
      }],
    })
    const result = parseNotebook(nb)
    const output = result.cells[0].outputs[0]
    expect(output.outputType).toBe('error')
    expect(output.ename).toBe('ValueError')
    expect(output.evalue).toBe('bad input')
    expect(output.traceback).toHaveLength(1)
  })

  it('handles empty cells array', () => {
    const nb = JSON.stringify({ nbformat: 4, nbformat_minor: 0, cells: [], metadata: {} })
    const result = parseNotebook(nb)
    expect(result.cells).toHaveLength(0)
    expect(result.parseErrors).toHaveLength(0)
  })

  it('detects indentation', () => {
    const nb = JSON.stringify({ nbformat: 4, nbformat_minor: 0, cells: [], metadata: {} }, null, 2)
    const result = parseNotebook(nb)
    expect(result._originalIndent).toBe('  ')
  })

  it('preserves raw cell data for round-trip', () => {
    const result = parseNotebook(VALID_NOTEBOOK)
    expect(result.cells[0]._raw).toBeDefined()
    expect(result.cells[0]._raw?.cell_type).toBe('code')
  })
})
