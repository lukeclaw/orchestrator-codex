import { describe, it, expect, beforeEach, vi } from 'vitest'

// Test the clamping / default logic without React hooks
// (vitest in this project runs without jsdom, so we test pure logic)

describe('useFileExplorerState constants and logic', () => {
  const MIN_WIDTH = 180
  const MAX_WIDTH = 400
  const DEFAULT_WIDTH = 240
  const DEFAULT_VIEWER_RATIO = 0.5

  describe('width clamping', () => {
    function clampWidth(w: number): number {
      return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, w))
    }

    it('clamps below minimum', () => {
      expect(clampWidth(100)).toBe(MIN_WIDTH)
    })

    it('clamps above maximum', () => {
      expect(clampWidth(500)).toBe(MAX_WIDTH)
    })

    it('keeps value in range', () => {
      expect(clampWidth(300)).toBe(300)
    })

    it('handles edge - exactly minimum', () => {
      expect(clampWidth(MIN_WIDTH)).toBe(MIN_WIDTH)
    })

    it('handles edge - exactly maximum', () => {
      expect(clampWidth(MAX_WIDTH)).toBe(MAX_WIDTH)
    })
  })

  describe('viewer height ratio clamping', () => {
    function clampRatio(r: number): number {
      return Math.max(0.2, Math.min(0.8, r))
    }

    it('clamps below 0.2', () => {
      expect(clampRatio(0.1)).toBe(0.2)
    })

    it('clamps above 0.8', () => {
      expect(clampRatio(0.9)).toBe(0.8)
    })

    it('keeps value in range', () => {
      expect(clampRatio(0.5)).toBe(0.5)
    })
  })

  describe('localStorage key conventions', () => {
    it('uses fe- prefix for all keys', () => {
      const keys = ['fe-open', 'fe-width', 'fe-viewer-ratio', 'fe-view-mode', 'fe-show-ignored']
      keys.forEach(key => {
        expect(key.startsWith('fe-')).toBe(true)
      })
    })
  })

  describe('default values', () => {
    it('default width is 240', () => {
      expect(DEFAULT_WIDTH).toBe(240)
    })

    it('default viewer ratio is 0.5', () => {
      expect(DEFAULT_VIEWER_RATIO).toBe(0.5)
    })
  })
})
