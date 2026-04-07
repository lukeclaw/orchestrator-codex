import { useReducer, useCallback, useRef, useEffect, useState } from 'react'
import { loader } from '@monaco-editor/react'
import type { editor } from 'monaco-editor'
import type { NotebookEditorState, NotebookAction, CellOperation } from './notebook/notebookTypes'
import { parseNotebook } from './notebook/notebookParser'
import { serializeNotebook } from './notebook/notebookSerializer'
import { NotebookUndoStack } from './notebook/notebookUndoStack'
import NotebookCell from './notebook/NotebookCell'
import NotebookToolbar from './notebook/NotebookToolbar'
import CellInsertBar from './notebook/CellInsertBar'
import './NotebookEditor.css'

interface NotebookEditorProps {
  content: string
  onContentChange: (json: string) => void
  sessionId: string
}

function getMonacoTheme(): string {
  return document.documentElement.getAttribute('data-theme') === 'light'
    ? 'cool-light'
    : 'cool-dark'
}

const LANG_MAP: Record<string, string> = {
  python: 'python', javascript: 'javascript', typescript: 'typescript',
  r: 'r', julia: 'julia', rust: 'rust', go: 'go', java: 'java',
  c: 'c', cpp: 'cpp', ruby: 'ruby', sql: 'sql', bash: 'shell',
}

function monacoLanguage(lang: string): string {
  return LANG_MAP[lang] ?? 'plaintext'
}

// ── Reducer ──────────────────────────────────────────────────────────────

function notebookReducer(state: NotebookEditorState, action: NotebookAction): NotebookEditorState {
  switch (action.type) {
    case 'SET_NOTEBOOK':
      return { ...state, notebook: action.notebook, dirty: false, editingCellId: null }
    case 'SELECT_CELL':
      return { ...state, selectedCellId: action.cellId, editingCellId: null }
    case 'ENTER_EDIT_MODE':
      return { ...state, selectedCellId: action.cellId, editingCellId: action.cellId }
    case 'EXIT_EDIT_MODE':
      return { ...state, editingCellId: null }
    case 'UPDATE_CELL_SOURCE': {
      const cells = state.notebook.cells.map(c =>
        c.id === action.cellId ? { ...c, source: action.source, _raw: undefined } : c
      )
      return { ...state, notebook: { ...state.notebook, cells }, dirty: true }
    }
    case 'DELETE_CELL': {
      const cells = state.notebook.cells.filter(c => c.id !== action.cellId)
      const wasSelected = state.selectedCellId === action.cellId
      const wasEditing = state.editingCellId === action.cellId
      const idx = state.notebook.cells.findIndex(c => c.id === action.cellId)
      const nextSelected = wasSelected
        ? (cells[Math.min(idx, cells.length - 1)]?.id ?? null)
        : state.selectedCellId
      return {
        ...state,
        notebook: { ...state.notebook, cells },
        selectedCellId: nextSelected,
        editingCellId: wasEditing ? null : state.editingCellId,
        dirty: true,
      }
    }
    case 'CHANGE_CELL_TYPE': {
      const cells = state.notebook.cells.map(c =>
        c.id === action.cellId
          ? { ...c, type: action.newType, outputs: action.newType === 'code' ? c.outputs : [], _raw: undefined }
          : c
      )
      return { ...state, notebook: { ...state.notebook, cells }, dirty: true, editingCellId: null }
    }
    case 'INSERT_CELL': {
      const newCell = action.cell ?? {
        id: crypto.randomUUID().slice(0, 8),
        type: action.cellType,
        source: '',
        executionCount: null,
        outputs: [],
        metadata: {},
      }
      const cells = [...state.notebook.cells]
      cells.splice(action.index, 0, newCell)
      return {
        ...state,
        notebook: { ...state.notebook, cells },
        selectedCellId: newCell.id,
        editingCellId: newCell.id,
        dirty: true,
      }
    }
    case 'MOVE_CELL': {
      const idx = state.notebook.cells.findIndex(c => c.id === action.cellId)
      if (idx === -1) return state
      const newIdx = action.direction === 'up' ? idx - 1 : idx + 1
      if (newIdx < 0 || newIdx >= state.notebook.cells.length) return state
      const cells = [...state.notebook.cells]
      ;[cells[idx], cells[newIdx]] = [cells[newIdx], cells[idx]]
      return { ...state, notebook: { ...state.notebook, cells }, dirty: true }
    }
    case 'TOGGLE_OUTPUT_COLLAPSE': {
      const next = new Set(state.collapsedOutputs)
      if (next.has(action.cellId)) next.delete(action.cellId)
      else next.add(action.cellId)
      return { ...state, collapsedOutputs: next }
    }
    case 'TOGGLE_LINE_NUMBERS':
      return { ...state, showLineNumbers: !state.showLineNumbers }
    case 'COLLAPSE_ALL': {
      const all = new Set(state.notebook.cells.filter(c => c.outputs.length > 0).map(c => c.id))
      return { ...state, collapsedOutputs: all }
    }
    case 'EXPAND_ALL':
      return { ...state, collapsedOutputs: new Set() }
    case 'TOGGLE_JSON_VIEW':
      return { ...state, showRawJson: !state.showRawJson }
    case 'MARK_CLEAN':
      return { ...state, dirty: false }
    case 'UNDO':
    case 'REDO':
      // Handled by the component wrapper — should not reach here
      return state
    default:
      return state
  }
}

function createInitialState(content: string): NotebookEditorState {
  const notebook = parseNotebook(content)
  return {
    notebook,
    selectedCellId: notebook.cells[0]?.id ?? null,
    editingCellId: null,
    collapsedCells: new Set(),
    collapsedOutputs: new Set(),
    showRawJson: false,
    showLineNumbers: true,
    dirty: false,
  }
}

// ── Component ────────────────────────────────────────────────────────────

export default function NotebookEditor({ content, onContentChange, sessionId: __ }: NotebookEditorProps) {
  const [state, rawDispatch] = useReducer(notebookReducer, content, createInitialState)
  const containerRef = useRef<HTMLDivElement>(null)
  const undoStackRef = useRef(new NotebookUndoStack())
  // Use a ref to read current cells inside dispatch without adding it as a dependency
  const cellsForUndoRef = useRef(state.notebook.cells)
  cellsForUndoRef.current = state.notebook.cells

  // Applies the inverse of an operation (for undo)
  const applyInverse = useCallback((op: CellOperation) => {
    switch (op.type) {
      case 'delete':
        // Undo delete = re-insert the deleted cell at its original index
        rawDispatch({ type: 'INSERT_CELL', index: op.index, cellType: op.cell.type, cell: op.cell })
        break
      case 'insert':
        // Undo insert = delete the cell at that index
        rawDispatch({ type: 'DELETE_CELL', cellId: op.cell.id })
        break
      case 'move':
        // Undo move = swap back
        rawDispatch({ type: 'MOVE_CELL', cellId: cellsForUndoRef.current[op.toIndex]?.id ?? '', direction: op.toIndex > op.fromIndex ? 'up' : 'down' })
        break
      case 'changeType':
        // Undo type change = change back to old type
        rawDispatch({ type: 'CHANGE_CELL_TYPE', cellId: op.cellId, newType: op.oldType })
        break
    }
  }, [])

  // Applies an operation forward (for redo)
  const applyForward = useCallback((op: CellOperation) => {
    switch (op.type) {
      case 'delete':
        // Redo delete = delete again
        rawDispatch({ type: 'DELETE_CELL', cellId: op.cell.id })
        break
      case 'insert':
        // Redo insert = insert again
        rawDispatch({ type: 'INSERT_CELL', index: op.index, cellType: op.cell.type, cell: op.cell })
        break
      case 'move':
        // Redo move = move again
        rawDispatch({ type: 'MOVE_CELL', cellId: cellsForUndoRef.current[op.fromIndex]?.id ?? '', direction: op.toIndex > op.fromIndex ? 'down' : 'up' })
        break
      case 'changeType':
        // Redo type change = apply new type again
        rawDispatch({ type: 'CHANGE_CELL_TYPE', cellId: op.cellId, newType: op.newType })
        break
    }
  }, [])

  // Wrapper that intercepts structural actions to record ops on the undo stack,
  // and handles UNDO/REDO by popping and applying inverse/forward operations.
  const dispatch = useCallback((action: NotebookAction) => {
    const cells = cellsForUndoRef.current
    const stack = undoStackRef.current

    if (action.type === 'UNDO') {
      const op = stack.undo()
      if (op) applyInverse(op)
      return
    }
    if (action.type === 'REDO') {
      const op = stack.redo()
      if (op) applyForward(op)
      return
    }

    // Record operation for structural actions (before dispatching)
    if (action.type === 'DELETE_CELL') {
      const idx = cells.findIndex(c => c.id === action.cellId)
      if (idx !== -1) stack.push({ type: 'delete', index: idx, cell: { ...cells[idx] } })
    } else if (action.type === 'INSERT_CELL') {
      // The cell might be provided (redo) or generated by reducer. For generated cells,
      // we record after dispatch using the updated cells array.
      rawDispatch(action)
      // Read the newly inserted cell from state after dispatch
      queueMicrotask(() => {
        const newCells = cellsForUndoRef.current
        const cell = newCells[action.index]
        if (cell) stack.push({ type: 'insert', index: action.index, cell: { ...cell } })
      })
      return
    } else if (action.type === 'MOVE_CELL') {
      const idx = cells.findIndex(c => c.id === action.cellId)
      if (idx !== -1) {
        const toIdx = action.direction === 'up' ? idx - 1 : idx + 1
        if (toIdx >= 0 && toIdx < cells.length) {
          stack.push({ type: 'move', fromIndex: idx, toIndex: toIdx })
        }
      }
    } else if (action.type === 'CHANGE_CELL_TYPE') {
      const cell = cells.find(c => c.id === action.cellId)
      if (cell) stack.push({ type: 'changeType', cellId: action.cellId, oldType: cell.type, newType: action.newType })
    }

    rawDispatch(action)
  }, [applyInverse, applyForward])

  // Monaco editor refs
  const monacoRef = useRef<editor.IStandaloneCodeEditor | null>(null)
  const editorContainerRef = useRef<HTMLDivElement>(null)
  const monacoApiRef = useRef<typeof import('monaco-editor') | null>(null)
  const isSettingValueRef = useRef(false)
  const updateTimerRef = useRef<number | null>(null)
  const editingCellIdRef = useRef<string | null>(null)
  // State (not ref) so the cell focus transition effect re-runs after async init
  const [monacoReady, setMonacoReady] = useState(false)

  // Keep ref in sync so the onChange handler can read current editingCellId
  editingCellIdRef.current = state.editingCellId

  const { cells, metadata, parseErrors } = state.notebook

  // ── Monaco Init (once) ────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false

    loader.init().then((monaco) => {
      // If the effect was cleaned up before the Promise resolved (React StrictMode),
      // do NOT create an editor — it would be orphaned.
      if (cancelled) return
      monacoApiRef.current = monaco
      if (!editorContainerRef.current) return

      const ed = monaco.editor.create(editorContainerRef.current, {
        language: 'python',
        theme: getMonacoTheme(),
        fontSize: 12,
        lineHeight: 19,
        lineNumbers: 'on',
        lineNumbersMinChars: 3,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: false,
        wordWrap: 'on',
        tabSize: 4,
        padding: { top: 8, bottom: 4 },
        overviewRulerLanes: 0,
        hideCursorInOverviewRuler: true,
        scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8, alwaysConsumeMouseWheel: false },
        renderWhitespace: 'none',
        contextmenu: false,
        folding: false,
        glyphMargin: false,
        renderLineHighlight: 'none',
      })

      monacoRef.current = ed
      setMonacoReady(true)

      // Content change handler (debounced 100ms)
      ed.onDidChangeModelContent(() => {
        if (isSettingValueRef.current) return
        const value = ed.getModel()?.getValue() ?? ''
        const cellId = editingCellIdRef.current
        if (!cellId) return
        if (updateTimerRef.current) clearTimeout(updateTimerRef.current)
        updateTimerRef.current = window.setTimeout(() => {
          dispatch({ type: 'UPDATE_CELL_SOURCE', cellId, source: value })
        }, 100)
      })

      // Auto-height
      ed.onDidContentSizeChange(() => {
        if (!editorContainerRef.current) return
        const h = Math.max(19, ed.getContentHeight())
        editorContainerRef.current.style.height = `${h}px`
        ed.layout()
      })
    })

    return () => {
      cancelled = true
      if (updateTimerRef.current) clearTimeout(updateTimerRef.current)
      const domNode = monacoRef.current?.getDomNode()
      monacoRef.current?.dispose()
      domNode?.remove()
      monacoRef.current = null
      setMonacoReady(false)
    }
  }, [])

  // ── Theme sync ────────────────────────────────────────────────────────

  useEffect(() => {
    const observer = new MutationObserver(() => {
      monacoApiRef.current?.editor.setTheme(getMonacoTheme())
    })
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])

  // ── Save integration (debounced serialization) ────────────────────────

  const serializeTimerRef = useRef<number | null>(null)

  useEffect(() => {
    if (!state.dirty) return

    if (serializeTimerRef.current) clearTimeout(serializeTimerRef.current)
    serializeTimerRef.current = window.setTimeout(() => {
      onContentChange(serializeNotebook(state.notebook))
    }, 300)

    return () => {
      if (serializeTimerRef.current) clearTimeout(serializeTimerRef.current)
    }
  }, [state.dirty, state.notebook, onContentChange])

  // ── Cell focus transition ─────────────────────────────────────────────

  // Use a ref to access cells without adding them to the effect's dependency array.
  // This prevents the effect from re-running when cell content changes (which would
  // call model.setValue() mid-typing, resetting the cursor and selecting text).
  const cellsRef = useRef(cells)
  cellsRef.current = cells

  // Track what we last mounted so we don't re-setValue during typing.
  // Reset when editingCellId or monacoReady changes so the effect re-runs.
  const mountedCellRef = useRef<string | null>(null)

  useEffect(() => {
    const ed = monacoRef.current
    const monaco = monacoApiRef.current
    if (!ed || !monaco) return

    if (!state.editingCellId) {
      // Hide Monaco
      if (editorContainerRef.current) {
        editorContainerRef.current.style.display = 'none'
        if (editorContainerRef.current.parentElement?.id?.startsWith('nb-editor-slot-')) {
          containerRef.current?.appendChild(editorContainerRef.current)
        }
      }
      mountedCellRef.current = null
      return
    }

    // Skip if we already mounted this cell (prevents re-setValue during typing
    // when other deps like monacoReady haven't changed)
    if (state.editingCellId === mountedCellRef.current) return
    mountedCellRef.current = state.editingCellId

    // Find the target cell's editor slot
    const slot = document.getElementById(`nb-editor-slot-${state.editingCellId}`)
    if (!slot || !editorContainerRef.current) return

    // Move Monaco container into the slot
    slot.appendChild(editorContainerRef.current)
    editorContainerRef.current.style.display = 'block'

    // Load cell content from ref (avoids stale closure AND avoids cells dependency)
    const cell = cellsRef.current.find(c => c.id === state.editingCellId)
    if (!cell) return

    const lang = cell.type === 'markdown' ? 'markdown'
      : cell.type === 'raw' ? 'plaintext'
      : monacoLanguage(metadata.language)

    const model = ed.getModel()
    if (model) {
      isSettingValueRef.current = true
      monaco.editor.setModelLanguage(model, lang)
      model.setValue(cell.source)
      isSettingValueRef.current = false
    }

    // Focus and layout
    requestAnimationFrame(() => {
      if (!editorContainerRef.current) return
      const h = Math.max(19, ed.getContentHeight())
      editorContainerRef.current.style.height = `${h}px`
      ed.layout()
      ed.focus()
      // Place cursor at end
      const lineCount = model?.getLineCount() ?? 1
      const lastCol = model?.getLineMaxColumn(lineCount) ?? 1
      ed.setPosition({ lineNumber: lineCount, column: lastCol })
    })
  }, [state.editingCellId, metadata.language, monacoReady])

  // When monacoReady changes, reset mountedCellRef so the effect re-runs the mount logic
  useEffect(() => {
    mountedCellRef.current = null
  }, [monacoReady])

  // ── Keyboard handler ──────────────────────────────────────────────────

  const dTimerRef = useRef<number | null>(null)

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    // In edit mode, only intercept Escape
    if (state.editingCellId) {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        // Flush any pending update
        if (updateTimerRef.current) {
          clearTimeout(updateTimerRef.current)
          updateTimerRef.current = null
          const value = monacoRef.current?.getModel()?.getValue() ?? ''
          const cellId = editingCellIdRef.current
          if (cellId) dispatch({ type: 'UPDATE_CELL_SOURCE', cellId, source: value })
        }
        dispatch({ type: 'EXIT_EDIT_MODE' })
        containerRef.current?.focus()
      }
      return
    }

    // Command mode shortcuts
    // Alt+Arrow for move
    if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
      e.preventDefault()
      e.stopPropagation()
      if (state.selectedCellId) {
        dispatch({ type: 'MOVE_CELL', cellId: state.selectedCellId, direction: e.key === 'ArrowUp' ? 'up' : 'down' })
      }
      return
    }

    switch (e.key) {
      case 'Enter':
        e.preventDefault()
        e.stopPropagation()
        if (state.selectedCellId) dispatch({ type: 'ENTER_EDIT_MODE', cellId: state.selectedCellId })
        break
      case 'j':
      case 'ArrowDown':
        e.preventDefault()
        e.stopPropagation()
        selectAdjacentCell(1)
        break
      case 'k':
      case 'ArrowUp':
        e.preventDefault()
        e.stopPropagation()
        selectAdjacentCell(-1)
        break
      case 'a':
        e.preventDefault()
        e.stopPropagation()
        insertCellRelative('above', 'code')
        break
      case 'b':
        e.preventDefault()
        e.stopPropagation()
        insertCellRelative('below', 'code')
        break
      case 'd':
        e.preventDefault()
        e.stopPropagation()
        handleDoubleD()
        break
      case 'm':
        e.preventDefault()
        e.stopPropagation()
        if (state.selectedCellId) dispatch({ type: 'CHANGE_CELL_TYPE', cellId: state.selectedCellId, newType: 'markdown' })
        break
      case 'y':
        e.preventDefault()
        e.stopPropagation()
        if (state.selectedCellId) dispatch({ type: 'CHANGE_CELL_TYPE', cellId: state.selectedCellId, newType: 'code' })
        break
      case 'z':
        e.preventDefault()
        e.stopPropagation()
        dispatch({ type: e.shiftKey ? 'REDO' : 'UNDO' })
        break
      case 'Z': // Shift+z
        e.preventDefault()
        e.stopPropagation()
        dispatch({ type: 'REDO' })
        break
      case 'o':
        e.preventDefault()
        e.stopPropagation()
        if (state.selectedCellId) dispatch({ type: 'TOGGLE_OUTPUT_COLLAPSE', cellId: state.selectedCellId })
        break
      case 'l':
        e.preventDefault()
        e.stopPropagation()
        dispatch({ type: 'TOGGLE_LINE_NUMBERS' })
        break
    }
  }, [state.editingCellId, state.selectedCellId, cells])

  // ── Helpers ───────────────────────────────────────────────────────────

  const selectAdjacentCell = useCallback((dir: 1 | -1) => {
    const idx = cells.findIndex(c => c.id === state.selectedCellId)
    const next = idx + dir
    if (next >= 0 && next < cells.length) {
      dispatch({ type: 'SELECT_CELL', cellId: cells[next].id })
      document.getElementById(`nb-cell-${cells[next].id}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [cells, state.selectedCellId])

  const insertCellRelative = useCallback((pos: 'above' | 'below', cellType: 'code' | 'markdown') => {
    const idx = cells.findIndex(c => c.id === state.selectedCellId)
    const insertIdx = pos === 'above' ? Math.max(0, idx) : idx + 1
    dispatch({ type: 'INSERT_CELL', index: insertIdx, cellType })
  }, [cells, state.selectedCellId])

  const handleDoubleD = useCallback(() => {
    if (dTimerRef.current !== null) {
      clearTimeout(dTimerRef.current)
      dTimerRef.current = null
      if (state.selectedCellId) dispatch({ type: 'DELETE_CELL', cellId: state.selectedCellId })
    } else {
      dTimerRef.current = window.setTimeout(() => { dTimerRef.current = null }, 500)
    }
  }, [state.selectedCellId])

  // ── Lazy rendering (IntersectionObserver) ───────────────────────────

  // Lazy rendering: only use IntersectionObserver for large notebooks (>30 cells)
  const useLazy = cells.length > 30
  const observerRef = useRef<IntersectionObserver | null>(null)
  const [observedCells, setObservedCells] = useState<Set<string>>(() => {
    return new Set(cells.slice(0, 20).map(c => c.id))
  })

  useEffect(() => {
    if (!useLazy) return

    observerRef.current = new IntersectionObserver(
      (entries) => {
        setObservedCells(prev => {
          const next = new Set(prev)
          let changed = false
          for (const entry of entries) {
            const cellId = entry.target.getAttribute('data-cell-id')
            if (cellId && entry.isIntersecting && !next.has(cellId)) {
              next.add(cellId)
              changed = true
            }
          }
          return changed ? next : prev
        })
      },
      { rootMargin: '200px' }
    )

    return () => { observerRef.current?.disconnect() }
  }, [useLazy])

  const placeholderRef = useCallback((el: HTMLDivElement | null) => {
    if (el && observerRef.current) observerRef.current.observe(el)
  }, [])

  // A cell is visible if: small notebook (all visible), or observed by IntersectionObserver,
  // or currently selected/editing (must always render for keyboard nav and Monaco mounting)
  const isCellVisible = useCallback((cellId: string) => {
    if (!useLazy) return true
    return observedCells.has(cellId) || cellId === state.selectedCellId || cellId === state.editingCellId
  }, [useLazy, observedCells, state.selectedCellId, state.editingCellId])

  // ── Render ────────────────────────────────────────────────────────────

  const langDisplay = metadata.kernelName ?? metadata.language
  const cellCount = cells.length
  const hasOutputs = cells.some(c => c.outputs.length > 0)

  return (
    <div
      className="nb-editor"
      ref={containerRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      {/* Header bar */}
      <div className="nb-header" role="toolbar">
        <span className="nb-header__language">{langDisplay}</span>
        <span className="nb-header__sep">&middot;</span>
        <span className="nb-header__count">{cellCount} cell{cellCount !== 1 ? 's' : ''}</span>
        <div className="nb-header__spacer" />
        {hasOutputs && (
          <>
            <button
              className="nb-header__text-btn"
              onClick={() => dispatch({ type: 'COLLAPSE_ALL' })}
              title="Collapse all outputs"
              aria-label="Collapse all outputs"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M4 10l4-4 4 4z"/></svg>
              Collapse
            </button>
            <button
              className="nb-header__text-btn"
              onClick={() => dispatch({ type: 'EXPAND_ALL' })}
              title="Expand all outputs"
              aria-label="Expand all outputs"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M4 6l4 4 4-4z"/></svg>
              Expand
            </button>
          </>
        )}
      </div>

      {/* Parse errors */}
      {parseErrors.length > 0 && (
        <div className="nb-error-banner">
          {parseErrors.map((err, i) => <div key={i}>{err}</div>)}
        </div>
      )}

      {/* Cell list */}
      <div className="nb-editor__cells" role="list">
        {/* Insert bar above first cell */}
        <CellInsertBar onInsert={(type) => dispatch({ type: 'INSERT_CELL', index: 0, cellType: type })} />

        {cells.map((cell, i) => (
          <div key={cell.id}>
            {isCellVisible(cell.id) ? (
              <div id={`nb-cell-${cell.id}`} role="listitem" className="nb-cell-wrapper" aria-selected={cell.id === state.selectedCellId}>
                <NotebookCell
                  cell={cell}
                  language={metadata.language}
                  isSelected={cell.id === state.selectedCellId}
                  isEditing={cell.id === state.editingCellId}
                  outputCollapsed={state.collapsedOutputs.has(cell.id)}
                  showLineNumbers={state.showLineNumbers}
                  onSelect={() => dispatch({ type: 'SELECT_CELL', cellId: cell.id })}
                  onEnterEdit={() => dispatch({ type: 'ENTER_EDIT_MODE', cellId: cell.id })}
                  onToggleOutputCollapse={() => dispatch({ type: 'TOGGLE_OUTPUT_COLLAPSE', cellId: cell.id })}
                />
                <NotebookToolbar
                  cellId={cell.id}
                  cellType={cell.type}
                  onDelete={() => dispatch({ type: 'DELETE_CELL', cellId: cell.id })}
                  onChangeType={(t) => dispatch({ type: 'CHANGE_CELL_TYPE', cellId: cell.id, newType: t })}
                />
              </div>
            ) : (
              <div
                className="nb-cell nb-cell--placeholder"
                data-cell-id={cell.id}
                ref={placeholderRef}
                style={{ height: Math.max(38, (cell.source.split('\n').length * 19) + 24) }}
              />
            )}
            {/* Insert bar below each cell */}
            <CellInsertBar onInsert={(type) => dispatch({ type: 'INSERT_CELL', index: i + 1, cellType: type })} />
          </div>
        ))}
      </div>

      {/* Empty state */}
      {cells.length === 0 && parseErrors.length === 0 && (
        <div className="nb-editor__empty">This notebook has no cells.</div>
      )}

      {/* Hidden Monaco editor container — moved between cells via appendChild */}
      <div
        ref={editorContainerRef}
        className="nb-editor__monaco-container"
        style={{ display: 'none', minHeight: 19 }}
      />
    </div>
  )
}
