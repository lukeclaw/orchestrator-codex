import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { api } from '../api/client'
import type { TrendsData, PrMergeDay, PrMergeItem } from '../api/types'

const RANGE_DAYS: Record<string, number> = { '7d': 7, '30d': 30, '90d': 90 }

export function useTrends() {
  const [data, setData] = useState<TrendsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [range, setRange] = useState<'7d' | '30d' | '90d'>('30d')
  const hasLoaded = useRef(false)

  // PR merge data: daily counts + detail items
  const [prMergeDays, setPrMergeDays] = useState<PrMergeDay[]>([])

  const fetchTrends = useCallback(async () => {
    if (!hasLoaded.current) setLoading(true)
    try {
      const result = await api<TrendsData>(`/api/trends?range=${range}`)
      setData(result)
      hasLoaded.current = true
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [range])

  const fetchPrMerges = useCallback(async () => {
    try {
      const days = RANGE_DAYS[range] || 30
      // Ensure the PR search cache is populated for this range.
      // This is fire-and-forget — if it fails (no gh auth), we still
      // read whatever is in the cache via /api/trends/pr-merges.
      await api(`/api/prs?tab=recent&days=${days}`).catch(() => {})
      const result = await api<PrMergeDay[]>(`/api/trends/pr-merges?range=${range}`)
      setPrMergeDays(result)
    } catch {
      // PR data is optional — don't block trends
    }
  }, [range])

  useEffect(() => { fetchTrends() }, [fetchTrends])
  useEffect(() => { fetchPrMerges() }, [fetchPrMerges])

  // Build date→count lookup for chart data
  const prByDay = useMemo(() => {
    const map: Record<string, number> = {}
    for (const d of prMergeDays) map[d.date] = d.count
    return map
  }, [prMergeDays])

  // Build date→PR items lookup for detail modal
  const prDetailByDay = useMemo(() => {
    const map: Record<string, PrMergeItem[]> = {}
    for (const d of prMergeDays) map[d.date] = d.prs
    return map
  }, [prMergeDays])

  // Merge PR counts into throughput data
  const mergedData = useMemo((): TrendsData | null => {
    if (!data) return null
    return {
      ...data,
      throughput: data.throughput.map(d => ({
        ...d,
        prs: prByDay[d.date] || 0,
      })),
    }
  }, [data, prByDay])

  return { data: mergedData, loading, range, setRange, prDetailByDay }
}
