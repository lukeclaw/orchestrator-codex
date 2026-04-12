import type { CellOperation } from './notebookTypes'

export class NotebookUndoStack {
  private undoStack: CellOperation[] = []
  private redoStack: CellOperation[] = []
  private maxSize = 100

  push(op: CellOperation): void {
    this.undoStack.push(op)
    if (this.undoStack.length > this.maxSize) {
      this.undoStack.shift()
    }
    this.redoStack = []
  }

  undo(): CellOperation | null {
    const op = this.undoStack.pop() ?? null
    if (op) this.redoStack.push(op)
    return op
  }

  redo(): CellOperation | null {
    const op = this.redoStack.pop() ?? null
    if (op) this.undoStack.push(op)
    return op
  }

  clear(): void {
    this.undoStack = []
    this.redoStack = []
  }

  get canUndo(): boolean { return this.undoStack.length > 0 }
  get canRedo(): boolean { return this.redoStack.length > 0 }
}
