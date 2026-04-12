import { useState, useEffect, useRef, useMemo } from 'react'
import { api } from '../api/client'
import type { TrendsData, PrMergeDay, ThroughputDetailItem, Task, Project } from '../api/types'
import { useApp } from '../context/AppContext'

export interface InsightMessage {
  id: string
  text: string
  /** [start, end) char indices of the highlighted stat within `text` */
  statRange: [number, number] | null
  type: 'positive' | 'rest-reminder'
}

/** Format hours: 1.0 → "1h", 3.5 → "3.5h" */
function fmtHours(h: number): string {
  const rounded = Math.round(h * 10) / 10
  return rounded === Math.floor(rounded) ? `${Math.floor(rounded)}h` : `${rounded}h`
}

/** Get a date string as YYYY-MM-DD in local timezone */
function toDateStr(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Build a message with a highlighted stat range */
function msg(id: string, before: string, stat: string, after: string, type: InsightMessage['type'] = 'positive'): InsightMessage {
  const text = before + stat + after
  return { id, text, statRange: [before.length, before.length + stat.length], type }
}

/** Sum an array field for entries whose date falls within [since, until] */
function sumDays<T extends { date: string }>(days: T[], since: string, until: string, field: (d: T) => number): number {
  return days.filter(d => d.date >= since && d.date <= until).reduce((s, d) => s + field(d), 0)
}

/** Truncate text at word boundary, adding "..." if cut */
function truncate(text: string, max: number): string {
  if (text.length <= max) return text
  const cut = text.lastIndexOf(' ', max)
  return (cut > 0 ? text.slice(0, cut) : text.slice(0, max)) + '...'
}

/**
 * Pure function: generate positive insight messages from trends data.
 * Exported for testing.
 */
export function generateInsights(
  trends: TrendsData | null,
  prMergeDays: PrMergeDay[],
  tasks: Task[],
  projects: Project[],
  recentCompletions: ThroughputDetailItem[] = [],
): InsightMessage[] {
  const celebrations: InsightMessage[] = []
  const messages: InsightMessage[] = []
  const restReminders: InsightMessage[] = []

  const now = new Date()
  const today = toDateStr(now)

  // Compute week start (Monday) and month start
  const weekStart = new Date(now)
  weekStart.setDate(now.getDate() - ((now.getDay() + 6) % 7)) // Monday
  const weekStartStr = toDateStr(weekStart)

  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1)
  const monthStartStr = toDateStr(monthStart)

  // --- Today ---

  if (trends) {
    const todayThroughput = trends.throughput.find(d => d.date === today)
    if (todayThroughput && todayThroughput.tasks > 0) {
      const n = String(todayThroughput.tasks)
      const label = todayThroughput.tasks === 1 ? 'task' : 'tasks'
      messages.push(msg('tasks-today', '', `${n} ${label}`, ' completed today \u2014 productive day so far'))
    }
    if (todayThroughput && todayThroughput.subtasks > 0) {
      const n = String(todayThroughput.subtasks)
      const label = todayThroughput.subtasks === 1 ? 'subtask' : 'subtasks'
      messages.push(msg('subtasks-today', '', `${n} ${label}`, ' checked off today \u2014 things are moving'))
    }

    const todayWorkerHours = trends.worker_hours.find(d => d.date === today)
    if (todayWorkerHours && todayWorkerHours.hours > 0) {
      const h = fmtHours(todayWorkerHours.hours)
      messages.push(msg('worker-hours-today', '', h, ' of worker compute today \u2014 your agents are putting in the work'))
    }
  }

  // PRs merged today
  if (prMergeDays.length > 0) {
    const todayPrs = sumDays(prMergeDays, today, today, d => d.count)
    if (todayPrs > 0) {
      const n = String(todayPrs)
      const label = todayPrs === 1 ? 'PR' : 'PRs'
      messages.push(msg('prs-today', '', `${n} ${label}`, ' merged today \u2014 code is landing'))
    }
  }

  // Active tasks right now
  const workingNow = tasks.filter(t => t.status === 'in_progress').length
  if (workingNow > 0) {
    const n = String(workingNow)
    const label = workingNow === 1 ? 'task' : 'tasks'
    messages.push(msg('active-now', '', `${n} ${label}`, ' in progress right now \u2014 work is happening as you watch'))
  }

  // --- This week ---

  if (trends) {
    const weekTasks = sumDays(trends.throughput, weekStartStr, today, d => d.tasks)
    if (weekTasks > 0) {
      const n = String(weekTasks)
      messages.push(msg('tasks-week', '', `${n} tasks`, ' completed this week \u2014 strong momentum'))
    }

    const weekWorkerH = sumDays(trends.worker_hours, weekStartStr, today, d => d.hours)
    if (weekWorkerH >= 1) {
      const h = fmtHours(weekWorkerH)
      messages.push(msg('worker-hours-week', '', h, ' of worker compute this week \u2014 you\'re making good use of your agents'))
    }
  }

  if (prMergeDays.length > 0) {
    const weekPrs = sumDays(prMergeDays, weekStartStr, today, d => d.count)
    if (weekPrs >= 2) {
      const n = String(weekPrs)
      messages.push(msg('prs-week', '', `${n} PRs`, ' landed this week \u2014 real code in production'))
    }
  }

  // --- This month ---

  if (trends) {
    const monthTasks = sumDays(trends.throughput, monthStartStr, today, d => d.tasks)
    if (monthTasks >= 5) {
      const n = String(monthTasks)
      messages.push(msg('tasks-month', '', `${n} tasks`, ' completed this month \u2014 consistent output'))
    }

    const monthSubtasks = sumDays(trends.throughput, monthStartStr, today, d => d.subtasks)
    if (monthSubtasks >= 10) {
      const n = String(monthSubtasks)
      messages.push(msg('subtasks-month', '', `${n} subtasks`, ' done this month \u2014 the details add up'))
    }

    const monthWorkerH = sumDays(trends.worker_hours, monthStartStr, today, d => d.hours)
    if (monthWorkerH >= 10) {
      const h = fmtHours(monthWorkerH)
      messages.push(msg('worker-hours-month', '', h, ' of agent compute this month \u2014 that\'s a lot of leverage'))
    }
  }

  if (prMergeDays.length > 0) {
    const monthPrs = sumDays(prMergeDays, monthStartStr, today, d => d.count)
    if (monthPrs >= 3) {
      const n = String(monthPrs)
      messages.push(msg('prs-month', '', `${n} PRs`, ' merged this month \u2014 shipping consistently'))
    }
  }

  // --- Total / cumulative ---

  const projectsWithDoneTasks = new Set(
    tasks.filter(t => t.status === 'done').map(t => t.project_id)
  )
  if (projectsWithDoneTasks.size >= 2) {
    const n = String(projectsWithDoneTasks.size)
    messages.push(msg('multi-project', 'Progress across ', `${n} projects`, ' \u2014 you\'re running a lot in parallel'))
  }

  const totalDone = tasks.filter(t => t.status === 'done').length
  if (totalDone >= 10) {
    const n = String(totalDone)
    messages.push(msg('total-done', '', `${n} tasks`, ' completed across all projects \u2014 look how far you\'ve come'))
  }

  const totalSubtasksDone = tasks.reduce((sum, t) => sum + (t.subtask_stats?.done ?? 0), 0)
  if (totalSubtasksDone >= 20) {
    const n = String(totalSubtasksDone)
    messages.push(msg('total-subtasks', '', `${n} subtasks`, ' finished overall \u2014 all those small wins add up'))
  }

  // --- Specific celebrations ---

  // Completed projects (all tasks done)
  const completedProjects = projects
    .filter(p => p.stats && p.stats.tasks.total > 0 && p.stats.tasks.done === p.stats.tasks.total)
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
    .slice(0, 2)
  for (const p of completedProjects) {
    celebrations.push(msg(`project-done-${p.id}`, '', p.name, ' is all done \u2014 another project wrapped up'))
  }

  // Recently completed top-level tasks (not subtasks)
  const completedTasks = recentCompletions
    .filter(item => !item.is_subtask && item.task_key)
    .slice(0, 3)
  for (const item of completedTasks) {
    const key = item.task_key!
    celebrations.push(msg(`task-done-${key}`, '', key, ` finished \u2014 ${truncate(item.title, 40)}`))
  }

  // Recently merged PRs (specific titles)
  const todayMergedPrs = prMergeDays.find(d => d.date === today)?.prs ?? []
  for (const pr of todayMergedPrs.slice(0, 2)) {
    celebrations.push(msg(`pr-merged-${pr.number}`, 'PR ', `#${pr.number}`, ` merged \u2014 ${truncate(pr.title, 40)}`))
  }

  // All subtasks of a parent task done
  const fullyDoneTasks = tasks
    .filter(t => !t.parent_task_id && t.subtask_stats && t.subtask_stats.total > 0 && t.subtask_stats.done === t.subtask_stats.total)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
    .slice(0, 2)
  for (const t of fullyDoneTasks) {
    const key = t.task_key ?? t.title
    celebrations.push(msg(`subtasks-complete-${t.id}`, 'All subtasks of ', key, ' are done \u2014 ready to wrap up'))
  }

  // --- Rest reminder ---

  if (trends) {
    const todayHuman = trends.human_hours.find(d => d.date === today)
    if (todayHuman && todayHuman.hours > 4) {
      const h = fmtHours(todayHuman.hours)
      const pool = [
        { before: "You've been here for ", stat: h, after: ' today \u2014 your workers can keep going while you rest' },
        { before: '', stat: h, after: ' at the dashboard today \u2014 a good time for a walk, the agents will wait' },
        { before: '', stat: h, after: ' of focus today \u2014 step away for a bit, everything will still be here' },
      ]
      const pick = pool[Math.floor(Date.now() / (20 * 60 * 1000)) % pool.length]
      restReminders.push(msg('rest-reminder', pick.before, pick.stat, pick.after, 'rest-reminder'))
    }
  }

  // Rest reminders first, then specific celebrations, then numeric stats
  return [...restReminders, ...celebrations, ...messages]
}

const CACHE_TTL = 5 * 60 * 1000 // 5 minutes

interface ThroughputDetailResponse {
  items: ThroughputDetailItem[]
}

export function useTickerInsights() {
  const { tasks, projects } = useApp()
  const [trends, setTrends] = useState<TrendsData | null>(null)
  const [prMergeDays, setPrMergeDays] = useState<PrMergeDay[]>([])
  const [recentCompletions, setRecentCompletions] = useState<ThroughputDetailItem[]>([])
  const [loading, setLoading] = useState(true)
  const cacheRef = useRef<{
    trends: TrendsData | null
    prs: PrMergeDay[]
    completions: ThroughputDetailItem[]
    fetchedAt: number
  } | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchData() {
      // Skip if cache is fresh
      if (cacheRef.current && Date.now() - cacheRef.current.fetchedAt < CACHE_TTL) {
        setTrends(cacheRef.current.trends)
        setPrMergeDays(cacheRef.current.prs)
        setRecentCompletions(cacheRef.current.completions)
        setLoading(false)
        return
      }

      const today = toDateStr(new Date())
      try {
        const [trendsResult, prsResult, detailResult] = await Promise.allSettled([
          api<TrendsData>('/api/trends?range=30d'),
          api<PrMergeDay[]>('/api/trends/pr-merges?range=30d'),
          api<ThroughputDetailResponse>(`/api/trends/detail?chart=throughput&date=${today}`),
        ])

        if (cancelled) return

        const t = trendsResult.status === 'fulfilled' ? trendsResult.value : null
        const p = prsResult.status === 'fulfilled' ? prsResult.value : []
        const c = detailResult.status === 'fulfilled' ? detailResult.value.items : []

        cacheRef.current = { trends: t, prs: p, completions: c, fetchedAt: Date.now() }
        setTrends(t)
        setPrMergeDays(p)
        setRecentCompletions(c)
      } catch {
        // Silently fail — ticker just won't show
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, CACHE_TTL)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  const messages = useMemo(
    () => generateInsights(trends, prMergeDays, tasks, projects, recentCompletions),
    [trends, prMergeDays, tasks, projects, recentCompletions],
  )

  return { messages, loading }
}
