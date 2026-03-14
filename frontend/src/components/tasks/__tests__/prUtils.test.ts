import { describe, it, expect } from 'vitest'
import { isPrUrl, prLinkLabel, getPrStatusChips } from '../prUtils'
import type { PrPreviewData } from '../../../api/types'

describe('isPrUrl', () => {
  it('returns true for valid GitHub PR URLs', () => {
    expect(isPrUrl('https://github.com/org/repo/pull/42')).toBe(true)
    expect(isPrUrl('https://github.com/my-org/my-repo/pull/1')).toBe(true)
  })

  it('returns false for non-PR URLs', () => {
    expect(isPrUrl('https://github.com/org/repo')).toBe(false)
    expect(isPrUrl('https://github.com/org/repo/issues/42')).toBe(false)
    expect(isPrUrl('https://example.com')).toBe(false)
    expect(isPrUrl('')).toBe(false)
  })
})

describe('prLinkLabel', () => {
  it('returns repo#number for valid PR URLs', () => {
    expect(prLinkLabel('https://github.com/org/repo/pull/42')).toBe('repo #42')
    expect(prLinkLabel('https://github.com/my-org/my-repo/pull/123')).toBe('my-repo #123')
  })

  it('returns the URL itself for non-PR URLs', () => {
    expect(prLinkLabel('https://example.com')).toBe('https://example.com')
  })
})

function makePrData(overrides: Partial<PrPreviewData> = {}): PrPreviewData {
  return {
    title: 'Test PR',
    state: 'open',
    draft: false,
    number: 1,
    repo: 'org/repo',
    author: 'alice',
    created_at: '',
    updated_at: '',
    closed_at: null,
    closed_by: null,
    merged_at: null,
    merged_by: null,
    additions: 0,
    deletions: 0,
    changed_files: 0,
    commits: 1,
    reviews: [],
    requested_reviewers: [],
    checks: [],
    auto_merge: false,
    files: [],
    fetched_at: '',
    ...overrides,
  }
}

describe('getPrStatusChips', () => {
  it('returns Merged chip for merged PRs', () => {
    const chips = getPrStatusChips(makePrData({ state: 'merged' }))
    expect(chips).toEqual([{ label: 'Merged', color: 'purple' }])
  })

  it('returns Closed chip for closed PRs', () => {
    const chips = getPrStatusChips(makePrData({ state: 'closed' }))
    expect(chips).toEqual([{ label: 'Closed', color: 'red' }])
  })

  it('returns Open chip for open non-draft PRs', () => {
    const chips = getPrStatusChips(makePrData({ state: 'open', draft: false }))
    expect(chips[0]).toEqual({ label: 'Open', color: 'green' })
  })

  it('returns Draft chip for draft PRs', () => {
    const chips = getPrStatusChips(makePrData({ state: 'open', draft: true }))
    expect(chips[0]).toEqual({ label: 'Draft', color: 'gray' })
  })

  it('returns Approved chip from reviews', () => {
    const chips = getPrStatusChips(makePrData({
      reviews: [{ reviewer: 'bob', state: 'approved', submitted_at: null, comments: 0, comment_threads: [], html_url: null }],
    }))
    expect(chips).toContainEqual({ label: 'Approved', color: 'green' })
  })

  it('returns Changes requested chip from reviews', () => {
    const chips = getPrStatusChips(makePrData({
      reviews: [{ reviewer: 'bob', state: 'changes_requested', submitted_at: null, comments: 0, comment_threads: [], html_url: null }],
    }))
    expect(chips).toContainEqual({ label: 'Changes requested', color: 'red' })
  })

  it('returns CI failing chip', () => {
    const chips = getPrStatusChips(makePrData({
      checks: [
        { name: 'build', status: 'completed', conclusion: 'failure' },
      ],
    }))
    expect(chips).toContainEqual({ label: 'CI failing', color: 'red' })
  })

  it('returns CI running chip', () => {
    const chips = getPrStatusChips(makePrData({
      checks: [
        { name: 'build', status: 'in_progress', conclusion: null },
      ],
    }))
    expect(chips).toContainEqual({ label: 'CI running', color: 'yellow' })
  })

  it('uses approval gate over review-based approval', () => {
    const chips = getPrStatusChips(makePrData({
      reviews: [{ reviewer: 'bob', state: 'approved', submitted_at: null, comments: 0, comment_threads: [], html_url: null }],
      checks: [
        { name: 'Code Approval', status: 'queued', conclusion: null },
      ],
    }))
    expect(chips).toContainEqual({ label: 'Owner approval pending', color: 'yellow' })
    expect(chips.find(c => c.label === 'Approved')).toBeUndefined()
  })

  it('skips cancelled/skipped/neutral checks for CI status', () => {
    const chips = getPrStatusChips(makePrData({
      checks: [
        { name: 'build', status: 'completed', conclusion: 'skipped' },
        { name: 'lint', status: 'completed', conclusion: 'cancelled' },
      ],
    }))
    expect(chips.find(c => c.label === 'CI failing')).toBeUndefined()
    expect(chips.find(c => c.label === 'CI running')).toBeUndefined()
  })
})
