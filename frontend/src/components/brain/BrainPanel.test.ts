import { describe, it, expect } from 'vitest'
import {
  FALLBACK_PROVIDER_REGISTRY,
} from '../../hooks/useProviderRegistry'
import { getBrainPanelQuickActionState } from './BrainPanel'

describe('BrainPanel provider gating', () => {
  it('disables the clear quick action for Codex but not Claude', () => {
    expect(
      getBrainPanelQuickActionState(FALLBACK_PROVIDER_REGISTRY, 'codex'),
    ).toEqual({
      clearDisabledReason: 'Codex quick-clear support is not implemented yet.',
    })

    expect(
      getBrainPanelQuickActionState(FALLBACK_PROVIDER_REGISTRY, 'claude'),
    ).toEqual({
      clearDisabledReason: null,
    })
  })
})
