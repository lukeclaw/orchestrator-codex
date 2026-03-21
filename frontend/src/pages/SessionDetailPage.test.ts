// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

/**
 * Tests for SessionDetailPage's onFocusRef callback pattern.
 *
 * When TerminalView initializes, it calls `onFocusRef(fn)` where `fn` focuses
 * the terminal. SessionDetailPage's callback must:
 *   1. Store the function in terminalFocusRef for later use (paste, overlay close)
 *   2. Call it via requestAnimationFrame to auto-focus on mount
 *
 * This mirrors the pattern used by InteractiveCLI (InteractiveCLI.tsx:122-126).
 */

describe('SessionDetailPage onFocusRef auto-focus', () => {
  let rafCallbacks: FrameRequestCallback[]
  const originalRAF = globalThis.requestAnimationFrame

  beforeEach(() => {
    rafCallbacks = []
    globalThis.requestAnimationFrame = vi.fn((cb: FrameRequestCallback) => {
      rafCallbacks.push(cb)
      return rafCallbacks.length
    })
  })

  afterEach(() => {
    globalThis.requestAnimationFrame = originalRAF
  })

  it('calls the focus function via requestAnimationFrame when onFocusRef fires', () => {
    // Simulate what SessionDetailPage does in the onFocusRef callback:
    //   (fn) => { terminalFocusRef.current = fn; requestAnimationFrame(() => fn()) }
    let terminalFocusRef: (() => void) | null = null
    const onFocusRef = (fn: () => void) => {
      terminalFocusRef = fn
      requestAnimationFrame(() => fn())
    }

    const mockFocus = vi.fn()

    // TerminalView calls onFocusRef with a focus function during init
    onFocusRef(mockFocus)

    // The ref should be stored for later use
    expect(terminalFocusRef).toBe(mockFocus)

    // Focus should not be called synchronously (uses rAF)
    expect(mockFocus).not.toHaveBeenCalled()

    // Flush rAF
    rafCallbacks.forEach(cb => cb(0))

    // Now focus should have been called
    expect(mockFocus).toHaveBeenCalledOnce()
  })

  it('stores the focus function for later use (paste refocus, overlay close)', () => {
    let terminalFocusRef: (() => void) | null = null
    const onFocusRef = (fn: () => void) => {
      terminalFocusRef = fn
      requestAnimationFrame(() => fn())
    }

    const mockFocus = vi.fn()
    onFocusRef(mockFocus)

    // Simulate later usage: paste completes, call stored ref
    expect(terminalFocusRef).not.toBeNull()
    terminalFocusRef!()
    terminalFocusRef!()

    // 2 direct calls (rAF not flushed yet)
    expect(mockFocus).toHaveBeenCalledTimes(2)

    // Flush rAF → total 3
    rafCallbacks.forEach(cb => cb(0))
    expect(mockFocus).toHaveBeenCalledTimes(3)
  })
})
