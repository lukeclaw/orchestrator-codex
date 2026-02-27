---
title: "File Explorer Panel — Design Document"
author: Yudong Qiu
created: 2026-02-26
last_modified: 2026-02-26
status: Phase 1 Implemented
version: 0.4
---

# File Explorer Panel — Design Document

## Problem

When a Claude Code worker is running on a task, the user has no visibility into what files exist or have been modified in the worker's working directory. The only way to see the filesystem is to interact with the terminal directly — typing `ls`, `find`, or `tree` commands — which interrupts the worker's flow and is tedious for quick file inspections.

This is especially painful for:
1. **Remote (rdev) workers**: The working directory lives on a remote machine, so the user can't just open it in their local file explorer or VS Code.
2. **Reviewing worker output**: After a worker completes a task, the user wants to quickly scan what files were created or modified without reading through the entire terminal history.
3. **Multi-worker workflows**: When orchestrating 5-10 workers in parallel, quickly checking each worker's file state is critical for situational awareness.

## Goals

- Provide a VS Code-quality file browsing experience embedded in the SessionDetailPage
- Support both local and remote (rdev/SSH) working directories transparently
- Non-intrusive: collapsed by default, does not change the existing terminal-only experience
- Fast: lazy-loaded directory expansion, not full recursive scan
- Read-only: browse and view file contents (code + rendered markdown), not edit them
- Activity-aware: surface what the worker has been touching with VS Code-style git status colors

## Non-Goals

- Full IDE functionality (editing, search-and-replace, integrated git)
- File upload/download (can be added later)
- Real-time file watching / auto-refresh (polling on demand is sufficient for MVP)

---

## UI Design

### Layout: VS Code-Style Three-Pane Split

When activated, the file explorer transforms the terminal area into a three-pane layout that mirrors VS Code: file tree on the left, file content viewer top-right, terminal bottom-right. The terminal **animates** from its full-size position into the bottom-right pane, giving a clear visual cue that the layout is splitting rather than replacing content.

#### Collapsed State (Default)

The terminal occupies the full area exactly as it does today. A floating trigger button sits in the bottom-right corner of the terminal area, above the footer.

```
┌─────────────────────────────────────────────────────────────────────┐
│  sd-topbar: Worker-1  ● working   [⏸] [■] [🗑]                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Terminal (xterm.js) — full width, full height                      │
│                                                                     │
│  $ claude --resume ...                                              │
│                                                                     │
│  I'll start by reading the existing code...                         │
│                                                                     │
│                                                            [📁]     │
├─────────────────────────────────────────────────────────────────────┤
│  sd-footer: [PENP-7] Fix race condition   Auto-reconnect [●] Paste │
└─────────────────────────────────────────────────────────────────────┘
```

The `[📁]` button is a **floating action button** (FAB) positioned in the bottom-right corner of the terminal area:

- **Position**: `position: absolute; bottom: 12px; right: 12px` within the terminal area container
- **Size**: 36px x 36px circle
- **Style**: `--surface-raised` background, `--border` border, `--shadow-md` shadow. Folder icon in `--text-secondary`
- **Hover**: Background transitions to `--surface-hover`, icon to `--text-primary`, shadow lifts to `--shadow-lg`
- **Tooltip**: "Open file explorer (Ctrl+Shift+E)"
- **Opacity**: 70% idle, 100% on hover — subtle enough to not distract from terminal output
- **When `work_dir` is null**: FAB is hidden entirely

Clicking the FAB or pressing `Ctrl+Shift+E` triggers the opening animation.

#### Opening Animation — Terminal Shrinks to Bottom-Right

When the file explorer opens, the terminal **animates** from full-size to the bottom-right pane. This creates a spatial metaphor: the terminal "makes room" for the file tree and viewer rather than being replaced.

**Animation sequence** (300ms total, `ease-out` curve):

```
Frame 0 (0ms)                         Frame 1 (300ms)
┌─────────────────────────────┐       ┌──────────┬──────────────────┐
│                             │       │ EXPLORER │  File Viewer     │
│                             │       │          │                  │
│      Terminal (100%)        │  ──►  │  ▼ src/  │                  │
│                             │       │    app.py├──────────────────┤
│                             │       │    ...   │  Terminal (shrank│
│                        [📁] │       │          │  to bottom-right)│
└─────────────────────────────┘       └──────────┴──────────────────┘
```

**CSS implementation:**

```css
.fe-content-area {
  display: flex;
  height: 100%;
}

/* File tree panel — slides in from left */
.fe-panel {
  width: 0;
  opacity: 0;
  overflow: hidden;
  transition: width 300ms ease-out, opacity 200ms ease-out;
}
.fe-panel.open {
  width: var(--fe-panel-width, 240px);  /* from localStorage or default */
  opacity: 1;
}

/* Right pane — the terminal's new container */
.fe-right-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* File viewer — slides down from top */
.fe-viewer {
  height: 0;
  opacity: 0;
  overflow: hidden;
  transition: height 300ms ease-out, opacity 200ms ease-out 100ms;
}
.fe-viewer.open {
  height: var(--fe-viewer-height, 50%);
  opacity: 1;
}

/* Terminal — smoothly resizes during transitions */
.sd-terminal-area {
  flex: 1;
  min-height: 120px;
  transition: flex 300ms ease-out;
}
```

**Key animation behaviors:**

1. **Opening**: File tree slides in from the left (width 0 → 240px). If a file is selected, the viewer slides down from the top simultaneously. The terminal smoothly shrinks to fill the remaining bottom-right space.
2. **Closing**: Reverse — panels collapse, terminal smoothly expands back to full size. The FAB fades back in after the animation completes.
3. **Opening a file** (viewer appears): The viewer pane grows from height 0 to 50%, pushing the terminal down. The terminal height animates smoothly.
4. **Closing the viewer** (✕ on tab): Viewer collapses to height 0, terminal smoothly expands upward to fill the right pane.
5. **During animation**: Resize handles are disabled. xterm.js `fit()` is called once after animation completes (via `transitionend` event) to avoid expensive re-fitting on every frame.

**Closing animation** reverses to the FAB:

The FAB serves as the visual anchor. When closing, the panels collapse and the terminal expands. Once the animation completes, the FAB fades in at its bottom-right position (200ms fade-in, starting after the 300ms collapse).

#### Expanded State — Full Three-Pane Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  sd-topbar: Worker-1  ● working   [⏸] [■] [🗑]                     │
├───────────────┬─────────────────────────────────────────────────────┤
│ EXPLORER   [↻]│  src/api/app.py                     [◧] [✕]  tab  │
│ …/my-project  │─────────────────────────────────────────────────────│
│───────────────│  1  #!/usr/bin/env python3                          │
│ ▼ src/        │  2  from fastapi import FastAPI                     │
│   ▼ api/      │  3                                                  │
│    M app.py   │  4  app = FastAPI()                                 │
│      routes.py│  5                                                  │
│   ▼ state/    │  6  @app.get("/health")                             │
│    M models.py│  7  def health():                                   │
│   main.py     │  8      return {"status": "ok"}                     │
│ ▼ tests/      │                                                     │
│  U test_new.py│─────────────────────────────────────────────────────│
│ README.md     │  Terminal (xterm.js)                                 │
│ pyproject.toml│                                                     │
│               │  $ claude --resume ...                               │
│  ─────────────│  I'll fix the race condition in the handler...      │
│  CHANGED FILES│                                                     │
│  M app.py     │  > Edit src/api/app.py                              │
│  M models.py  │                                                     │
│  U test_new.py│                                            [📁 ✕]   │
├───────────────┴─────────────────────────────────────────────────────┤
│  sd-footer: [PENP-7] Fix race condition   Auto-reconnect [●] Paste │
└─────────────────────────────────────────────────────────────────────┘
```

Note: The `[📁 ✕]` button in the bottom-right of the terminal area is the close trigger — same position as the open FAB, now showing a close icon. This gives spatial consistency: the toggle always lives at the bottom-right of the terminal.

The three panes are:

| Pane | Position | Purpose |
|------|----------|---------|
| **File Tree** | Left | Browse project structure, see changed files with git status colors |
| **File Viewer** | Top-right | Read-only code viewer / rendered markdown preview |
| **Terminal** | Bottom-right | Existing xterm.js terminal, unchanged |

#### Tree-Only State (No File Selected)

Before any file is clicked, the right side remains a single pane (terminal only). The file viewer pane animates in only when a file is selected:

```
├───────────────┬─────────────────────────────────────────────────────┤
│ EXPLORER   [↻]│                                                     │
│ …/my-project  │  Terminal (xterm.js) — full height                  │
│───────────────│                                                     │
│ ▼ src/        │  $ claude --resume ...                              │
│   ▼ api/      │                                                     │
│    M app.py   │  I'll fix the race condition in the handler...      │
│      routes.py│                                            [📁 ✕]   │
├───────────────┴─────────────────────────────────────────────────────┤
```

This progressive disclosure avoids overwhelming the user. The three-pane layout is revealed only when needed.

### Pane Sizing and Resize Handles

All three panes are resizable via drag handles that follow the existing Brain Panel resize handle pattern (4px strip, `col-resize` / `row-resize` cursor, accent color on hover).

| Dimension | Default | Min | Max | Persisted |
|-----------|---------|-----|-----|-----------|
| File tree width | 240px | 180px | 400px | localStorage |
| File viewer height | 50% of right pane | 120px | right pane height - 120px | localStorage |

**Minimum terminal constraint**: The terminal pane must always maintain at least 500px width and 120px height. Resize handles clamp dynamically based on the available viewport to enforce this. For example, if the viewport's content area is 900px wide, the file tree max is clamped to 400px (900 - 500).

**During resize**: Transitions are disabled (via a `.resizing` class) so panels track the mouse exactly. Transitions re-enable after `mouseup`.

---

## File Tree Panel

### Panel Header

```
┌──────────────────────────┐
│ EXPLORER         [🔍] [↻] │
│ …/codes/my-project        │
└──────────────────────────┘
```

- **Title**: "EXPLORER" (matches VS Code naming)
- **Filter button** (🔍): Toggles the inline filter input (see Filter section below)
- **Refresh button** (↻): Re-fetches all currently expanded directories, preserving expanded/collapsed state
- **Path subtitle**: Shows the `work_dir` path, truncated with ellipsis from the left if too long (e.g., `…/codes/my-project`)

### Tree View Modes

The file tree panel has two view modes, switchable via tabs at the bottom of the panel:

#### Files View (default)

Standard hierarchical file tree showing the full directory structure. Files with git modifications are shown in their status color (see Git Status Colors below).

#### Changed Files View

A flat list showing only files modified during this session, styled exactly like VS Code's Source Control sidebar. Files are grouped by status and displayed with their relative paths.

```
┌──────────────────────────┐
│  CHANGED FILES        3  │
│──────────────────────────│
│  M  src/api/app.py       │   ← gold text (#E2C08D)
│  M  src/state/models.py  │   ← gold text
│  U  tests/test_new.py    │   ← green text (#73C991)
└──────────────────────────┘
```

Each file row shows:
- **Status badge letter** (M, A, D, U, R) in the corresponding VS Code git color
- **Full filename text** also colored by status (matching VS Code behavior — the entire filename is tinted)
- **Relative path** in muted text when the filename alone is ambiguous
- On hover: inline action to open the file in the viewer

Detection methods:
1. **mtime comparison**: Files with `mtime` within the session's duration
2. **Terminal output parsing** (Phase 2): Parse Claude's `> Edit`, `> Write`, `> Read` tool-use output from the terminal to build a list of touched files
3. **`git status --porcelain`** (Phase 2): Accurate status from git itself

Clicking a file in this view opens it in the file viewer, same as the tree view.

### Git Status Colors

File tree decorations follow VS Code's exact git color scheme. Both the **filename text color** and a **badge letter** to the right of the filename are used, matching how VS Code renders git status in its explorer.

#### Color Definitions (CSS Variables)

```css
/* Git decoration colors — matches VS Code Dark+ defaults */
--git-modified:    #E2C08D;  /* warm gold/tan */
--git-added:       #81b88b;  /* muted green (staged add) */
--git-untracked:   #73C991;  /* bright green */
--git-deleted:     #c74e39;  /* red */
--git-renamed:     #73C991;  /* bright green (same as untracked) */
--git-conflicting: #e4676b;  /* pinkish red */
--git-ignored:     #8C8C8C;  /* gray */
--git-submodule:   #8db9e2;  /* light blue */
```

#### How Colors Are Applied

| Git Status | Badge | Text Color | Behavior |
|---|---|---|---|
| Modified | `M` | `--git-modified` (#E2C08D) | Entire filename turns warm gold |
| Added (staged) | `A` | `--git-added` (#81b88b) | Muted green |
| Untracked (new) | `U` | `--git-untracked` (#73C991) | Bright green |
| Deleted | `D` | `--git-deleted` (#c74e39) | Red, with ~~strikethrough~~ on filename |
| Renamed | `R` | `--git-renamed` (#73C991) | Bright green |
| Conflicting | `!` | `--git-conflicting` (#e4676b) | Pinkish red, distinct from deleted |
| Ignored | `I` | `--git-ignored` (#8C8C8C) | Gray, 50% opacity on entire row |
| Submodule | `S` | `--git-submodule` (#8db9e2) | Soft blue |

#### Parent Directory Propagation

Following VS Code's behavior: git status colors **propagate upward** to parent directories for all statuses except deleted files. If `src/api/app.py` is modified, both `src/` and `src/api/` show the modified gold color. When children have mixed statuses, the highest-severity color wins (conflict > deleted > modified > untracked > added).

```
  ▼ src/                  ← gold (propagated from modified child)
    ▼ api/                ← gold (propagated)
      M app.py     2.1 KB ← gold text + "M" badge
        routes.py  890 B  ← normal (no changes)
    ▼ state/              ← gold (propagated)
      M models.py  1.4 KB ← gold text + "M" badge
  ▼ tests/                ← green (propagated from untracked child)
    U test_new.py  340 B  ← green text + "U" badge
```

The badge letter only appears on the file itself, not on parent directories. Parent directories show only the propagated text color.

### File Tree Rendering

The tree follows VS Code conventions:

| Element | Visual | Behavior |
|---|---|---|
| Directory (collapsed) | `▶ dirname/` | Click to expand (lazy fetch). Chevron animates rotation |
| Directory (expanded) | `▼ dirname/` | Click to collapse. Chevron animates rotation |
| File | `  filename.ext` | Single-click opens in preview (italic tab). Double-click pins the tab |
| File (selected) | `  filename.ext` (highlighted row) | `--list-active-selection` background (#04395E) |
| File (hovered) | `  filename.ext` (hover row) | `--list-hover` background (#2A2D2E) |
| Symlink | `  name → target` | Italic text, shows link target |
| Gitignored entries | `node_modules/`, `__pycache__/` | Hidden by default (toggle to show) |
| Loading | `▶ dirname/ ⟳` | Spinner while fetching children |
| Empty dir | `  (empty)` | Italic, muted text |
| Error | `  ⚠ Failed to load` | Red text (`--git-deleted`) with retry on click |

**Chevron animation**: The expand/collapse arrow uses a CSS `transform: rotate()` transition (150ms) — `rotate(0deg)` when collapsed, `rotate(90deg)` when expanded. This matches VS Code's codicon chevron behavior.

**File metadata on each row** (right-aligned, muted text):

```
  ▼ src/
    M app.py              2.1 KB   2m ago
      routes.py           890 B
    ▼ state/
      M models.py         1.4 KB   1m ago
```

- **Size**: Human-readable (B, KB, MB), always shown for files
- **Recency indicator**: Relative time shown for files modified within the last 10 minutes, colored with `--accent` to draw attention. Tooltip shows absolute timestamp
- **Git badge**: Status letter (M, A, U, D, R, !) in the corresponding color, right-aligned before the size

**Indent guides**: Solid 1px vertical lines at each indent level, using `--border-subtle` color (#21262d). 8px indentation per level (matching VS Code's `workbench.tree.indent` default). Lines connect parent to children visually.

### File Icons

Simple SVG icons by extension, consistent with VS Code's Seti icon theme:

| Extension | Icon |
|---|---|
| `.py` | Python logo (simplified) |
| `.ts`, `.tsx` | TypeScript T |
| `.js`, `.jsx` | JavaScript J |
| `.json` | Braces `{}` |
| `.md` | M document |
| `.css` | # style |
| `.html` | `<>` |
| `.yaml`, `.yml` | Gear |
| `.sql` | Database cylinder |
| `.sh` | Terminal `$_` |
| `.toml` | Gear (variant) |
| `.lock` | Lock |
| `.env` | Key |
| Directory (collapsed) | Folder icon (outline) |
| Directory (expanded) | Folder icon (filled/open) |
| Default | Generic document |

### Gitignore-Aware Filtering

By default, entries matching common noise patterns are hidden:
- `.git/`, `__pycache__/`, `node_modules/`, `.venv/`, `.ruff_cache/`, `.mypy_cache/`
- `*.pyc`, `.DS_Store`

A small toggle in the panel header ("Show ignored") reveals them in gray text (`--git-ignored`, #8C8C8C) at 50% row opacity. The list of ignored patterns is read from `.gitignore` if available via the file listing API, or falls back to the hardcoded defaults above.

### Inline Filter (Search)

When the filter button is clicked or the user presses `/` while the tree is focused, a filter input appears below the header:

```
┌──────────────────────────┐
│ EXPLORER         [🔍] [↻] │
│ ┌────────────────────┐   │
│ │ Filter files...  ✕ │   │
│ └────────────────────┘   │
│ ▼ src/api/               │
│     app.py               │   ← matches "app"
│ ▼ src/state/             │
│     app_state.py         │   ← matches "app"
│──────────────────────────│
```

- Client-side only — filters the already-fetched tree nodes
- Matches against file/directory names (case-insensitive substring)
- Matching text within filenames is highlighted with `--list-highlight` color (#2AAAFF), matching VS Code's filter highlight
- Auto-expands directories that contain matches
- Dims non-matching entries rather than hiding them (so the user retains spatial context)
- `Escape` or `✕` clears the filter and restores the full tree

### Context Menu

Right-click on any file or directory, or click the `⋯` icon that appears on row hover:

| Action | Files | Directories |
|--------|-------|-------------|
| **Copy path** | Full path to clipboard | Full path to clipboard |
| **Copy relative path** | Relative to work_dir | Relative to work_dir |
| **Refresh** | — | Re-fetch this directory |
| **Collapse all** | — | Collapse all expanded children |

A brief "Copied!" toast (1.5s, using the existing NotificationToast system) confirms clipboard actions.

---

## File Viewer Pane

The file viewer is a read-only pane that appears in the top-right when a file is selected. It supports two modes: **source view** (syntax-highlighted code) and **rendered preview** (for markdown files). This mirrors VS Code's built-in markdown preview.

### Viewer Header (Tab Bar)

```
┌─────────────────────────────────────────────────────────────────┐
│  src/api/app.py                          2.1 KB   [◧]    [✕]   │
└─────────────────────────────────────────────────────────────────┘
```

For markdown files:

```
┌─────────────────────────────────────────────────────────────────┐
│  README.md                               4.3 KB  [<>|◧]  [✕]  │
└─────────────────────────────────────────────────────────────────┘
```

- **File path**: Relative path from work_dir, truncated from left with ellipsis if needed
- **File size**: Human-readable
- **Preview/Source toggle** (`[<>|◧]`): Only shown for markdown files. Two-segment toggle button:
  - `<>` — source view (raw markdown with syntax highlighting)
  - `◧` — rendered preview (default for `.md` files)
- **Close button** (✕): Closes the viewer pane. Terminal animates upward to fill the right pane.

**Preview tab behavior** (matches VS Code):
- **Single-click** a file in the tree → opens as a **preview tab** (filename in *italic*). Clicking another file replaces the preview.
- **Double-click** a file in the tree → **pins** the tab (filename in regular weight). Pinned tabs persist when clicking other files.
- Only one preview tab exists at a time. Pinned tabs are remembered until explicitly closed.

### Source View (Code Files)

- **Syntax highlighting**: Use `highlight.js` with dynamic imports (load only the grammar for the current file's language). Falls back to plaintext.
- **Line numbers**: Always shown, using `--text-muted` color, in a fixed-width gutter
- **Font**: Monospace (`--font-mono`), same size as terminal
- **Scrollable**: Both vertical and horizontal overflow scroll
- **Max lines**: Truncate at 500 lines with a "File truncated — showing first 500 of N lines" banner at the bottom
- **Binary files**: Show a centered message "Binary file — preview not available" with the file size
- **Empty files**: Show "(empty file)" in muted italic text
- **Large files** (> 1MB): Show a warning banner "Large file (X MB) — loading may be slow" before fetching, with a "Load anyway" button. Files > 5MB are refused with "File too large to preview."

### Markdown Preview (Rendered View)

When a `.md` file is opened, the viewer defaults to **rendered preview mode**. The styling follows VS Code's built-in markdown preview:

**Typography and layout:**

```css
.fe-md-preview {
  font-family: var(--font-sans);  /* system sans-serif, not monospace */
  font-size: 14px;
  line-height: 22px;
  padding: 26px 26px 1em;
  color: var(--text-primary);
  overflow-y: auto;
}
```

**Element rendering:**

| Element | Style |
|---|---|
| **h1** | 2em, font-weight 600, bottom border (`--border`), 0.3em padding-bottom |
| **h2** | 1.5em, font-weight 600, bottom border, 0.3em padding-bottom |
| **h3** | 1.25em, font-weight 600 |
| **h4–h6** | Scaling down from 1em, font-weight 600 |
| **Paragraphs** | 16px bottom margin |
| **Code (inline)** | Monospace font, `--surface-raised` background, 3px padding, 3px border-radius |
| **Code blocks** | Monospace font, `--surface-raised` background, 16px padding, 3px border-radius, 1px `--border` border, syntax highlighted, horizontal scroll |
| **Links** | `--accent` color, underline on hover only |
| **Blockquotes** | 5px left border (`--accent`), 10px left padding, `--text-secondary` color |
| **Lists** | Standard bullets/numbers, 0.7em bottom margin, nested lists collapse margin |
| **Tables** | Collapsed borders (`--border`), cell padding 5px 10px, alternating row background |
| **Images** | `max-width: 100%; max-height: 80vh`, centered, rounded corners |
| **Horizontal rules** | 1px solid `--border` |
| **Task lists** | Rendered checkboxes (read-only) with `☐` / `☑` |

**Markdown rendering library**: Use `react-markdown` with `remark-gfm` (GitHub Flavored Markdown — tables, task lists, strikethrough, autolinks) and `rehype-highlight` for code block syntax highlighting. This is lightweight (~50KB gzipped) and handles the full GFM spec.

**Security**: All HTML in markdown is sanitized via `rehype-sanitize` to prevent XSS. No raw HTML passthrough.

### Viewer Loading State

While fetching file content, show a skeleton with animated lines (similar to the tree loading state) at the correct viewer dimensions. This prevents layout jumps.

### Viewer Keyboard

| Shortcut | Action |
|---|---|
| `Escape` | Close viewer, return focus to terminal |
| `Ctrl+C` | Copy selected text (standard browser behavior) |
| `Ctrl+Shift+V` | Toggle markdown preview (when viewing .md file) — matches VS Code shortcut |

---

## Architecture

### Backend API

Two endpoints on the sessions router:

#### `GET /api/sessions/{id}/files`

Lists the contents of a directory within the session's working directory.

**Query parameters:**
- `path` (string, default `"."`) — relative path from `work_dir` to list
- `depth` (int, default `1`) — how many levels deep to fetch (1 = immediate children only)
- `show_ignored` (bool, default `false`) — include gitignored entries

**Response:**
```json
{
  "work_dir": "/home/user/my-project",
  "path": "src",
  "entries": [
    {
      "name": "api",
      "type": "directory",
      "children_count": 5,
      "mtime": "2026-02-26T10:30:00Z"
    },
    {
      "name": "main.py",
      "type": "file",
      "size": 2048,
      "mtime": "2026-02-26T10:30:00Z",
      "git_status": "modified"
    },
    {
      "name": "config.yaml",
      "type": "file",
      "size": 512,
      "mtime": "2026-02-26T09:15:00Z",
      "symlink_target": "../shared/config.yaml"
    }
  ],
  "git_available": true
}
```

New fields:
- `git_status` (string, optional): One of `modified`, `added`, `untracked`, `deleted`, `renamed`, `conflicting`, `ignored`, `submodule`. Absent if the file has no git changes or git is not available.
- `git_available` (bool): Whether git status data was successfully fetched. When `false`, the frontend falls back to mtime-based recency indicators.

**Git status implementation**: Run `git status --porcelain=v1 -z` once per directory listing request, cache the output for 5 seconds (keyed by session + work_dir). Parse the porcelain output to map file paths to statuses. The `-z` flag uses NUL separators for reliable parsing of filenames with spaces.

**Sorting**: Directories first, then files. Both sorted alphabetically (case-insensitive). Dotfiles sorted among their peers (not grouped separately).

**Implementation — local sessions**: Direct `os.scandir()` on the host filesystem.

**Implementation — remote sessions**: Execute a Python script over SSH via stdin piping to avoid shell injection:

```python
import subprocess, json

# The script to run on the remote machine — passed via stdin, NOT interpolated into a shell command
LISTING_SCRIPT = '''
import os, json, sys, subprocess

target = sys.argv[1]

# Try to get git status
git_status = {}
git_available = False
try:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        capture_output=True, text=True, cwd=target, timeout=5
    )
    if result.returncode == 0:
        git_available = True
        for entry in result.stdout.split("\\0"):
            if len(entry) >= 4:
                status_code = entry[:2].strip()
                filepath = entry[3:]
                status_map = {
                    "M": "modified", "A": "added", "D": "deleted",
                    "R": "renamed", "C": "copied", "U": "conflicting",
                    "?": "untracked", "!": "ignored",
                }
                git_status[filepath] = status_map.get(status_code[0], "modified")
except Exception:
    pass

entries = []
for e in os.scandir(target):
    st = e.stat(follow_symlinks=False)
    entry = {"name": e.name, "type": "directory" if e.is_dir() else "file"}
    if e.is_file():
        entry["size"] = st.st_size
    entry["mtime"] = st.st_mtime
    if e.is_symlink():
        entry["symlink_target"] = os.readlink(e.path)
    if e.is_dir():
        try:
            entry["children_count"] = len(os.listdir(e.path))
        except PermissionError:
            entry["children_count"] = 0
    # Attach git status if available
    rel_path = e.name
    if rel_path in git_status:
        entry["git_status"] = git_status[rel_path]
    elif e.is_dir():
        # Check if any child path starts with this directory name
        for gpath, gstatus in git_status.items():
            if gpath.startswith(rel_path + "/"):
                entry["git_status"] = gstatus
                break
    entries.append(entry)
entries.sort(key=lambda e: (e["type"] != "directory", e["name"].lower()))
print(json.dumps({"entries": entries, "git_available": git_available}))
'''

def list_remote_dir(ssh_target: str, remote_path: str) -> dict:
    """List directory contents on a remote machine via SSH.

    The listing script is piped via stdin to avoid any shell injection risk —
    the remote_path is passed as a command-line argument to python3, never
    interpolated into a shell string.
    """
    result = subprocess.run(
        ["ssh", ssh_target, "python3", "-", remote_path],
        input=LISTING_SCRIPT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Remote listing failed: {result.stderr}")
    return json.loads(result.stdout)
```

This eliminates the shell injection risk entirely: the Python script is passed via stdin, and the path is passed as an argument to the Python interpreter (not interpolated into any shell command). If Python is unavailable on the remote, fall back to SFTP via `asyncssh` which has a native `listdir` API.

**Security:**
- Path traversal protection: reject any `path` containing `..` or starting with `/`
- All paths are resolved relative to `work_dir` — cannot escape the working directory
- Remote commands use stdin piping, never shell interpolation
- Read-only: no write/delete/modify operations
- Rate limiting: max 20 requests per session per 10 seconds (429 response if exceeded)

#### `GET /api/sessions/{id}/files/content`

Returns the contents of a single file for the viewer pane.

**Query parameters:**
- `path` (string, required) — relative path from `work_dir`
- `max_lines` (int, default `500`) — truncate large files
- `encoding` (string, default `"utf-8"`) — for binary detection

**Response:**
```json
{
  "path": "src/main.py",
  "content": "#!/usr/bin/env python3\nimport ...",
  "truncated": false,
  "total_lines": 127,
  "size": 2048,
  "binary": false,
  "language": "python"
}
```

- `language` is inferred from the file extension for syntax highlighting hints
- For markdown files, `language` is `"markdown"` — the frontend uses this to enable the preview toggle
- For binary files, returns `binary: true` and no `content`
- Same security constraints as the listing endpoint (path traversal, rate limiting)

**Implementation — remote sessions**: Same stdin-piping pattern. The script reads the file and returns JSON with content, binary detection (check for null bytes in first 8KB), and line count.

### Smart Pre-Fetching

When expanding a directory with `children_count <= 5`, the frontend requests `depth=2` instead of `depth=1`. This eliminates the loading spinner for common shallow directories and makes navigation feel instant.

### Frontend Components

#### Component Hierarchy

```
SessionDetailPage
├── sd-topbar (existing)
├── sd-content-area (new flex container, replaces direct terminal mount)
│   ├── FileExplorerPanel (new, conditional, animated width)
│   │   ├── FileExplorerHeader
│   │   ├── FilterInput (conditional)
│   │   ├── FileTree
│   │   │   └── FileTreeNode (recursive)
│   │   └── ViewModeTabs ("Files" | "Changed")
│   ├── fe-resize-handle-vertical (drag to resize tree width)
│   ├── sd-right-pane (flex column, flex: 1)
│   │   ├── FileViewer (conditional, animated height)
│   │   │   ├── FileViewerHeader (tab bar + preview toggle)
│   │   │   ├── CodeView (syntax-highlighted source)
│   │   │   └── MarkdownPreview (rendered markdown)
│   │   ├── fe-resize-handle-horizontal (drag to resize viewer/terminal split)
│   │   └── sd-terminal-area (existing, flex: 1, animated)
│   │       └── FloatingToggle (FAB — bottom-right, conditional)
├── sd-footer (existing)
```

When collapsed: `sd-content-area` renders only `sd-terminal-area` at full size with the FAB overlay — identical to current behavior plus the floating button.

#### `FileExplorerPanel` Component

```typescript
interface FileExplorerPanelProps {
  sessionId: string
  workDir: string | null
  isRemote: boolean
}

// State
const [width, setWidth] = useState(() =>
  parseInt(localStorage.getItem('fe-width') || '240')
)
const [tree, setTree] = useState<TreeNode[]>([])
const [selectedFile, setSelectedFile] = useState<string | null>(null)
const [pinnedFile, setPinnedFile] = useState<string | null>(null)
const [viewMode, setViewMode] = useState<'files' | 'changed'>('files')
const [filterText, setFilterText] = useState('')
const [showIgnored, setShowIgnored] = useState(false)
```

#### `FileViewer` Component

```typescript
interface FileViewerProps {
  sessionId: string
  filePath: string       // relative path from work_dir
  isPinned: boolean      // false = preview tab (italic), true = pinned tab
  onClose: () => void
  onPin: () => void      // called on double-click or edit action
}

// State
const [content, setContent] = useState<string | null>(null)
const [language, setLanguage] = useState<string>('plaintext')
const [loading, setLoading] = useState(true)
const [error, setError] = useState<string | null>(null)
const [truncated, setTruncated] = useState(false)
const [totalLines, setTotalLines] = useState(0)
const [viewAs, setViewAs] = useState<'source' | 'preview'>(() =>
  language === 'markdown' ? 'preview' : 'source'
)
```

#### `MarkdownPreview` Component

```typescript
interface MarkdownPreviewProps {
  content: string
}

// Uses react-markdown + remark-gfm + rehype-highlight + rehype-sanitize
// Renders into a scrollable container with .fe-md-preview styles
```

#### `FileTreeNode` Component

Recursive tree node — renders a single file or directory.

```typescript
interface TreeNode {
  name: string
  type: 'file' | 'directory'
  path: string              // relative path from work_dir
  size?: number
  mtime?: string
  symlink_target?: string
  children_count?: number
  children?: TreeNode[]     // populated on expand
  expanded?: boolean
  loading?: boolean
  error?: string
  git_status?: GitStatus    // decoration color + badge
}

type GitStatus =
  | 'modified'    // #E2C08D — gold
  | 'added'       // #81b88b — muted green
  | 'untracked'   // #73C991 — bright green
  | 'deleted'     // #c74e39 — red
  | 'renamed'     // #73C991 — bright green
  | 'conflicting' // #e4676b — pinkish red
  | 'ignored'     // #8C8C8C — gray
  | 'submodule'   // #8db9e2 — light blue
```

**Expand behavior**: When a directory node is clicked, if `children` is undefined, fetch from API with `path=node.path` and `depth=1` (or `depth=2` if `children_count <= 5`). Populate `children`, set `expanded=true`. Subsequent collapses/expands reuse the cached children. Refresh re-fetches only currently expanded directories, preserving the expanded/collapsed state.

#### CSS Structure

New classes prefixed with `fe-` (file explorer) following the existing BEM-like convention:

```css
/* Git status colors */
:root {
  --git-modified: #E2C08D;
  --git-added: #81b88b;
  --git-untracked: #73C991;
  --git-deleted: #c74e39;
  --git-renamed: #73C991;
  --git-conflicting: #e4676b;
  --git-ignored: #8C8C8C;
  --git-submodule: #8db9e2;
}

/* Layout */
.fe-content-area { }              /* Outer flex container (row) */
.fe-right-pane { }                /* Right column: viewer + terminal (column flex) */
.fe-resize-v { }                  /* Vertical resize handle (tree ↔ right pane) */
.fe-resize-h { }                  /* Horizontal resize handle (viewer ↔ terminal) */

/* Floating toggle button */
.fe-fab { }                       /* Floating action button (bottom-right) */
.fe-fab--open { }                 /* Close variant when panel is open */

/* File Tree Panel — animated */
.fe-panel { }                     /* Tree panel container, width transitions */
.fe-panel.open { }                /* Expanded state */
.fe-header { }
.fe-path { }
.fe-filter { }
.fe-filter-input { }
.fe-tree { }                      /* Scrollable tree container */
.fe-view-tabs { }                 /* Files / Changed tab bar at bottom */

/* Tree Nodes */
.fe-node { }
.fe-node--directory { }
.fe-node--file { }
.fe-node--selected { }            /* background: #04395E (VS Code active selection) */
.fe-node--hovered { }             /* background: #2A2D2E (VS Code hover) */
.fe-node--dimmed { }              /* Gitignored: gray text, 50% opacity */
.fe-node--filtered-out { }
.fe-node--git-modified { }        /* color: var(--git-modified) */
.fe-node--git-added { }           /* color: var(--git-added) */
.fe-node--git-untracked { }       /* color: var(--git-untracked) */
.fe-node--git-deleted { }         /* color: var(--git-deleted); text-decoration: line-through */
.fe-node--git-conflicting { }     /* color: var(--git-conflicting) */
.fe-node__indent-guide { }        /* 1px solid var(--border-subtle), 8px per level */
.fe-node__chevron { }             /* Animated rotation (150ms) */
.fe-node__icon { }
.fe-node__name { }                /* Inherits color from git status modifier */
.fe-node__badge { }               /* Right-aligned: M, A, U, D, R, ! in git color */
.fe-node__meta { }                /* Right-aligned: size, recency */
.fe-node__kebab { }               /* ⋯ menu button on hover */

/* File Viewer — animated */
.fe-viewer { }                    /* Height transitions */
.fe-viewer.open { }
.fe-viewer__header { }            /* Tab bar with filename + preview toggle + close */
.fe-viewer__tab { }               /* Tab label */
.fe-viewer__tab--preview { }      /* Italic filename for preview tabs */
.fe-viewer__tab--pinned { }       /* Regular weight for pinned tabs */
.fe-viewer__toggle { }            /* Source/Preview toggle for markdown */
.fe-viewer__content { }           /* Source code view */
.fe-viewer__line-numbers { }
.fe-viewer__truncated-banner { }
.fe-viewer__empty { }

/* Markdown preview */
.fe-md-preview { }                /* Rendered markdown container */
.fe-md-preview h1 { }             /* 2em, border-bottom */
.fe-md-preview h2 { }             /* 1.5em, border-bottom */
.fe-md-preview code { }           /* Inline code */
.fe-md-preview pre { }            /* Code blocks, syntax highlighted */
.fe-md-preview blockquote { }     /* Left border accent */
.fe-md-preview table { }          /* Collapsed borders */
.fe-md-preview img { }            /* max-width: 100% */
.fe-md-preview a { }              /* --accent color */

/* Context Menu */
.fe-context-menu { }
.fe-context-menu__item { }
```

### State Persistence

| Setting | Storage Key | Default |
|---------|-------------|---------|
| Panel open/collapsed | `fe-open` | `false` |
| Panel width | `fe-width` | `240` |
| Viewer height ratio | `fe-viewer-ratio` | `0.5` |
| View mode (files/changed) | `fe-view-mode` | `files` |
| Show ignored files | `fe-show-ignored` | `false` |
| Markdown default view | `fe-md-view` | `preview` |
| Expanded directories | Not persisted | — (too volatile) |

---

## Interaction Details

### Keyboard Shortcuts

| Shortcut | Context | Action |
|---|---|---|
| `Ctrl+Shift+E` | Global (any focus) | Toggle file explorer panel |
| `↑` / `↓` | Tree focused | Navigate tree nodes |
| `Enter` | Tree focused | Expand/collapse dir, or open file in viewer |
| `←` | Tree focused | Collapse current directory / move to parent |
| `→` | Tree focused | Expand current directory |
| `/` | Tree focused | Open inline filter |
| `Escape` | Filter focused | Clear filter, return focus to tree |
| `Escape` | Viewer focused | Close viewer, return focus to terminal |
| `Escape` | Tree focused (no filter) | Close panel, return focus to terminal |
| `Ctrl+Shift+V` | Viewer focused (.md) | Toggle between source and rendered preview |

`Ctrl+Shift+E` is chosen to match VS Code's explorer shortcut and avoid conflicts with terminal keybindings (`Ctrl+B` is tmux prefix, `Ctrl+E` is end-of-line in bash).

### Resize Handles

Two resize handles:

1. **Vertical** (between tree panel and right pane): `col-resize` cursor, drags to resize tree width. Clamped to 180px–400px, further clamped to ensure terminal gets at least 500px.
2. **Horizontal** (between viewer and terminal): `row-resize` cursor, drags to resize the viewer/terminal height split. Each pane must maintain at least 120px. Only present when the viewer is open.

Both handles are 4px wide/tall, transparent by default, accent-colored on hover — matching the existing Brain Panel resize handle. During drag, a `.resizing` class is added to the content area to disable CSS transitions.

### Loading States

| State | Visual |
|---|---|
| **Initial tree load** | Skeleton shimmer: 6 rows of animated placeholder bars at varying widths |
| **Directory expand** | Small spinner icon replaces the chevron; siblings remain interactive |
| **Tree refresh** | Pulse animation on ↻ button; tree stays visible at 60% opacity during fetch |
| **File viewer load** | Skeleton shimmer in the viewer area with faint line-number gutter |
| **Markdown preview load** | Skeleton shimmer with heading-shaped and paragraph-shaped bars |
| **Remote SSH timeout** | After 5s: "Loading... 5s" counter. After 10s: error with auto-retry once, then manual "Retry" link |
| **Error** | Red banner at top of affected pane with "Failed to load — Retry" link |

### Empty States

| State | Visual |
|---|---|
| **No work_dir** | FAB is hidden; no panel rendered |
| **Empty directory** | "(empty)" in italic muted text |
| **No changed files** | "No files changed yet" with muted text and worker-activity icon |
| **Session disconnected** | Tree/viewer show last cached state with amber banner: "Session disconnected — file data may be stale" |
| **No file selected** | Viewer pane hidden; terminal takes full height of right pane |

---

## Accessibility

The file tree implements the [WAI-ARIA TreeView pattern](https://www.w3.org/WAI/ARIA/apg/patterns/treeview/):

| Element | ARIA |
|---|---|
| Tree container | `role="tree"`, `aria-label="File explorer"` |
| Tree node | `role="treeitem"` |
| Directory node | `aria-expanded="true\|false"` |
| Nesting depth | `aria-level={depth}` (1-indexed) |
| Focused node | `aria-selected="true"`, `tabindex="0"` (roving tabindex) |
| Group of children | `role="group"` |
| Loading directory | `aria-busy="true"` |
| Filter input | `aria-label="Filter files"`, linked to tree via `aria-controls` |
| Git status badge | `aria-label="Modified"` (or appropriate status) for screen readers |
| Markdown preview toggle | `role="radiogroup"` with `role="radio"` for each option |

Status announcements via `aria-live="polite"` region:
- "Loaded 12 entries" after directory expansion
- "3 files match filter" when filtering
- "Copied path to clipboard" on copy actions
- "Switched to markdown preview" / "Switched to source view"

Floating toggle button: `aria-pressed="true|false"`, `aria-label="Toggle file explorer"`.

---

## Phased Rollout

### Phase 1: Three-Pane Layout + File Browsing (MVP)

**Backend:**
- `GET /api/sessions/{id}/files` — local sessions only, with `git status` integration
- `GET /api/sessions/{id}/files/content` — local sessions only
- Path traversal protection, rate limiting

**Frontend:**
- Floating toggle button (bottom-right FAB) with `Ctrl+Shift+E`
- Animated three-pane layout: tree slides from left, viewer slides from top, terminal shrinks to bottom-right
- Lazy-loaded directory tree with git status colors (VS Code Dark+ palette)
- File viewer with syntax highlighting (highlight.js, dynamic imports)
- Markdown preview with rendered GFM (react-markdown + remark-gfm)
- Preview/pinned tab behavior (single-click = preview italic, double-click = pin)
- Inline filter (client-side, `/` shortcut, highlighted matches)
- Context menu (copy path, copy relative path, refresh) with ⋯ hover button
- File size and git badge indicators on tree nodes
- Gitignore-aware filtering (hardcoded defaults)
- Keyboard navigation (arrow keys, enter, escape)
- ARIA tree roles and screen reader announcements
- localStorage persistence for all panel dimensions and preferences
- Skeleton loading states, error states, empty states

### Phase 2: Remote Support + Activity Awareness

- Backend: SSH-based file listing and content reading (stdin-piped Python script, SFTP fallback)
- Changed Files view with accurate git status (M, A, U, D, R badges in VS Code colors)
- Parse terminal output for `> Edit`, `> Write`, `> Read` to enhance changed-file tracking
- Auto-scroll tree to the file currently being edited by the worker
- Read `.gitignore` from remote for filtering

### Phase 3: Enhanced Features

- Multi-tab file viewer (open several files, switch between tabs)
- File diff view — show what the worker changed vs the original (red/green inline diff)
- Search across file contents (ripgrep on backend, results in viewer)
- Drag-and-drop file path into terminal input
- File download for remote sessions
- Markdown preview: support mermaid diagrams and LaTeX math (KaTeX)

---

## Edge Cases

| Scenario | Handling |
|---|---|
| `work_dir` is null | Hide FAB, no panel rendered |
| `work_dir` doesn't exist | Show error "Directory not found" in panel body, offer refresh |
| Very large directory (1000+ entries) | Show first 200, "Show N more" button. Cap at 1000 total with warning |
| Deep nesting (20+ levels) | Indent guides prevent visual confusion. No artificial depth cap |
| Permission denied on directory | Show `⚠ Permission denied` for that node, siblings remain accessible |
| Permission denied on file read | Show error in viewer: "Cannot read file — permission denied" |
| Session disconnected during fetch | Show cached data with amber "stale" banner, disable refresh |
| SSH timeout on remote listing | 10s timeout. Auto-retry once after 2s. Then show error with manual retry + elapsed time counter |
| Rapid expand/collapse clicking | Debounce fetch calls (200ms), cancel in-flight requests on collapse via AbortController |
| Race between refresh and expand | Per-node loading state, not global. Concurrent requests are independent |
| Binary file selected | Viewer shows centered "Binary file — preview not available" with file size |
| Very large file selected (>1MB) | Viewer shows warning with "Load anyway" button. >5MB refused entirely |
| Unicode filenames | Full UTF-8 support, render as-is |
| Very long filenames | Truncate with ellipsis, full name in tooltip |
| Viewport too narrow for three panes | If right pane < 500px after tree opens, auto-collapse tree to minimum (180px). Below 700px total, show warning tooltip on FAB |
| Brain Panel + File Explorer both open | They coexist: Brain Panel is app-level right sidebar, File Explorer is inside the session content area. On narrow viewports, opening one auto-collapses the other |
| Git not available in work_dir | `git_available: false` in API response. Fall back to mtime-based recency indicators. No badge letters shown, just the blue dot for recent files |
| Markdown with untrusted content | All HTML sanitized via rehype-sanitize. No script execution, no iframe embedding |
| Markdown with images (relative paths) | Images from remote sessions are not rendered (would require proxying). Show alt text instead with a note "Image not available in remote preview" |
| Animation interrupted by rapid toggling | Use `transitionend` event with a guard flag. If toggled during animation, queue the state change for after the current transition completes |
| xterm.js resize during animation | Call `fit()` only once via `transitionend`, not on every frame. Terminal content may briefly overflow during the 300ms transition — acceptable tradeoff |

---

## Performance Considerations

- **Lazy loading**: Only fetch one directory level at a time (or two for small directories). Never recursively scan.
- **Caching**: Cache directory listings and file contents in component state. Refresh re-fetches only expanded directories, not the entire tree. Git status cache: 5-second TTL on the backend.
- **Debouncing**: Debounce resize events (16ms / one animation frame), refresh actions (200ms), and filter input (150ms).
- **Remote overhead**: SSH roundtrip adds latency. Show loading state immediately. Use smart pre-fetching (depth=2 for small directories) to reduce round trips.
- **Large directories**: Cap at 200 entries per directory with "Show N more" pagination. Sort directories first, then files, both alphabetically (case-insensitive).
- **File viewer**: Syntax highlighting is done on the client via dynamic imports — only the grammar for the current language is loaded. For files > 500 lines, highlight only the visible viewport and use virtual scrolling for the rest.
- **Markdown rendering**: `react-markdown` is lightweight. Code blocks within markdown are syntax-highlighted via `rehype-highlight` (reuses the same highlight.js grammars).
- **Animation performance**: All animated properties (`width`, `height`, `opacity`) use CSS transitions. Panel widths are animated via `transform: scaleX()` where possible to stay on the compositor thread. `will-change: width, height` is set on animated elements during transitions only (removed after `transitionend` to free memory).
- **Rate limiting**: Backend enforces max 20 requests per session per 10 seconds. Frontend coalesces rapid requests via AbortController.
- **Memory**: File content is held in state for the currently viewed file only. Switching files discards the previous content from memory.

---

## Self-Review

### What works well:

1. **VS Code mental model**: The three-pane layout (tree | editor | terminal) is instantly familiar. Git status colors match VS Code's exact hex values, so developers see the same visual language they already know.
2. **Animated transitions**: The terminal shrinking to the bottom-right provides spatial continuity. The user sees where their terminal "went" rather than experiencing an abrupt layout swap. The FAB at the bottom-right is the visual anchor for both opening and closing.
3. **Progressive disclosure**: Collapsed → tree + terminal → full three-pane. The UI complexity scales with the user's intent. The FAB is minimal and unobtrusive.
4. **Markdown preview**: Rendered markdown is a high-value feature for reviewing READMEs, design docs, and changelogs that workers frequently create or edit.
5. **Activity awareness**: Git status colors + Changed Files view tie the file explorer to the worker's activity — making it more useful than a generic file browser.
6. **Secure remote execution**: Stdin-piped SSH scripts eliminate shell injection risk without requiring agent deployment on remote machines.

### Areas that could be improved:

1. **Syntax highlighting bundle size**: `highlight.js` with dynamic imports is ~15KB per grammar. For a session that opens files in 5+ languages, this adds up. Tree-shaking and CDN-based loading could reduce the impact further.

2. **No real-time updates**: The design still relies on manual refresh or explicit file selection. A WebSocket-based file watcher could stream changes, but adds complexity. The git status integration partially addresses this — `git status` reflects the full picture when the user hits refresh.

3. **Animation on lower-end devices**: The 300ms layout transition involves reflowing the terminal and potentially re-fitting xterm.js. On low-powered machines, this could stutter. A `prefers-reduced-motion` media query should disable animations and use instant layout switches instead.

4. **FAB overlapping terminal content**: The floating button at 70% opacity still covers terminal text in the bottom-right corner. This is a small area (36x36px) and the opacity helps, but it could be annoying for users who frequently read the last line of terminal output. An alternative is to place the FAB in the footer bar, but that reduces discoverability.

5. **Brain Panel coexistence on narrow viewports**: The mutual-collapse heuristic (opening one collapses the other below 700px) works but may surprise users. An alternative is a tabbed approach where Brain Panel and File Explorer are tabs in the same sidebar slot — but this loses the ability to see both simultaneously on wide screens.

6. **Changed Files detection via mtime is imprecise**: A file modified by a cron job or background process would show up as "changed." Git status (Phase 1 for local, Phase 2 for remote) is more accurate. Terminal output parsing (Phase 2) is the most precise but fragile if Claude's output format changes.
