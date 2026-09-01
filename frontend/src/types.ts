export interface StatusMeta {
  key: string
  label: string
  group: 'progress' | 'fallback' | 'terminal' | 'special'
  order: number
  color: string
}

export interface Meta {
  statuses: StatusMeta[]
  batches: string[]
  default_status: string
  default_batch: string
}

export interface Tag {
  id: number
  name: string
  color: string
}

export interface Application {
  id: number
  source: 'manual' | 'auto'
  company: string
  job_title: string
  department: string | null
  work_location: string | null
  applied_at: string
  batch: string
  current_status: string
  raw_status_text: string | null
  note: string | null
  last_synced_at: string | null
  created_at: string
  updated_at: string
  tags: Tag[]
}

export interface HistoryItem {
  id: number
  from_status: string | null
  to_status: string
  raw_status_text: string | null
  detected_at: string
}

export interface ApplicationDetail extends Application {
  history: HistoryItem[]
}

export interface User {
  id: number
  email: string
  role: string
  created_at: string
}

export interface Portal {
  id: number
  name: string
  company: string
  provider_key: string
  domains: string[]
  verified: boolean
  note: string | null
}

export interface BindingPortal {
  id: number
  name: string
  company: string
}

export interface Binding {
  id: number
  portal: BindingPortal
  status: 'pending' | 'active' | 'expired' | 'paused'
  interval_hours: number
  last_check_at: string | null
  next_check_at: string | null
  cookie_updated_at: string | null
  last_error: string | null
  applications_count: number
}

export interface BindIntent {
  id: number
  token: string
  login_url: string
  session_cookie_names: string[]
  expires_at: string
}
