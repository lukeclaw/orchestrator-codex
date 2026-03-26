import { describe, it, expect } from 'vitest'
import {
  FALLBACK_PROVIDER_REGISTRY,
} from '../../hooks/useProviderRegistry'
import {
  buildWorkerCreatePayload,
  getRemoteWorkerTypeDisabledReason,
} from './AddSessionModal'

describe('AddSessionModal provider flow', () => {
  it('includes the selected provider in local worker payloads', () => {
    expect(
      buildWorkerCreatePayload({
        workerType: 'local',
        provider: 'codex',
        name: 'api-worker',
        selectedRdev: '',
        sshHost: '',
        sshWorkDir: '',
        mpPath: '/tmp/project',
      }),
    ).toEqual({
      name: 'api-worker',
      host: 'localhost',
      work_dir: '/tmp/project',
      provider: 'codex',
    })
  })

  it('disables remote worker types for Codex but not Claude', () => {
    expect(getRemoteWorkerTypeDisabledReason(FALLBACK_PROVIDER_REGISTRY, 'codex')).toBe(
      'Remote Codex support is not available in MVP.',
    )
    expect(getRemoteWorkerTypeDisabledReason(FALLBACK_PROVIDER_REGISTRY, 'claude')).toBeNull()
  })
})
