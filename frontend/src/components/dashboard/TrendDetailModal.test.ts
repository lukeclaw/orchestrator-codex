import { describe, it, expect } from 'vitest'
import { buildDetailQuery } from './TrendDetailModal'
import type { TrendDetailSelection } from '../../api/types'

describe('TrendDetailModal', () => {
  describe('buildDetailQuery', () => {
    it('builds throughput query with date', () => {
      const selection: TrendDetailSelection = { chart: 'throughput', date: '2026-01-15' }
      const url = buildDetailQuery(selection, '7d')
      expect(url).toBe('/api/trends/detail?chart=throughput&date=2026-01-15')
    })

    it('builds worker_hours query with date', () => {
      const selection: TrendDetailSelection = { chart: 'worker_hours', date: '2026-02-20' }
      const url = buildDetailQuery(selection, '30d')
      expect(url).toBe('/api/trends/detail?chart=worker_hours&date=2026-02-20')
    })

    it('builds heatmap query with day_of_week, hour, and range', () => {
      const selection: TrendDetailSelection = { chart: 'heatmap', day_of_week: 1, hour: 14 }
      const url = buildDetailQuery(selection, '90d')
      expect(url).toBe('/api/trends/detail?chart=heatmap&day_of_week=1&hour=14&range=90d')
    })

    it('does not include range for throughput', () => {
      const selection: TrendDetailSelection = { chart: 'throughput', date: '2026-01-15' }
      const url = buildDetailQuery(selection, '90d')
      expect(url).not.toContain('range=')
    })

    it('does not include range for worker_hours', () => {
      const selection: TrendDetailSelection = { chart: 'worker_hours', date: '2026-01-15' }
      const url = buildDetailQuery(selection, '30d')
      expect(url).not.toContain('range=')
    })

    it('builds human_hours query with date', () => {
      const selection: TrendDetailSelection = { chart: 'human_hours', date: '2026-03-10' }
      const url = buildDetailQuery(selection, '7d')
      expect(url).toBe('/api/trends/detail?chart=human_hours&date=2026-03-10')
    })

    it('does not include range for human_hours', () => {
      const selection: TrendDetailSelection = { chart: 'human_hours', date: '2026-03-10' }
      const url = buildDetailQuery(selection, '30d')
      expect(url).not.toContain('range=')
    })

    it('includes range only for heatmap', () => {
      const selection: TrendDetailSelection = { chart: 'heatmap', day_of_week: 0, hour: 0 }
      const url = buildDetailQuery(selection, '7d')
      expect(url).toContain('range=7d')
    })

    it('handles hour=0 and day_of_week=0 correctly', () => {
      const selection: TrendDetailSelection = { chart: 'heatmap', day_of_week: 0, hour: 0 }
      const url = buildDetailQuery(selection, '7d')
      expect(url).toContain('day_of_week=0')
      expect(url).toContain('hour=0')
    })
  })
})
