// === Top-level notebook model ===

export interface NotebookMetadata {
  kernelName: string | null
  language: string
  nbformat: number
  nbformatMinor: number
  raw: Record<string, unknown>
}

export interface NotebookCell {
  id: string
  type: 'code' | 'markdown' | 'raw'
  source: string
  executionCount: number | null
  outputs: CellOutput[]
  metadata: Record<string, unknown>
  attachments?: Record<string, Record<string, string>>
  /** Original cell JSON for round-trip fidelity on unmodified cells */
  _raw?: Record<string, unknown>
}

export interface CellOutput {
  outputType: 'stream' | 'display_data' | 'execute_result' | 'error'
  stream?: 'stdout' | 'stderr'
  text?: string
  data?: Record<string, string>
  outputMetadata?: Record<string, unknown>
  executionCount?: number | null
  ename?: string
  evalue?: string
  traceback?: string[]
  raw: Record<string, unknown>
}

export interface NotebookState {
  metadata: NotebookMetadata
  cells: NotebookCell[]
  parseErrors: string[]
  _originalIndent?: string
}

// === Undo/Redo ===

export type CellOperation =
  | { type: 'insert'; index: number; cell: NotebookCell }
  | { type: 'delete'; index: number; cell: NotebookCell }
  | { type: 'move'; fromIndex: number; toIndex: number }
  | { type: 'changeType'; cellId: string; oldType: NotebookCell['type']; newType: NotebookCell['type'] }
  | { type: 'merge'; index: number; deletedCell: NotebookCell; oldSource: string }
  | { type: 'split'; index: number; oldSource: string; newCellId: string }

// === Reducer State & Actions ===

export interface NotebookEditorState {
  notebook: NotebookState
  selectedCellId: string | null
  editingCellId: string | null
  collapsedCells: Set<string>
  collapsedOutputs: Set<string>
  showRawJson: boolean
  showLineNumbers: boolean
  dirty: boolean
}

export type NotebookAction =
  | { type: 'SET_NOTEBOOK'; notebook: NotebookState }
  | { type: 'SELECT_CELL'; cellId: string }
  | { type: 'ENTER_EDIT_MODE'; cellId: string }
  | { type: 'EXIT_EDIT_MODE' }
  | { type: 'UPDATE_CELL_SOURCE'; cellId: string; source: string }
  | { type: 'INSERT_CELL'; index: number; cellType: NotebookCell['type']; cell?: NotebookCell }
  | { type: 'DELETE_CELL'; cellId: string }
  | { type: 'MOVE_CELL'; cellId: string; direction: 'up' | 'down' }
  | { type: 'CHANGE_CELL_TYPE'; cellId: string; newType: NotebookCell['type'] }
  | { type: 'SPLIT_CELL'; cellId: string; cursorOffset: number }
  | { type: 'MERGE_CELL_ABOVE'; cellId: string }
  | { type: 'TOGGLE_COLLAPSE'; cellId: string }
  | { type: 'TOGGLE_OUTPUT_COLLAPSE'; cellId: string }
  | { type: 'TOGGLE_LINE_NUMBERS' }
  | { type: 'COLLAPSE_ALL' }
  | { type: 'EXPAND_ALL' }
  | { type: 'TOGGLE_JSON_VIEW' }
  | { type: 'UNDO' }
  | { type: 'REDO' }
  | { type: 'MARK_CLEAN' }
