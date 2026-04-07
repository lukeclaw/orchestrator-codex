import type { NotebookState, NotebookCell, CellOutput } from './notebookTypes'

const MAX_CELLS = 500

function joinSource(source: unknown): string {
  if (typeof source === 'string') return source
  if (Array.isArray(source)) return source.join('')
  return ''
}

function joinTextData(val: unknown): string {
  if (typeof val === 'string') return val
  if (Array.isArray(val)) return val.join('')
  return ''
}

function detectIndent(json: string): string {
  // Look for the first indented line to detect indentation style
  const match = json.match(/\n(\s+)/)
  return match ? match[1] : ' '
}

function parseOutput(raw: Record<string, unknown>): CellOutput {
  const outputType = raw.output_type as string

  if (outputType === 'stream') {
    return {
      outputType: 'stream',
      stream: (raw.name as 'stdout' | 'stderr') ?? 'stdout',
      text: joinTextData(raw.text),
      raw,
    }
  }

  if (outputType === 'display_data' || outputType === 'execute_result') {
    const data: Record<string, string> = {}
    const rawData = raw.data as Record<string, unknown> | undefined
    if (rawData) {
      for (const [mime, val] of Object.entries(rawData)) {
        data[mime] = joinTextData(val)
      }
    }
    return {
      outputType,
      data,
      outputMetadata: raw.metadata as Record<string, unknown> | undefined,
      executionCount: outputType === 'execute_result' ? (raw.execution_count as number | null) ?? null : undefined,
      raw,
    }
  }

  if (outputType === 'error') {
    return {
      outputType: 'error',
      ename: (raw.ename as string) ?? 'Error',
      evalue: (raw.evalue as string) ?? '',
      traceback: (raw.traceback as string[]) ?? [],
      raw,
    }
  }

  // Unknown output type — treat as display_data
  return { outputType: 'display_data', data: {}, raw }
}

function parseCell(rawCell: Record<string, unknown>, index: number): NotebookCell | null {
  const cellType = rawCell.cell_type as string | undefined
  if (!cellType || !['code', 'markdown', 'raw'].includes(cellType)) {
    return null
  }

  const id = (rawCell.id as string) || crypto.randomUUID().slice(0, 8)
  const source = joinSource(rawCell.source)
  const outputs: CellOutput[] = []

  if (cellType === 'code' && Array.isArray(rawCell.outputs)) {
    for (const rawOutput of rawCell.outputs) {
      if (rawOutput && typeof rawOutput === 'object') {
        outputs.push(parseOutput(rawOutput as Record<string, unknown>))
      }
    }
  }

  return {
    id,
    type: cellType as 'code' | 'markdown' | 'raw',
    source,
    executionCount: cellType === 'code' ? (rawCell.execution_count as number | null) ?? null : null,
    outputs,
    metadata: (rawCell.metadata as Record<string, unknown>) ?? {},
    attachments: rawCell.attachments as Record<string, Record<string, string>> | undefined,
    _raw: rawCell,
  }
}

export function parseNotebook(jsonString: string): NotebookState {
  const errors: string[] = []

  let raw: Record<string, unknown>
  try {
    raw = JSON.parse(jsonString)
  } catch (e) {
    return {
      metadata: { kernelName: null, language: 'python', nbformat: 4, nbformatMinor: 0, raw: {} },
      cells: [],
      parseErrors: [`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`],
    }
  }

  const nbformat = (raw.nbformat as number) ?? 0
  if (nbformat < 4) {
    return {
      metadata: { kernelName: null, language: 'python', nbformat, nbformatMinor: 0, raw: {} },
      cells: [],
      parseErrors: [`Unsupported notebook format (v${nbformat}). Only nbformat 4+ is supported.`],
    }
  }

  const metadata = (raw.metadata ?? {}) as Record<string, unknown>
  const kernelspec = metadata.kernelspec as Record<string, unknown> | undefined
  const languageInfo = metadata.language_info as Record<string, unknown> | undefined

  const indent = detectIndent(jsonString)
  const rawCells = raw.cells as Record<string, unknown>[] | undefined
  const cells: NotebookCell[] = []

  if (Array.isArray(rawCells)) {
    const limit = Math.min(rawCells.length, MAX_CELLS)
    for (let i = 0; i < limit; i++) {
      const cell = parseCell(rawCells[i], i)
      if (cell) {
        cells.push(cell)
      } else {
        errors.push(`Cell ${i + 1}: unknown type "${rawCells[i]?.cell_type ?? 'missing'}", skipped.`)
      }
    }
    if (rawCells.length > MAX_CELLS) {
      errors.push(`Notebook truncated at ${MAX_CELLS} cells. ${rawCells.length - MAX_CELLS} cells not shown.`)
    }
  }

  return {
    metadata: {
      kernelName: (kernelspec?.display_name as string) ?? null,
      language: (languageInfo?.name as string) ?? 'python',
      nbformat,
      nbformatMinor: (raw.nbformat_minor as number) ?? 0,
      raw: metadata,
    },
    cells,
    parseErrors: errors,
    _originalIndent: indent,
  }
}
