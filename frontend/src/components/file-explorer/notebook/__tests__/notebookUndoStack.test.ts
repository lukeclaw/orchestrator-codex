import { describe, it, expect } from 'vitest'
import { NotebookUndoStack } from '../notebookUndoStack'
import type { CellOperation } from '../notebookTypes'

describe('NotebookUndoStack', () => {
  const makeOp = (type: string, index: number): CellOperation => ({
    type: 'insert',
    index,
    cell: { id: `${type}-${index}`, type: 'code', source: '', executionCount: null, outputs: [], metadata: {}, _raw: undefined },
  })

  it('push and undo returns the operation', () => {
    const stack = new NotebookUndoStack()
    const op = makeOp('insert', 0)
    stack.push(op)
    expect(stack.canUndo).toBe(true)
    const undone = stack.undo()
    expect(undone).toBe(op)
    expect(stack.canUndo).toBe(false)
  })

  it('redo returns the undone operation', () => {
    const stack = new NotebookUndoStack()
    const op = makeOp('insert', 0)
    stack.push(op)
    stack.undo()
    expect(stack.canRedo).toBe(true)
    const redone = stack.redo()
    expect(redone).toBe(op)
    expect(stack.canRedo).toBe(false)
  })

  it('push clears redo stack', () => {
    const stack = new NotebookUndoStack()
    stack.push(makeOp('a', 0))
    stack.undo()
    expect(stack.canRedo).toBe(true)
    stack.push(makeOp('b', 1))
    expect(stack.canRedo).toBe(false)
  })

  it('respects max stack size', () => {
    const stack = new NotebookUndoStack()
    for (let i = 0; i < 150; i++) {
      stack.push(makeOp('op', i))
    }
    // Should have max 100 items
    let count = 0
    while (stack.canUndo) { stack.undo(); count++ }
    expect(count).toBe(100)
  })

  it('undo returns null when empty', () => {
    const stack = new NotebookUndoStack()
    expect(stack.undo()).toBeNull()
  })

  it('redo returns null when empty', () => {
    const stack = new NotebookUndoStack()
    expect(stack.redo()).toBeNull()
  })

  it('clear empties both stacks', () => {
    const stack = new NotebookUndoStack()
    stack.push(makeOp('a', 0))
    stack.push(makeOp('b', 1))
    stack.undo()
    expect(stack.canUndo).toBe(true)
    expect(stack.canRedo).toBe(true)
    stack.clear()
    expect(stack.canUndo).toBe(false)
    expect(stack.canRedo).toBe(false)
  })

  it('handles multiple undo/redo cycles', () => {
    const stack = new NotebookUndoStack()
    const op1 = makeOp('a', 0)
    const op2 = makeOp('b', 1)
    const op3 = makeOp('c', 2)
    stack.push(op1)
    stack.push(op2)
    stack.push(op3)

    expect(stack.undo()).toBe(op3)
    expect(stack.undo()).toBe(op2)
    expect(stack.redo()).toBe(op2)
    expect(stack.redo()).toBe(op3)
    expect(stack.canRedo).toBe(false)
  })
})
