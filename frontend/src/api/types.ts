export interface Session {
  id: string
  name: string
  host: string
  work_dir: string | null
  tunnel_pid: number | null
  status: 'idle' | 'working' | 'waiting' | 'paused' | 'disconnected' | 'connecting'
  created_at: string
  last_status_changed_at: string | null
  last_viewed_at: string | null
  session_type: 'worker' | 'brain' | 'system'
  preview?: string
  auto_reconnect: boolean
  rws_pty_id: string | null
  reconnect_step: string | null
}

export interface WorkerDetail {
  id: string
  name: string
  status: string
}

export interface ProjectStats {
  tasks: {
    total: number
    todo: number
    in_progress: number
    done: number
    blocked: number
  }
  subtasks: {
    total: number
    done: number
  }
  workers: {
    total: number
    working: number
    idle: number
    waiting: number
    details?: WorkerDetail[]
  }
  context: {
    total: number
  }
}

export interface Project {
  id: string
  name: string
  description: string | null
  status: string
  target_date: string | null
  created_at: string
  updated_at?: string
  stats?: ProjectStats
}

export interface TaskLink {
  url: string
  tag?: string  // optional free-form tag like "PR", "PRD", etc.
}

export interface SubtaskStats {
  total: number
  done: number
  in_progress: number
}

export interface Task {
  id: string
  project_id: string
  title: string
  description: string | null
  status: string
  priority: string  // H (High), M (Medium), L (Low)
  assigned_session_id: string | null
  parent_task_id: string | null
  notes: string | null
  links: TaskLink[]
  task_index: number | null
  task_key: string | null  // Human-readable key like "UTI-1" or "UTI-1-1"
  subtask_stats?: SubtaskStats
  created_at: string
  updated_at: string
}

export interface Rdev {
  name: string
  state: string
  cluster: string
  created: string
  last_accessed: string
  in_use: boolean
  worker_name?: string
  worker_status?: string
  worker_id?: string
}

export interface ContextItem {
  id: string
  scope: 'global' | 'project' | 'brain'
  project_id: string | null
  title: string
  description: string | null
  content?: string
  category: string | null
  source: string | null
  metadata: string | null
  created_at: string
  updated_at: string
}

export interface PrCommentThread {
  body: string
  file: string
  html_url: string | null
  original_lines: string | null
  created_at: string | null
  replies: { author: string; body: string; created_at: string | null }[]
}

export interface PrReview {
  reviewer: string
  state: 'approved' | 'changes_requested' | 'commented' | 'pending' | 'dismissed'
  submitted_at: string | null
  comments: number
  comment_threads: PrCommentThread[]
  html_url: string | null
}

export interface PrCheck {
  name: string
  status: 'completed' | 'in_progress' | 'queued' | 'pending'
  conclusion: 'success' | 'failure' | 'cancelled' | 'skipped' | 'timed_out' | 'neutral' | null
}

export interface PrChangedFile {
  filename: string
  status: string
  additions: number
  deletions: number
}

export interface PrPreviewData {
  title: string
  state: 'open' | 'closed' | 'merged'
  draft: boolean
  number: number
  repo: string
  author: string
  created_at: string
  updated_at: string
  closed_at: string | null
  closed_by: string | null
  merged_at: string | null
  merged_by: string | null
  additions: number
  deletions: number
  changed_files: number
  commits: number
  reviews: PrReview[]
  requested_reviewers: string[]
  checks: PrCheck[]
  auto_merge: boolean
  files: PrChangedFile[]
  fetched_at: string
}

export interface PrCommentMetadata {
  pr_title?: string
  reviewer_comment?: string
  reviewer_name?: string
  reviewer_commented_at?: string
  reply?: string
  reply_author?: string
  reply_commented_at?: string
}

export interface Notification {
  id: string
  task_id: string | null
  session_id: string | null
  message: string
  notification_type: 'info' | 'pr_comment' | 'warning'
  link_url: string | null
  metadata: PrCommentMetadata | null
  created_at: string
  dismissed: boolean
  dismissed_at: string | null
}

export interface ThroughputDay {
  date: string
  tasks: number
  subtasks: number
}

export interface HeatmapCell {
  day_of_week: number
  hour: number
  count: number
}

export interface WorkerHoursDay {
  date: string
  hours: number
}

export interface TrendsData {
  range: string
  throughput: ThroughputDay[]
  heatmap: HeatmapCell[]
  worker_hours: WorkerHoursDay[]
}

export interface ThroughputDetailItem {
  entity_id: string; is_subtask: boolean; timestamp: string
  title: string; task_key: string | null; status: string
  parent_task_id: string | null; parent_title: string | null; parent_task_key: string | null
}

export interface WorkerHoursDetailItem {
  session_id: string; session_name: string; total_hours: number
  intervals: { start: string; end: string }[]
  current_task: { id: string; title: string } | null
}

export interface HeatmapDetailItem {
  date: string; session_id: string; session_name: string; timestamp: string
}

export interface Skill {
  id: string
  name: string
  target: 'brain' | 'worker'
  type: 'built_in' | 'custom'
  description: string | null
  content: string | null
  line_count: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface PrSearchItem {
  url: string
  repo: string
  number: number
  title: string
  state: 'open' | 'closed'
  draft: boolean
  author: string
  created_at: string
  updated_at: string
  closed_at: string | null
  merged_at: string | null
  additions: number
  deletions: number
  changed_files: number
  review_decision: 'approved' | 'changes_requested' | 'review_required' | null
  review_requests: string[]
  auto_merge: boolean
  ci_state: 'success' | 'failure' | 'pending' | null
  mergeable: 'mergeable' | 'conflicting' | null
  attention_level: 1 | 2 | 3 | 4
  merged_by: string | null
  linked_task: { id: string; task_key: string | null; title: string; status: string } | null
  linked_worker: { id: string; name: string; status: string } | null
}

export interface PrSearchResponse {
  prs: PrSearchItem[]
}

export type TrendDetailSelection =
  | { chart: 'throughput'; date: string }
  | { chart: 'worker_hours'; date: string }
  | { chart: 'heatmap'; day_of_week: number; hour: number }
