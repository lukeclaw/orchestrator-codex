import { describe, it, expect } from 'vitest'
import {
  FALLBACK_PROVIDER_REGISTRY,
} from '../hooks/useProviderRegistry'
import { getSettingsCapabilityState } from './SettingsPage'

describe('SettingsPage provider gating', () => {
  it('keeps Claude settings enabled when both defaults are Claude', () => {
    expect(
      getSettingsCapabilityState(FALLBACK_PROVIDER_REGISTRY, 'claude', 'claude'),
    ).toEqual({
      updateBeforeStartDisabledReason: null,
      skipPermissionsDisabledReason: null,
      defaultModelDisabledReason: null,
      defaultEffortDisabledReason: null,
      brainHeartbeatDisabledReason: null,
    })
  })

  it('disables Claude-only launch settings for Codex defaults', () => {
    expect(
      getSettingsCapabilityState(FALLBACK_PROVIDER_REGISTRY, 'codex', 'codex'),
    ).toEqual({
      updateBeforeStartDisabledReason: 'Codex hook automation is not implemented yet.',
      skipPermissionsDisabledReason: 'Codex skip-permissions support is not implemented yet.',
      defaultModelDisabledReason: null,
      defaultEffortDisabledReason: null,
      brainHeartbeatDisabledReason: 'Codex heartbeat loop support is not implemented yet.',
    })
  })
})
