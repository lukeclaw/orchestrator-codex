import type { NotebookCell as CellType } from './notebookTypes'
import NotebookOutput from './NotebookOutput'
import Markdown from '../../common/Markdown'
import { highlightCode, escapeHtml } from '../../common/Markdown'

interface NotebookCellProps {
  cell: CellType
  language: string
  isSelected: boolean
  isEditing: boolean
  outputCollapsed: boolean
  showLineNumbers: boolean
  onSelect: () => void
  onEnterEdit: () => void
  onToggleOutputCollapse: () => void
}

function CodeCell({ cell, language, isSelected, isEditing, outputCollapsed, showLineNumbers, onSelect, onEnterEdit, onToggleOutputCollapse }: NotebookCellProps) {
  const highlighted = highlightCode(cell.source, language) || escapeHtml(cell.source)

  return (
    <div className={`nb-cell nb-cell--code${isSelected ? ' nb-cell--selected' : ''}${isEditing ? ' nb-cell--editing' : ''}`}>
      <div className="nb-cell__main">
        <div className="nb-cell__gutter" onClick={onSelect}>
          {cell.executionCount != null ? `[${cell.executionCount}]` : '[ ]'}
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
