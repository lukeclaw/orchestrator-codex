import { describe, it, expect } from 'vitest'
import {
  FALLBACK_PROVIDER_REGISTRY,
} from '../../hooks/useProviderRegistry'
import { getBrainPanelQuickActionState } from './BrainPanel'

describe('BrainPanel provider gating', () => {
  it('keeps the clear quick action enabled for Codex and Claude', () => {
    expect(
      getBrainPanelQuickActionState(FALLBACK_PROVIDER_REGISTRY, 'codex'),
    ).toEqual({
      clearDisabledReason: null,
    })

    expect(
      getBrainPanelQuickActionState(FALLBACK_PROVIDER_REGISTRY, 'claude'),
    ).toEqual({
      clearDisabledReason: null,
    })
  })
})
