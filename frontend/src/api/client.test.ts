// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { openUrl } from './client'

describe('openUrl', () => {
  const originalFetch = globalThis.fetch
  const originalOpen = window.open

  let mockFetch: ReturnType<typeof vi.fn>
  let mockOpen: ReturnType<typeof vi.fn>

  beforeEach(() => {
    mockFetch = vi.fn()
    mockOpen = vi.fn()
    globalThis.fetch = mockFetch as unknown as typeof fetch
    window.open = mockOpen as unknown as typeof window.open
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    window.open = originalOpen
  })

  it('calls /api/open-url with the given URL', () => {
    mockFetch.mockResolvedValue(new Response('ok'))
    openUrl('https://github.com/org/repo/pull/42')

    expect(mockFetch).toHaveBeenCalledWith('/api/open-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: 'https://github.com/org/repo/pull/42' }),
    })
  })

  it('falls back to window.open when fetch fails', async () => {
    mockFetch.mockRejectedValue(new Error('network error'))
    openUrl('https://example.com')

    // Wait for the catch handler to fire
    await vi.waitFor(() => {
      expect(mockOpen).toHaveBeenCalledWith('https://example.com', '_blank', 'noopener')
    })
  })

  it('does not call window.open when fetch succeeds', async () => {
    mockFetch.mockResolvedValue(new Response('ok'))
    openUrl('https://example.com')

    // Give the promise time to resolve
    await new Promise(r => setTimeout(r, 10))
    expect(mockOpen).not.toHaveBeenCalled()
  })
})
