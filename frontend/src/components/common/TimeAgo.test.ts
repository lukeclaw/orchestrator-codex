import { describe, it, expect } from 'vitest'
import { parseLocalDate, parseDate } from './TimeAgo'

describe('parseLocalDate', () => {
  it('parses a date-only string as local midnight (not UTC)', () => {
    const d = parseLocalDate('2026-03-09')
    // Should be March 9 in local timezone, regardless of UTC offset
    expect(d.getFullYear()).toBe(2026)
    expect(d.getMonth()).toBe(2) // 0-indexed: March = 2
    expect(d.getDate()).toBe(9)
    expect(d.getHours()).toBe(0)
    expect(d.getMinutes()).toBe(0)
  })

  it('does not shift to previous day in negative UTC offsets', () => {
    // This is the exact bug: new Date("2026-03-09") → UTC midnight → Mar 8 in UTC-8
    // parseLocalDate must always return the date as written
    const d = parseLocalDate('2026-03-09')
    expect(d.getDate()).toBe(9)
  })

  it('handles year boundaries correctly', () => {
    const d = parseLocalDate('2026-01-01')
    expect(d.getFullYear()).toBe(2026)
    expect(d.getMonth()).toBe(0)
    expect(d.getDate()).toBe(1)
  })

  it('handles end-of-month dates', () => {
    const d = parseLocalDate('2026-02-28')
    expect(d.getMonth()).toBe(1)
    expect(d.getDate()).toBe(28)
  })

  it('handles leap year date', () => {
    const d = parseLocalDate('2024-02-29')
    expect(d.getMonth()).toBe(1)
    expect(d.getDate()).toBe(29)
  })
})

describe('parseDate (UTC-based)', () => {
  it('treats timezone-naive datetime strings as UTC', () => {
    const d = parseDate('2026-03-09 12:30:00')
    expect(d.getUTCFullYear()).toBe(2026)
    expect(d.getUTCMonth()).toBe(2)
    expect(d.getUTCDate()).toBe(9)
    expect(d.getUTCHours()).toBe(12)
  })

  it('preserves explicit Z suffix', () => {
    const d = parseDate('2026-03-09T12:30:00Z')
    expect(d.getUTCHours()).toBe(12)
  })

  it('returns current date for null/undefined', () => {
    const before = Date.now()
    const d = parseDate(null)
    const after = Date.now()
    expect(d.getTime()).toBeGreaterThanOrEqual(before)
    expect(d.getTime()).toBeLessThanOrEqual(after)
  })
})
