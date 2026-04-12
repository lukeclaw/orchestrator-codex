import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { generateInsights } from './useTickerInsights'
import type { TrendsData, PrMergeDay, ThroughputDetailItem, Task, Project } from '../api/types'

// Frozen time: Monday 2026-04-06 14:00 local
// Week starts Monday 2026-04-06, month starts 2026-04-01
const FROZEN = new Date('2026-04-06T14:00:00')
const TODAY = '2026-04-06'
const YESTERDAY = '2026-04-05'   // Sunday (prior week)
const MONTH_DAY = '2026-04-02'   // Thursday (this month, but prior week)

/** Build TrendsData with entries on specific days */
function makeTrends(
  days: { date: string; tasks?: number; subtasks?: number; workerH?: number; humanH?: number }[],
): TrendsData {
  return {
    range: '30d',
    throughput: days.map(d => ({ date: d.date, tasks: d.tasks ?? 0, subtasks: d.subtasks ?? 0 })),
    heatmap: [],
    worker_hours: days.map(d => ({ date: d.date, hours: d.workerH ?? 0 })),
    human_hours: days.map(d => ({ date: d.date, hours: d.humanH ?? 0 })),
  }
}

function makePrDays(entries: { date: string; count: number; prs?: { number: number; title: string }[] }[]): PrMergeDay[] {
  return entries.map(e => ({
    date: e.date,
    count: e.count,
    prs: (e.prs ?? []).map(p => ({ url: '', number: p.number, title: p.title, repo: 'test/repo', merged_at: e.date, additions: 0, deletions: 0 })),
  }))
}

function makeProject(name: string, tasksDone: number, tasksTotal: number): Project {
  return {
    id: `proj-${name.toLowerCase().replace(/\s/g, '-')}`,
    name,
    description: null,
    status: tasksDone === tasksTotal ? 'completed' : 'active',
    target_date: null,
    starred: false,
    created_at: '2026-04-01T10:00:00',
    updated_at: '2026-04-06T10:00:00',
    stats: {
      tasks: { total: tasksTotal, todo: 0, in_progress: 0, done: tasksDone, blocked: 0 },
      subtasks: { total: 0, done: 0 },
      workers: { total: 0, working: 0, idle: 0, waiting: 0, blocked: 0 },
      context: { total: 0 },
    },
  }
}

function makeCompletion(taskKey: string, title: string, isSubtask = false): ThroughputDetailItem {
  return {
    entity_id: `task-${taskKey}`,
    is_subtask: isSubtask,
    timestamp: '2026-04-06T14:00:00Z',
    title,
    task_key: taskKey,
    status: 'done',
    parent_task_id: null,
    parent_title: null,
    parent_task_key: null,
  }
}

function makeTask(projectId: string, status: string, subtasksDone = 0): Task {
  return {
    id: `task-${Math.random().toString(36).slice(2, 8)}`,
    project_id: projectId,
    title: 'Test task',
    description: null,
    status,
    priority: 'M',
    assigned_session_id: null,
    parent_task_id: null,
    notes: null,
    links: [],
    task_index: null,
    task_key: null,
    subtask_stats: subtasksDone > 0 ? { total: subtasksDone, done: subtasksDone, in_progress: 0 } : undefined,
    created_at: '2026-04-06T10:00:00',
    updated_at: '2026-04-06T10:00:00',
  }
}

describe('generateInsights', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(FROZEN)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  // --- basics ---

  it('returns empty array for null data', () => {
    expect(generateInsights(null, [], [], [])).toEqual([])
  })

  it('returns empty array when all stats are zero', () => {
    const trends = makeTrends([{ date: TODAY }])
    expect(generateInsights(trends, [], [], [])).toEqual([])
  })

  // --- today ---

  it('generates tasks-today message', () => {
    const trends = makeTrends([{ date: TODAY, tasks: 5 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'tasks-today')!
    expect(m.text).toContain('5 tasks')
    expect(m.text).toContain('productive day')
  })

  it('singular form for 1 task', () => {
    const trends = makeTrends([{ date: TODAY, tasks: 1 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'tasks-today')!
    expect(m.text).toContain('1 task')
    expect(m.text).not.toContain('1 tasks')
  })

  it('generates subtasks-today message', () => {
    const trends = makeTrends([{ date: TODAY, subtasks: 8 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'subtasks-today')!
    expect(m.text).toContain('8 subtasks')
    expect(m.text).toContain('things are moving')
  })

  it('generates worker-hours-today message', () => {
    const trends = makeTrends([{ date: TODAY, workerH: 6.2 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'worker-hours-today')!
    expect(m.text).toContain('6.2h')
    expect(m.text).toContain('worker compute today')
  })

  it('formats whole hours without decimal', () => {
    const trends = makeTrends([{ date: TODAY, workerH: 8.0 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'worker-hours-today')!
    expect(m.text).toContain('8h')
    expect(m.text).not.toContain('8.0h')
  })

  it('generates active-now for in-progress tasks', () => {
    const tasks = [makeTask('a', 'in_progress'), makeTask('b', 'in_progress')]
    const m = generateInsights(null, [], tasks, []).find(m => m.id === 'active-now')!
    expect(m.text).toContain('2 tasks')
    expect(m.text).toContain('in progress right now')
  })

  // --- this week ---

  it('generates tasks-week by summing Mon–today', () => {
    // Frozen to Monday, so only today counts as "this week"
    const trends = makeTrends([
      { date: YESTERDAY, tasks: 10 }, // Sunday = prior week
      { date: TODAY, tasks: 3 },      // Monday = this week
    ])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'tasks-week')!
    expect(m.text).toContain('3 tasks')
    expect(m.text).toContain('this week')
  })

  it('generates worker-hours-week when >= 1h', () => {
    const trends = makeTrends([{ date: TODAY, workerH: 2.5 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'worker-hours-week')!
    expect(m.text).toContain('2.5h')
    expect(m.text).toContain('this week')
  })

  it('generates prs-today when PRs merged today', () => {
    const prs = makePrDays([{ date: TODAY, count: 2 }])
    const m = generateInsights(null, prs, [], []).find(m => m.id === 'prs-today')!
    expect(m.text).toContain('2 PRs')
    expect(m.text).toContain('merged today')
  })

  it('singular form for 1 PR today', () => {
    const prs = makePrDays([{ date: TODAY, count: 1 }])
    const m = generateInsights(null, prs, [], []).find(m => m.id === 'prs-today')!
    expect(m.text).toContain('1 PR')
    expect(m.text).not.toContain('1 PRs')
  })

  it('generates prs-week when >= 2 PRs this week', () => {
    const prs = makePrDays([
      { date: YESTERDAY, count: 5 },  // prior week (Sunday)
      { date: TODAY, count: 3 },       // this week (Monday)
    ])
    const m = generateInsights(null, prs, [], []).find(m => m.id === 'prs-week')!
    expect(m.text).toContain('3 PRs')
    expect(m.text).toContain('landed this week')
  })

  it('skips prs-week when < 2 PRs this week', () => {
    const prs = makePrDays([{ date: TODAY, count: 1 }])
    expect(generateInsights(null, prs, [], []).find(m => m.id === 'prs-week')).toBeUndefined()
  })

  // --- this month ---

  it('generates tasks-month when >= 5 tasks', () => {
    const trends = makeTrends([
      { date: MONTH_DAY, tasks: 3 },  // Apr 2 (this month, prior week)
      { date: TODAY, tasks: 3 },       // Apr 6
    ])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'tasks-month')!
    expect(m.text).toContain('6 tasks')
    expect(m.text).toContain('this month')
  })

  it('skips tasks-month when < 5 tasks', () => {
    const trends = makeTrends([{ date: TODAY, tasks: 2 }])
    expect(generateInsights(trends, [], [], []).find(m => m.id === 'tasks-month')).toBeUndefined()
  })

  it('generates subtasks-month when >= 10', () => {
    const trends = makeTrends([
      { date: MONTH_DAY, subtasks: 6 },
      { date: TODAY, subtasks: 5 },
    ])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'subtasks-month')!
    expect(m.text).toContain('11 subtasks')
    expect(m.text).toContain('this month')
  })

  it('generates worker-hours-month when >= 10h', () => {
    const trends = makeTrends([
      { date: MONTH_DAY, workerH: 6 },
      { date: TODAY, workerH: 5 },
    ])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'worker-hours-month')!
    expect(m.text).toContain('11h')
    expect(m.text).toContain('this month')
  })

  it('generates prs-month when >= 3', () => {
    const prs = makePrDays([
      { date: MONTH_DAY, count: 2 },
      { date: TODAY, count: 1 },
    ])
    const m = generateInsights(null, prs, [], []).find(m => m.id === 'prs-month')!
    expect(m.text).toContain('3 PRs')
    expect(m.text).toContain('this month')
  })

  // --- total / cumulative ---

  it('generates multi-project when >= 2 projects have done tasks', () => {
    const tasks = [makeTask('a', 'done'), makeTask('b', 'done'), makeTask('c', 'done')]
    const m = generateInsights(null, [], tasks, []).find(m => m.id === 'multi-project')!
    expect(m.text).toContain('3 projects')
    expect(m.text).toContain('in parallel')
  })

  it('skips multi-project when < 2 projects', () => {
    const tasks = [makeTask('a', 'done')]
    expect(generateInsights(null, [], tasks, []).find(m => m.id === 'multi-project')).toBeUndefined()
  })

  it('generates total-done when >= 10 tasks', () => {
    const tasks = Array.from({ length: 12 }, (_, i) => makeTask(`p${i % 2}`, 'done'))
    const m = generateInsights(null, [], tasks, []).find(m => m.id === 'total-done')!
    expect(m.text).toContain('12 tasks')
    expect(m.text).toContain('how far')
  })

  it('skips total-done when < 10', () => {
    const tasks = Array.from({ length: 5 }, () => makeTask('a', 'done'))
    expect(generateInsights(null, [], tasks, []).find(m => m.id === 'total-done')).toBeUndefined()
  })

  it('generates total-subtasks when >= 20 subtasks done', () => {
    const tasks = [makeTask('a', 'done', 25)]
    const m = generateInsights(null, [], tasks, []).find(m => m.id === 'total-subtasks')!
    expect(m.text).toContain('25 subtasks')
    expect(m.text).toContain('small wins')
  })

  it('skips total-subtasks when < 20', () => {
    const tasks = [makeTask('a', 'done', 10)]
    expect(generateInsights(null, [], tasks, []).find(m => m.id === 'total-subtasks')).toBeUndefined()
  })

  // --- specific celebrations ---

  it('celebrates completed projects', () => {
    const projects = [makeProject('Lix Cleanups', 5, 5)]
    const m = generateInsights(null, [], [], projects).find(m => m.id.startsWith('project-done'))!
    expect(m.text).toContain('Lix Cleanups')
    expect(m.text).toContain('all done')
  })

  it('skips projects where not all tasks are done', () => {
    const projects = [makeProject('In Progress', 3, 5)]
    expect(generateInsights(null, [], [], projects).find(m => m.id.startsWith('project-done'))).toBeUndefined()
  })

  it('skips projects with zero tasks', () => {
    const projects = [makeProject('Empty', 0, 0)]
    expect(generateInsights(null, [], [], projects).find(m => m.id.startsWith('project-done'))).toBeUndefined()
  })

  it('limits completed projects to 2', () => {
    const projects = [makeProject('A', 1, 1), makeProject('B', 2, 2), makeProject('C', 3, 3)]
    const msgs = generateInsights(null, [], [], projects).filter(m => m.id.startsWith('project-done'))
    expect(msgs).toHaveLength(2)
  })

  it('celebrates recently completed top-level tasks', () => {
    const completions = [makeCompletion('RRO-13', 'Implement payment flow')]
    const m = generateInsights(null, [], [], [], completions).find(m => m.id.startsWith('task-done'))!
    expect(m.text).toContain('RRO-13')
    expect(m.text).toContain('Implement payment flow')
  })

  it('skips subtask completions', () => {
    const completions = [makeCompletion('RRO-13-1', 'Write tests', true)]
    expect(generateInsights(null, [], [], [], completions).find(m => m.id.startsWith('task-done'))).toBeUndefined()
  })

  it('limits completed tasks to 3', () => {
    const completions = [
      makeCompletion('T-1', 'A'), makeCompletion('T-2', 'B'),
      makeCompletion('T-3', 'C'), makeCompletion('T-4', 'D'),
    ]
    const msgs = generateInsights(null, [], [], [], completions).filter(m => m.id.startsWith('task-done'))
    expect(msgs).toHaveLength(3)
  })

  it('truncates long task titles', () => {
    const completions = [makeCompletion('T-1', 'This is a very long task title that should be truncated at word boundary')]
    const m = generateInsights(null, [], [], [], completions).find(m => m.id.startsWith('task-done'))!
    expect(m.text).toContain('...')
    expect(m.text.length).toBeLessThan(80)
  })

  it('celebrates merged PRs with titles', () => {
    const prs = makePrDays([{
      date: TODAY, count: 1,
      prs: [{ number: 456, title: 'Fix authentication bug' }],
    }])
    const m = generateInsights(null, prs, [], []).find(m => m.id.startsWith('pr-merged'))!
    expect(m.text).toContain('#456')
    expect(m.text).toContain('Fix authentication bug')
  })

  it('limits merged PR celebrations to 2', () => {
    const prs = makePrDays([{
      date: TODAY, count: 3,
      prs: [{ number: 1, title: 'A' }, { number: 2, title: 'B' }, { number: 3, title: 'C' }],
    }])
    const msgs = generateInsights(null, prs, [], []).filter(m => m.id.startsWith('pr-merged'))
    expect(msgs).toHaveLength(2)
  })

  it('celebrates when all subtasks of a parent task are done', () => {
    const tasks = [{
      ...makeTask('proj-a', 'done', 5),
      task_key: 'PAY-5' as string | null,
    }]
    const m = generateInsights(null, [], tasks, []).find(m => m.id.startsWith('subtasks-complete'))!
    expect(m.text).toContain('PAY-5')
    expect(m.text).toContain('All subtasks')
  })

  it('skips subtask celebration when not all subtasks done', () => {
    const tasks = [{
      ...makeTask('proj-a', 'in_progress'),
      subtask_stats: { total: 5, done: 3, in_progress: 2 },
    }]
    expect(generateInsights(null, [], tasks, []).find(m => m.id.startsWith('subtasks-complete'))).toBeUndefined()
  })

  it('celebrations appear before numeric messages', () => {
    const trends = makeTrends([{ date: TODAY, tasks: 5 }])
    const completions = [makeCompletion('T-1', 'Finished task')]
    const msgs = generateInsights(trends, [], [], [], completions)
    const celebIdx = msgs.findIndex(m => m.id.startsWith('task-done'))
    const numericIdx = msgs.findIndex(m => m.id === 'tasks-today')
    expect(celebIdx).toBeLessThan(numericIdx)
  })

  // --- rest reminder ---

  it('generates rest reminder when human hours > 4', () => {
    const trends = makeTrends([{ date: TODAY, humanH: 5.2 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'rest-reminder')!
    expect(m.type).toBe('rest-reminder')
    expect(m.text).toContain('5.2h')
  })

  it('no rest reminder when <= 4h', () => {
    const trends = makeTrends([{ date: TODAY, humanH: 3.9 }])
    expect(generateInsights(trends, [], [], []).find(m => m.id === 'rest-reminder')).toBeUndefined()
  })

  it('rest reminder is first in array', () => {
    const trends = makeTrends([{ date: TODAY, tasks: 3, humanH: 5.0 }])
    const msgs = generateInsights(trends, [], [], [])
    expect(msgs[0].type).toBe('rest-reminder')
    expect(msgs[1].type).toBe('positive')
  })

  it('rest reminder reassures agents keep working', () => {
    const trends = makeTrends([{ date: TODAY, humanH: 5.2 }])
    const text = generateInsights(trends, [], [], [])[0].text
    expect(
      text.includes('workers can keep going') || text.includes('agents will wait') || text.includes('still be here')
    ).toBe(true)
  })

  // --- statRange ---

  it('statRange correctly marks the highlighted portion', () => {
    const trends = makeTrends([{ date: TODAY, tasks: 5 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'tasks-today')!
    const [s, e] = m.statRange!
    expect(m.text.slice(s, e)).toBe('5 tasks')
  })

  // --- time window boundaries ---

  it('does not count yesterday (Sunday) in this week for Monday', () => {
    // Frozen to Monday — Sunday is prior week
    const trends = makeTrends([{ date: YESTERDAY, tasks: 10 }])
    expect(generateInsights(trends, [], [], []).find(m => m.id === 'tasks-week')).toBeUndefined()
  })

  it('counts earlier this month in monthly aggregation', () => {
    // Apr 2 is this month but prior week
    const trends = makeTrends([{ date: MONTH_DAY, workerH: 15 }])
    const m = generateInsights(trends, [], [], []).find(m => m.id === 'worker-hours-month')!
    expect(m.text).toContain('15h')
  })
})
