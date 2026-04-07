interface CellInsertBarProps {
  onInsert: (type: 'code' | 'markdown') => void
}

export default function CellInsertBar({ onInsert }: CellInsertBarProps) {
  return (
    <div className="nb-insert-bar">
      <div className="nb-insert-bar__line" />
      <button
        className="nb-insert-bar__btn"
        onClick={() => onInsert('code')}
        title="Insert code cell"
        aria-label="Insert code cell"
      >
        +
      </button>
      <div className="nb-insert-bar__line" />
    </div>
  )
}
