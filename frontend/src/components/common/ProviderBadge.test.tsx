import { describe, it, expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import ProviderBadge, { getProviderLabel, normalizeProviderId } from './ProviderBadge'

describe('ProviderBadge helpers', () => {
  it('normalizes provider ids', () => {
    expect(normalizeProviderId(' Claude ')).toBe('claude')
    expect(normalizeProviderId('')).toBe('')
  })

  it('formats provider labels', () => {
    expect(getProviderLabel('claude')).toBe('Claude')
    expect(getProviderLabel('codex')).toBe('Codex')
    expect(getProviderLabel('unknown-provider')).toBe('Unknown-provider')
  })
})

describe('ProviderBadge', () => {
  it('renders a claude badge', () => {
    const html = renderToStaticMarkup(<ProviderBadge provider="claude" />)
    expect(html).toContain('provider-badge--claude')
    expect(html).toContain('Provider: Claude')
    expect(html).toContain('Claude')
  })

  it('renders a compact codex badge', () => {
    const html = renderToStaticMarkup(<ProviderBadge provider="codex" compact />)
    expect(html).toContain('provider-badge--compact')
    expect(html).toContain('provider-badge--codex')
    expect(html).toContain('Codex')
  })
})
