import { useState, useCallback, useRef, useEffect } from 'react'
import Editor, { loader } from '@monaco-editor/react'

// Define a custom dark theme with a cooler background
loader.init().then(monaco => {
  monaco.editor.defineTheme('cool-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [],
    colors: {
      'editor.background': '#181a20',
      'editorGutter.background': '#181a20',
      'minimap.background': '#181a20',
    },
  })
})
import { IconX, IconSave, IconPencil, IconEye } from '../common/Icons'
import Markdown from '../common/Markdown'
import type { Tab } from '../../hooks/useEditorTabs'
import './FileViewer.css'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface FileViewerProps {
  sessionId: string
  tabs: Tab[]
  activeTabPath: string | null
  pendingClose: string | null
  saveConflict: string | null
  onTabSelect: (path: string) => void
  onTabClose: (path: string) => boolean
  onTabPin: (path: string) => void
  onConfirmClose: (path: string) => void
  onCancelClose: () => void
  onContentChange: (path: string, content: string) => void
  onSave: (path: string) => Promise<boolean>
  onResolveSaveConflict: (overwrite: boolean) => void
  onReloadTab: (path: string) => void
  onDismissExternalChange: (path: string) => void
  isDirty: (path: string) => boolean
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.ico'])

function isImage(path: string): boolean {
  const ext = path.slice(path.lastIndexOf('.')).toLowerCase()
  return IMAGE_EXTENSIONS.has(ext)
}

/** Map our language names to Monaco's language IDs. */
function monacoLanguage(lang: string | null): string {
  const map: Record<string, string> = {
    python: 'python', javascript: 'javascript', typescript: 'typescript',
    json: 'json', yaml: 'yaml', html: 'html', css: 'css', scss: 'scss', less: 'less',
    bash: 'shell', shell: 'shell', rust: 'rust', go: 'go', java: 'java',
    c: 'c', cpp: 'cpp', ruby: 'ruby', php: 'php', sql: 'sql',
    xml: 'xml', markdown: 'markdown', dockerfile: 'dockerfile',
    toml: 'ini', lua: 'lua', swift: 'swift', kotlin: 'kotlin',
    hcl: 'hcl', r: 'r', ini: 'ini', csv: 'plaintext',
    gradle: 'groovy', makefile: 'makefile', gitignore: 'plaintext',
  }
  return map[lang ?? ''] ?? 'plaintext'
}

function isMarkdownFile(path: string): boolean {
  return path.endsWith('.md') || path.endsWith('.markdown')
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function FileViewer({
  sessionId,
  tabs,
  activeTabPath,
  pendingClose,
  saveConflict,
  onTabSelect,
  onTabClose,
  onTabPin,
  onConfirmClose,
  onCancelClose,
  onContentChange,
  onSave,
  onResolveSaveConflict,
  onReloadTab,
  onDismissExternalChange,
  isDirty,
}: FileViewerProps) {
  // For markdown files: whether showing preview (true) or editor (false)
  const [mdPreviewMode, setMdPreviewMode] = useState<Record<string, boolean>>({})
  const tabBarRef = useRef<HTMLDivElement>(null)

  const activeTab = tabs.find(t => t.path === activeTabPath) ?? null
  const isMarkdown = activeTab ? isMarkdownFile(activeTab.path) : false
  // Markdown defaults to preview; toggle switches to editor
  const showingMdPreview = activeTab && isMarkdown ? (mdPreviewMode[activeTab.path] ?? true) : false

  const toggleMdPreview = useCallback((path: string) => {
    setMdPreviewMode(prev => ({ ...prev, [path]: !(prev[path] ?? true) }))
  }, [])

  // Scroll active tab into view
  useEffect(() => {
    if (!tabBarRef.current || !activeTabPath) return
    const el = tabBarRef.current.querySelector(`[data-tab-path="${CSS.escape(activeTabPath)}"]`)
    if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activeTabPath])

  // Horizontal scroll on mouse wheel
  useEffect(() => {
    const bar = tabBarRef.current
    if (!bar) return
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY === 0) return
      e.preventDefault()
      bar.scrollLeft += e.deltaY
    }
    bar.addEventListener('wheel', onWheel, { passive: false })
    return () => bar.removeEventListener('wheel', onWheel)
  }, [])

  if (tabs.length === 0) {
    return (
      <div className="fe-viewer fe-viewer--empty">
        <span className="fe-viewer-placeholder">Select a file to view</span>
      </div>
    )
  }

  return (
    <div className="fe-viewer">
      {/* Tab bar */}
      <div className="fe-viewer__tab-bar" ref={tabBarRef}>
        {tabs.map(tab => {
          const dirty = isDirty(tab.path)
          const active = tab.path === activeTabPath
          return (
            <div
              key={tab.path}
              data-tab-path={tab.path}
              className={`fe-viewer__tab-item${active ? ' active' : ''}${tab.isPreview ? ' preview' : ''}${tab.externallyChanged ? ' ext-changed' : ''}`}
              onClick={() => onTabSelect(tab.path)}
              onDoubleClick={() => onTabPin(tab.path)}
              title={tab.path}
            >
              {tab.externallyChanged ? (
                <span className="fe-viewer__ext-dot" title="File changed on disk" />
              ) : dirty ? (
                <span className="fe-viewer__dirty-dot" />
              ) : null}
              <span className="fe-viewer__tab-name">{tab.fileName}</span>
              {dirty && (
                <button
                  className="fe-viewer__tab-icon-btn save"
                  onClick={(e) => { e.stopPropagation(); onSave(tab.path) }}
                  title="Save (Ctrl+S)"
                >
                  <IconSave size={12} />
                </button>
              )}
              {isMarkdownFile(tab.path) && active && (
                <button
                  className="fe-viewer__tab-icon-btn"
                  onClick={(e) => { e.stopPropagation(); toggleMdPreview(tab.path) }}
                  title={(mdPreviewMode[tab.path] ?? true) ? 'Switch to editor' : 'Switch to preview'}
                >
                  {(mdPreviewMode[tab.path] ?? true) ? <IconPencil size={12} /> : <IconEye size={12} />}
                </button>
              )}
              <button
                className="fe-viewer__tab-close"
                onClick={(e) => { e.stopPropagation(); onTabClose(tab.path) }}
                title="Close"
              >
                <IconX size={12} />
              </button>
            </div>
          )
        })}
      </div>

      {/* Unsaved changes confirmation */}
      {pendingClose && (
        <div className="fe-viewer__close-confirm">
          <span>Unsaved changes will be lost.</span>
          <button className="fe-viewer__close-confirm-btn save" onClick={() => { onSave(pendingClose).then(ok => { if (ok) onConfirmClose(pendingClose) }); onCancelClose() }}>Save</button>
          <button className="fe-viewer__close-confirm-btn discard" onClick={() => onConfirmClose(pendingClose)}>Discard</button>
          <button className="fe-viewer__close-confirm-btn cancel" onClick={onCancelClose}>Cancel</button>
        </div>
      )}

      {/* Save conflict banner */}
      {saveConflict && activeTab && saveConflict === activeTab.path && (
        <div className="fe-viewer__conflict-banner">
          <span>File was modified on disk. Overwrite with your changes or reload from disk?</span>
          <button className="fe-viewer__close-confirm-btn save" onClick={() => onResolveSaveConflict(true)}>Overwrite</button>
          <button className="fe-viewer__close-confirm-btn discard" onClick={() => onResolveSaveConflict(false)}>Reload</button>
        </div>
      )}

      {/* External change banner */}
      {activeTab && activeTab.externallyChanged && !saveConflict && (
        <div className="fe-viewer__ext-change-banner">
          <span>File changed on disk. Reload to see latest or keep your version.</span>
          <button className="fe-viewer__close-confirm-btn save" onClick={() => onReloadTab(activeTab.path)}>Reload</button>
          <button className="fe-viewer__close-confirm-btn cancel" onClick={() => onDismissExternalChange(activeTab.path)}>Keep</button>
        </div>
      )}

      {/* Content area */}
      <div className="fe-viewer__content">
        {activeTab && activeTab.loading && (
          <div className="fe-viewer__skeleton">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="fe-viewer__skeleton-line" style={{ width: `${40 + Math.random() * 50}%` }} />
            ))}
          </div>
        )}

        {activeTab && activeTab.error && !activeTab.loading && (
          <div className="fe-viewer__error">
            <span>{activeTab.error}</span>
          </div>
        )}

        {activeTab && !activeTab.loading && !activeTab.error && (
          <>
            {activeTab.binary && isImage(activeTab.path) ? (
              <div className="fe-viewer__image">
                <img
                  src={`/api/sessions/${sessionId}/files/raw?path=${encodeURIComponent(activeTab.path)}`}
                  alt={activeTab.fileName}
                />
              </div>
            ) : activeTab.binary ? (
              <div className="fe-viewer__binary">Binary file — preview not available</div>
            ) : showingMdPreview ? (
              <div className="fe-md-preview">
                <Markdown>{activeTab.currentContent ?? ''}</Markdown>
              </div>
            ) : (
              <div className="fe-viewer__monaco">
                <Editor
                  height="100%"
                  language={monacoLanguage(activeTab.language)}
                  value={activeTab.currentContent ?? ''}
                  onChange={(value) => onContentChange(activeTab.path, value ?? '')}
                  theme="cool-dark"
                  options={{
                    minimap: { enabled: true, scale: 1, showSlider: 'mouseover' },
                    fontSize: 12,
                    lineHeight: 19,
                    lineNumbers: 'on',
                    lineNumbersMinChars: 3,
                    lineDecorationsWidth: 4,
                    padding: { top: 8 },
                    scrollBeyondLastLine: false,
                    wordWrap: 'on',
                    readOnly: false,
                    automaticLayout: true,
                    tabSize: 2,
                    renderWhitespace: 'none',
                    overviewRulerLanes: 0,
                    hideCursorInOverviewRuler: true,
                    scrollbar: {
                      verticalScrollbarSize: 10,
                      horizontalScrollbarSize: 10,
                    },
                  }}
                />
              </div>
            )}

            {activeTab.truncated && (
              <div className="fe-viewer__truncated">
                File truncated — showing {(activeTab.currentContent ?? '').split('\n').length} of {activeTab.totalLines} lines.
                Editing disabled for truncated files.
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
