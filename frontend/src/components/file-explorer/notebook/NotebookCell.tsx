import type { NotebookCell as CellType } from './notebookTypes'
import NotebookOutput from './NotebookOutput'
import Markdown from '../../common/Markdown'
import { highlightCode, escapeHtml } from '../../common/Markdown'

interface NotebookCellProps {
  cell: CellType
  language: string
  isSelected: boolean
  isEditing: boolean
  isExecuting: boolean
  outputCollapsed: boolean
  showLineNumbers: boolean
  onSelect: () => void
  onEnterEdit: () => void
  onToggleOutputCollapse: () => void
  onRun?: () => void
  onInterrupt?: () => void
}

function CodeCell({ cell, language, isSelected, isEditing, isExecuting, outputCollapsed, showLineNumbers, onSelect, onEnterEdit, onToggleOutputCollapse, onRun, onInterrupt }: NotebookCellProps) {
  const highlighted = highlightCode(cell.source, language) || escapeHtml(cell.source)

  const execDisplay = isExecuting ? '[*]' : cell.executionCount != null ? `[${cell.executionCount}]` : '[ ]'

  return (
    <div className={`nb-cell nb-cell--code${isSelected ? ' nb-cell--selected' : ''}${isEditing ? ' nb-cell--editing' : ''}${isExecuting ? ' nb-cell--executing' : ''}`}>
      <div className="nb-cell__main">
        <div className="nb-cell__gutter" onClick={onSelect}>
          <button
            className={`nb-cell__run-btn${isExecuting ? ' nb-cell__run-btn--stop' : ''}`}
            onClick={(e) => { e.stopPropagation(); isExecuting ? onInterrupt?.() : onRun?.() }}
            title={isExecuting ? 'Interrupt (I,I)' : 'Run cell (Ctrl+Enter)'}
            aria-label={isExecuting ? 'Interrupt execution' : 'Run cell'}
          >
            {isExecuting ? (
              <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><rect x="1" y="1" width="8" height="8" rx="1"/></svg>
            ) : (
              <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><path d="M2 1l7 4-7 4z"/></svg>
            )}
          </button>
          <span className={isExecuting ? 'nb-cell__exec-count--running' : ''}>{execDisplay}</span>
        </div>
        <div className="nb-cell__content">
          {!isEditing && (
            <div className="nb-cell__source" onClick={onEnterEdit}>
              {showLineNumbers && (
                <div className="nb-cell__line-numbers">
                  {cell.source.split('\n').map((_, i) => (
                    <div key={i} className="nb-cell__line-number">{i + 1}</div>
                  ))}
                </div>
              )}
              <pre className="nb-cell__source-static">
                <code dangerouslySetInnerHTML={{ __html: highlighted }} />
              </pre>
            </div>
          )}
          <div id={`nb-editor-slot-${cell.id}`} className="nb-cell__editor-slot" />
        </div>
      </div>
      {cell.outputs.length > 0 && (
        <NotebookOutput
          outputs={cell.outputs}
          collapsed={outputCollapsed}
          onToggleCollapse={onToggleOutputCollapse}
        />
      )}
    </div>
  )
}

function MarkdownCell({ cell, isSelected, isEditing, onSelect, onEnterEdit }: NotebookCellProps) {
  return (
    <div className={`nb-cell nb-cell--markdown${isSelected ? ' nb-cell--selected' : ''}${isEditing ? ' nb-cell--editing' : ''}`}>
      <div className="nb-cell__main">
        <div className="nb-cell__gutter nb-cell__gutter--md" onClick={onSelect} />
        <div className="nb-cell__content">
          {!isEditing && (
            <div className="nb-cell__preview" onClick={onEnterEdit}>
              {cell.source.trim() ? (
                <Markdown>{cell.source}</Markdown>
              ) : (
                <span className="nb-cell__placeholder">Empty markdown cell</span>
              )}
            </div>
          )}
          <div id={`nb-editor-slot-${cell.id}`} className="nb-cell__editor-slot" />
        </div>
      </div>
    </div>
  )
}

function RawCell({ cell, isSelected, isEditing, onSelect, onEnterEdit }: NotebookCellProps) {
  return (
    <div className={`nb-cell nb-cell--raw${isSelected ? ' nb-cell--selected' : ''}${isEditing ? ' nb-cell--editing' : ''}`}>
      <div className="nb-cell__main">
        <div className="nb-cell__gutter nb-cell__gutter--raw" onClick={onSelect} />
        <div className="nb-cell__content">
          {!isEditing && (
            <pre className="nb-cell__source-static nb-cell__source-static--raw" onClick={onEnterEdit}>
              {cell.source}
            </pre>
          )}
          <div id={`nb-editor-slot-${cell.id}`} className="nb-cell__editor-slot" />
        </div>
      </div>
    </div>
  )
}

export default function NotebookCell(props: NotebookCellProps) {
  switch (props.cell.type) {
    case 'code':
      return <CodeCell {...props} />
    case 'markdown':
      return <MarkdownCell {...props} />
    case 'raw':
      return <RawCell {...props} />
    default:
      return null
  }
}
