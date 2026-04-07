import type { NotebookState, NotebookCell } from './notebookTypes'

function splitSource(source: string): string[] {
  if (source === '') return ['']
  const lines = source.split('\n')
  // Each line except the last gets '\n' appended.
  // If source ends with '\n', split produces a trailing empty string — drop it.
  const result = lines.map((line, i) => i < lines.length - 1 ? line + '\n' : line)
  if (result.length > 1 && result[result.length - 1] === '') {
    result.pop()
  }
  return result
}

function joinRawSource(raw: unknown): string {
  if (typeof raw === 'string') return raw
  if (Array.isArray(raw)) return raw.join('')
  return ''
}

function isCellUnmodified(cell: NotebookCell): boolean {
  if (!cell._raw) return false
  const originalSource = joinRawSource(cell._raw.source)
  return cell.source === originalSource
}

function sortKeys(obj: Record<string, unknown>): Record<string, unknown> {
  const sorted: Record<string, unknown> = {}
  for (const key of Object.keys(obj).sort()) {
    sorted[key] = obj[key]
  }
  return sorted
}

function buildCellJson(cell: NotebookCell): Record<string, unknown> {
  const obj: Record<string, unknown> = {
    cell_type: cell.type,
    id: cell.id,
    metadata: cell.metadata,
    source: splitSource(cell.source),
  }

  if (cell.type === 'code') {
    obj.execution_count = cell.executionCount
    obj.outputs = cell.outputs.map(o => o.raw)
  }

  return sortKeys(obj)
}

export function serializeNotebook(state: NotebookState): string {
  const indent = state._originalIndent ?? ' '

  const cells = state.cells.map(cell => {
    // Use raw verbatim for unmodified cells (byte-identical round-trip)
    if (isCellUnmodified(cell)) {
      return cell._raw!
    }
    return buildCellJson(cell)
  })

  const obj: Record<string, unknown> = {
    cells,
    metadata: state.metadata.raw,
    nbformat: state.metadata.nbformat,
    nbformat_minor: state.metadata.nbformatMinor,
  }

  return JSON.stringify(obj, null, indent) + '\n'
}
