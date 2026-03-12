import { describe, it, expect } from 'vitest'
import type { Session } from '../api/types'

// Pure helpers mirroring WorkersPage logic for testability

type WorkerType = 'local' | 'ssh' | 'rdev'

function getWorkerType(host: string): WorkerType {
  if (host.includes('/')) return 'rdev'
  if (host !== 'localhost') return 'ssh'
  return 'local'
}

function computeTypeCounts(workers: Pick<Session, 'host'>[]): Record<string, number> {
  return workers.reduce<Record<string, number>>((acc, w) => {
    const type = getWorkerType(w.host)
    acc[type] = (acc[type] || 0) + 1
    return acc
  }, {})
}

function filterByType<T extends Pick<Session, 'host'>>(
  workers: T[],
  typeFilter: '' | WorkerType,
): T[] {
  if (!typeFilter) return workers
  return workers.filter(s => {
    if (typeFilter === 'rdev') return s.host.includes('/')
    if (typeFilter === 'ssh') return !s.host.includes('/') && s.host !== 'localhost'
    return s.host === 'localhost'
  })
}

function filterByStatus<T extends Pick<Session, 'status'>>(
  workers: T[],
  statusFilter: string,
): T[] {
  if (!statusFilter) return workers
  return workers.filter(s => s.status === statusFilter)
}

// Fixtures

function makeWorker(overrides: Partial<Session> & { host: string }): Session {
  return {
    id: Math.random().toString(36).slice(2),
    name: 'worker',
    work_dir: null,
    tunnel_pid: null,
    status: 'idle',
    created_at: '2025-01-01T00:00:00Z',
    last_status_changed_at: null,
    last_viewed_at: null,
    session_type: 'worker',
    auto_reconnect: false,
    rws_pty_id: null,
    ...overrides,
  }
}

const LOCAL = makeWorker({ name: 'local-1', host: 'localhost', status: 'idle' })
const SSH = makeWorker({ name: 'ssh-1', host: 'devbox.corp.net', status: 'working' })
const RDEV1 = makeWorker({ name: 'rdev-1', host: 'rdev/project/machine-1', status: 'idle' })
const RDEV2 = makeWorker({ name: 'rdev-2', host: 'rdev/project/machine-2', status: 'working' })
const LOCAL2 = makeWorker({ name: 'local-2', host: 'localhost', status: 'error' })

const ALL_WORKERS = [LOCAL, SSH, RDEV1, RDEV2, LOCAL2]

describe('WorkersPage type filter logic', () => {
  describe('getWorkerType', () => {
    it('classifies localhost as local', () => {
      expect(getWorkerType('localhost')).toBe('local')
    })

    it('classifies host with / as rdev', () => {
      expect(getWorkerType('rdev/project/machine')).toBe('rdev')
      expect(getWorkerType('abc/def')).toBe('rdev')
    })

    it('classifies non-localhost non-slash host as ssh', () => {
      expect(getWorkerType('devbox.corp.net')).toBe('ssh')
      expect(getWorkerType('192.168.1.1')).toBe('ssh')
    })
  })

  describe('computeTypeCounts', () => {
    it('counts each type correctly', () => {
      const counts = computeTypeCounts(ALL_WORKERS)
      expect(counts).toEqual({ local: 2, ssh: 1, rdev: 2 })
    })

    it('returns empty object for no workers', () => {
      expect(computeTypeCounts([])).toEqual({})
    })

    it('handles single type', () => {
      const counts = computeTypeCounts([LOCAL, LOCAL2])
      expect(counts).toEqual({ local: 2 })
    })
  })

  describe('filterByType', () => {
    it('returns all workers when filter is empty', () => {
      expect(filterByType(ALL_WORKERS, '')).toEqual(ALL_WORKERS)
    })

    it('filters to local workers only', () => {
      const result = filterByType(ALL_WORKERS, 'local')
      expect(result.map(w => w.name)).toEqual(['local-1', 'local-2'])
    })

    it('filters to ssh workers only', () => {
      const result = filterByType(ALL_WORKERS, 'ssh')
      expect(result.map(w => w.name)).toEqual(['ssh-1'])
    })

    it('filters to rdev workers only', () => {
      const result = filterByType(ALL_WORKERS, 'rdev')
      expect(result.map(w => w.name)).toEqual(['rdev-1', 'rdev-2'])
    })
  })

  describe('combined status + type filtering', () => {
    it('filters by both status and type', () => {
      const byStatus = filterByStatus(ALL_WORKERS, 'idle')
      const result = filterByType(byStatus, 'rdev')
      expect(result.map(w => w.name)).toEqual(['rdev-1'])
    })

    it('returns empty when filters exclude everything', () => {
      const byStatus = filterByStatus(ALL_WORKERS, 'error')
      const result = filterByType(byStatus, 'ssh')
      expect(result).toEqual([])
    })

    it('status filter alone works correctly', () => {
      const result = filterByStatus(ALL_WORKERS, 'working')
      expect(result.map(w => w.name)).toEqual(['ssh-1', 'rdev-2'])
    })
  })
})
