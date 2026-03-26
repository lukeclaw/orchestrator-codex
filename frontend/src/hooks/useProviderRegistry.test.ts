import { describe, it, expect } from 'vitest'
import {
  FALLBACK_PROVIDER_REGISTRY,
  CAPABILITY_REMOTE_SESSIONS,
  CAPABILITY_HOOKS,
  getCapabilityDisabledReason,
  getSharedCapabilityDisabledReason,
  getBrainQuickActionDisabledReason,
} from './useProviderRegistry'

describe('provider registry helpers', () => {
  it('treats Claude as the baseline provider', () => {
    expect(
      getCapabilityDisabledReason(FALLBACK_PROVIDER_REGISTRY, 'claude', CAPABILITY_REMOTE_SESSIONS),
    ).toBeNull()
  })

  it('exposes Codex disabled reasons for unsupported capabilities', () => {
    expect(
      getCapabilityDisabledReason(FALLBACK_PROVIDER_REGISTRY, 'codex', CAPABILITY_REMOTE_SESSIONS),
    ).toBe('Remote Codex support is not available in MVP.')
  })

  it('returns the first unsupported shared launch capability reason', () => {
    expect(
      getSharedCapabilityDisabledReason(
        FALLBACK_PROVIDER_REGISTRY,
        ['claude', 'codex'],
        CAPABILITY_HOOKS,
      ),
    ).toBe('Codex hook automation is not implemented yet.')
  })

  it('disables only the unsupported brain quick action for Codex', () => {
    expect(
      getBrainQuickActionDisabledReason(FALLBACK_PROVIDER_REGISTRY, 'codex', 'clear'),
    ).toBe('Codex quick-clear support is not implemented yet.')
    expect(
      getBrainQuickActionDisabledReason(FALLBACK_PROVIDER_REGISTRY, 'codex', 'check'),
    ).toBeNull()
    expect(
      getBrainQuickActionDisabledReason(FALLBACK_PROVIDER_REGISTRY, 'codex', 'create'),
    ).toBeNull()
  })
})
