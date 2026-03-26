import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'

export const DEFAULT_PROVIDER_ID = 'claude'

export const CAPABILITY_WORKER_SESSIONS = 'worker_sessions'
export const CAPABILITY_BRAIN_SESSIONS = 'brain_sessions'
export const CAPABILITY_LOCAL_SESSIONS = 'local_sessions'
export const CAPABILITY_REMOTE_SESSIONS = 'remote_sessions'
export const CAPABILITY_MODEL_SELECTION = 'model_selection'
export const CAPABILITY_EFFORT_SELECTION = 'effort_selection'
export const CAPABILITY_SKIP_PERMISSIONS = 'skip_permissions'
export const CAPABILITY_HOOKS = 'hooks'
export const CAPABILITY_SKILLS_DEPLOYMENT = 'skills_deployment'
export const CAPABILITY_HEARTBEAT_LOOP = 'heartbeat_loop'
export const CAPABILITY_QUICK_CLEAR = 'quick_clear'
export const CAPABILITY_RECONNECT = 'reconnect'

export const CAPABILITY_KEYS = [
  CAPABILITY_WORKER_SESSIONS,
  CAPABILITY_BRAIN_SESSIONS,
  CAPABILITY_LOCAL_SESSIONS,
  CAPABILITY_REMOTE_SESSIONS,
  CAPABILITY_MODEL_SELECTION,
  CAPABILITY_EFFORT_SELECTION,
  CAPABILITY_SKIP_PERMISSIONS,
  CAPABILITY_HOOKS,
  CAPABILITY_SKILLS_DEPLOYMENT,
  CAPABILITY_HEARTBEAT_LOOP,
  CAPABILITY_QUICK_CLEAR,
  CAPABILITY_RECONNECT,
] as const

export type ProviderCapabilityKey = typeof CAPABILITY_KEYS[number]

export interface ProviderCapability {
  supported: boolean
  disabled_reason: string | null
}

export interface ProviderDefinition {
  id: string
  label: string
  capabilities: Record<ProviderCapabilityKey, ProviderCapability>
}

export interface ProviderRegistryResponse {
  providers: ProviderDefinition[]
  defaults: {
    worker: string
    brain: string
  }
}

export interface ProviderOption {
  value: string
  label: string
}

export const FALLBACK_PROVIDER_REGISTRY: ProviderRegistryResponse = {
  providers: [
    {
      id: 'claude',
      label: 'Claude',
      capabilities: {
        [CAPABILITY_WORKER_SESSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_BRAIN_SESSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_LOCAL_SESSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_REMOTE_SESSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_MODEL_SELECTION]: { supported: true, disabled_reason: null },
        [CAPABILITY_EFFORT_SELECTION]: { supported: true, disabled_reason: null },
        [CAPABILITY_SKIP_PERMISSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_HOOKS]: { supported: true, disabled_reason: null },
        [CAPABILITY_SKILLS_DEPLOYMENT]: { supported: true, disabled_reason: null },
        [CAPABILITY_HEARTBEAT_LOOP]: { supported: true, disabled_reason: null },
        [CAPABILITY_QUICK_CLEAR]: { supported: true, disabled_reason: null },
        [CAPABILITY_RECONNECT]: { supported: true, disabled_reason: null },
      },
    },
    {
      id: 'codex',
      label: 'Codex',
      capabilities: {
        [CAPABILITY_WORKER_SESSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_BRAIN_SESSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_LOCAL_SESSIONS]: { supported: true, disabled_reason: null },
        [CAPABILITY_REMOTE_SESSIONS]: {
          supported: false,
          disabled_reason: 'Remote Codex support is not available in MVP.',
        },
        [CAPABILITY_MODEL_SELECTION]: { supported: true, disabled_reason: null },
        [CAPABILITY_EFFORT_SELECTION]: { supported: true, disabled_reason: null },
        [CAPABILITY_SKIP_PERMISSIONS]: {
          supported: false,
          disabled_reason: 'Codex skip-permissions support is not implemented yet.',
        },
        [CAPABILITY_HOOKS]: {
          supported: false,
          disabled_reason: 'Codex hook automation is not implemented yet.',
        },
        [CAPABILITY_SKILLS_DEPLOYMENT]: {
          supported: false,
          disabled_reason: 'Codex skills deployment is not implemented yet.',
        },
        [CAPABILITY_HEARTBEAT_LOOP]: { supported: true, disabled_reason: null },
        [CAPABILITY_QUICK_CLEAR]: { supported: true, disabled_reason: null },
        [CAPABILITY_RECONNECT]: {
          supported: false,
          disabled_reason: 'Codex reconnect support is not implemented yet.',
        },
      },
    },
  ],
  defaults: {
    worker: DEFAULT_PROVIDER_ID,
    brain: DEFAULT_PROVIDER_ID,
  },
}

function buildProviderMap(registry: ProviderRegistryResponse) {
  return new Map(registry.providers.map(provider => [provider.id, provider] as const))
}

export function getProviderDefinition(
  registry: ProviderRegistryResponse,
  providerId: string,
): ProviderDefinition {
  const providerMap = buildProviderMap(registry)
  return providerMap.get(providerId) ?? providerMap.get(DEFAULT_PROVIDER_ID) ?? registry.providers[0] ?? FALLBACK_PROVIDER_REGISTRY.providers[0]
}

export function getProviderCapability(
  registry: ProviderRegistryResponse,
  providerId: string,
  capability: ProviderCapabilityKey,
): ProviderCapability {
  return getProviderDefinition(registry, providerId).capabilities[capability]
}

export function isCapabilitySupported(
  registry: ProviderRegistryResponse,
  providerId: string,
  capability: ProviderCapabilityKey,
): boolean {
  return getProviderCapability(registry, providerId, capability).supported
}

export function getCapabilityDisabledReason(
  registry: ProviderRegistryResponse,
  providerId: string,
  capability: ProviderCapabilityKey,
): string | null {
  const providerCapability = getProviderCapability(registry, providerId, capability)
  return providerCapability.supported ? null : providerCapability.disabled_reason
}

export function getSharedCapabilityDisabledReason(
  registry: ProviderRegistryResponse,
  providerIds: string[],
  capability: ProviderCapabilityKey,
): string | null {
  for (const providerId of providerIds) {
    const disabledReason = getCapabilityDisabledReason(registry, providerId, capability)
    if (disabledReason) return disabledReason
  }
  return null
}

export function getBrainQuickActionDisabledReason(
  registry: ProviderRegistryResponse,
  providerId: string,
  action: 'clear' | 'check' | 'create',
): string | null {
  if (action === 'clear') {
    return getCapabilityDisabledReason(registry, providerId, CAPABILITY_QUICK_CLEAR)
  }
  return null
}

export function getProviderOptions(registry: ProviderRegistryResponse): ProviderOption[] {
  return registry.providers.map(provider => ({
    value: provider.id,
    label: provider.label,
  }))
}

export function useProviderRegistry() {
  const [registry, setRegistry] = useState<ProviderRegistryResponse>(FALLBACK_PROVIDER_REGISTRY)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function loadRegistry() {
      setLoading(true)
      try {
        const data = await api<ProviderRegistryResponse>('/api/settings/providers')
        if (!cancelled && data?.providers?.length) {
          setRegistry(data)
        }
      } catch {
        if (!cancelled) {
          setRegistry(FALLBACK_PROVIDER_REGISTRY)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadRegistry()
    return () => {
      cancelled = true
    }
  }, [])

  const providerMap = useMemo(() => buildProviderMap(registry), [registry])

  const getProvider = useCallback((providerId: string) => {
    return providerMap.get(providerId) ?? providerMap.get(DEFAULT_PROVIDER_ID) ?? registry.providers[0] ?? FALLBACK_PROVIDER_REGISTRY.providers[0]
  }, [providerMap, registry])

  const providerOptions = useMemo(() => getProviderOptions(registry), [registry])

  return {
    registry,
    providers: registry.providers,
    providerMap,
    providerOptions,
    defaults: registry.defaults,
    loading,
    getProvider,
    getProviderCapability: (providerId: string, capability: ProviderCapabilityKey) => getProviderCapability(registry, providerId, capability),
    isCapabilitySupported: (providerId: string, capability: ProviderCapabilityKey) => isCapabilitySupported(registry, providerId, capability),
    getCapabilityDisabledReason: (providerId: string, capability: ProviderCapabilityKey) => getCapabilityDisabledReason(registry, providerId, capability),
  }
}
