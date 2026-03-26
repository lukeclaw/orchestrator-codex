import { describe, it, expect } from 'vitest'
import {
  FALLBACK_PROVIDER_REGISTRY,
} from '../hooks/useProviderRegistry'
import { getSettingsCapabilityState } from './SettingsPage'

describe('SettingsPage provider gating', () => {
  it('keeps provider-specific launch settings enabled for supported providers', () => {
    expect(
      getSettingsCapabilityState(FALLBACK_PROVIDER_REGISTRY, 'claude'),
    ).toEqual({
      claudeUpdateBeforeStartDisabledReason: null,
      claudeSkipPermissionsDisabledReason: null,
      claudeDefaultModelDisabledReason: null,
      claudeDefaultEffortDisabledReason: null,
      codexDefaultModelDisabledReason: null,
      codexDefaultEffortDisabledReason: null,
      brainHeartbeatDisabledReason: null,
    })
  })

  it('keeps the brain heartbeat enabled when the brain default is Codex', () => {
    expect(
      getSettingsCapabilityState(FALLBACK_PROVIDER_REGISTRY, 'codex'),
    ).toEqual({
      claudeUpdateBeforeStartDisabledReason: null,
      claudeSkipPermissionsDisabledReason: null,
      claudeDefaultModelDisabledReason: null,
      claudeDefaultEffortDisabledReason: null,
      codexDefaultModelDisabledReason: null,
      codexDefaultEffortDisabledReason: null,
      brainHeartbeatDisabledReason: null,
    })
  })
})
