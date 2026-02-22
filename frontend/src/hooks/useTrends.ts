import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { TrendsData } from '../api/types'

export function useTrends() {
  const [data, setData] = useState<TrendsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [range, setRange] = useState<'7d' | '30d' | '90d'>('7d')

  const fetch = useCallback(async () => {
    setLoading(true)
    try {
      const result = await api<TrendsData>(`/api/trends?range=${range}`)
      setData(result)
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => { fetch() }, [fetch])

  return { data, loading, range, setRange }
}
