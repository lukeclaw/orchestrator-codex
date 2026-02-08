export interface Session {
  id: string
  name: string
  host: string
  mp_path: string | null
  tmux_window: string | null
  tunnel_pane: string | null
  status: 'idle' | 'working' | 'waiting' | 'paused' | 'error' | 'disconnected' | 'connecting'
  current_task_id: string | null
  created_at: string
  last_activity: string | null
}

export interface Decision {
  id: string
  session_id: string | null
  question: string
  options: string | string[] | null
  context: string | null
  urgency: 'low' | 'normal' | 'high' | 'critical'
  status: 'pending' | 'responded' | 'dismissed'
  response: string | null
  created_at: string
}

export interface Activity {
  id: string
  session_id: string | null
  event_type: string
  event_data: string | Record<string, unknown> | null
  created_at: string
}

export interface Project {
  id: string
  name: string
  description: string | null
  status: string
  target_date: string | null
  created_at: string
}

export interface TaskLink {
  url: string
  title: string
  type: string  // pr, doc, reference, etc.
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
  priority: number
  assigned_session_id: string | null
  parent_task_id: string | null
  notes: string | null
  links: TaskLink[]
  subtask_stats?: SubtaskStats
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface PullRequest {
  id: string
  url: string
  number: number | null
  title: string | null
  status: string
  session_id: string | null
}

export interface ContextItem {
  id: string
  scope: 'global' | 'project'
  project_id: string | null
  title: string
  content: string
  category: string | null
  source: string | null
  metadata: string | null
  created_at: string
  updated_at: string
}
