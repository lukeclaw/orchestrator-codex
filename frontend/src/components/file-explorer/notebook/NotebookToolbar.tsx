interface NotebookToolbarProps {
  cellId: string
  cellType: 'code' | 'markdown' | 'raw'
  onDelete: () => void
  onChangeType: (newType: 'code' | 'markdown') => void
}

export default function NotebookToolbar({ cellType, onDelete, onChangeType }: NotebookToolbarProps) {
  return (
    <div className="nb-cell__toolbar">
      <button
        className="nb-cell__toolbar-btn"
        title={cellType === 'code' ? 'Convert to Markdown' : 'Convert to Code'}
        onClick={(e) => { e.stopPropagation(); onChangeType(cellType === 'code' ? 'markdown' : 'code') }}
        aria-label={cellType === 'code' ? 'Convert to Markdown' : 'Convert to Code'}
      >
        {cellType === 'code' ? 'M' : '</>'}
      </button>
      <button
        className="nb-cell__toolbar-btn nb-cell__toolbar-btn--danger"
        title="Delete cell (dd)"
        onClick={(e) => { e.stopPropagation(); onDelete() }}
        aria-label="Delete cell"
      >
        &times;
      </button>
    </div>
  )
}
