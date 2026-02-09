export interface Session {
  id: string
  name: string
  host: string
  work_dir: string | null
  tmux_window: string | null
  tunnel_pane: string | null
  status: 'idle' | 'working' | 'waiting' | 'paused' | 'error' | 'disconnected' | 'connecting'
  created_at: string
  last_activity: string | null
  session_type: 'worker' | 'brain' | 'system'
}

export interface Activity {
  id: string
  session_id: string | null
  event_type: string
  event_data: string | Record<string, unknown> | null
  created_at: string
}

export interface ProjectStats {
  tasks: {
    total: number
    todo: number
    in_progress: number
    done: number
    blocked: number
  }
  workers: {
    total: number
    working: number
    idle: number
    waiting: number
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
  started_at: string | null
  completed_at: string | null
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
