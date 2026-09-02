import { http } from './client'
import type { Application, ApplicationDetail, Binding, BindIntent, Meta, Portal, Tag, User } from '../types'

export const authApi = {
  register: (body: { email: string; password: string; invite_code: string }) =>
    http.post<User>('/auth/register', body),
  login: (body: { email: string; password: string }) => http.post<User>('/auth/login', body),
  logout: () => http.post<{ ok: boolean }>('/auth/logout'),
  me: () => http.get<User>('/auth/me'),
}

export const appsApi = {
  list: (params: Record<string, string | number | undefined>) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== '') qs.set(k, String(v))
    }
    const q = qs.toString()
    return http.get<Application[]>('/applications' + (q ? `?${q}` : ''))
  },
  detail: (id: number) => http.get<ApplicationDetail>(`/applications/${id}`),
  create: (body: Record<string, unknown>) => http.post<Application>('/applications', body),
  update: (id: number, body: Record<string, unknown>) =>
    http.patch<Application>(`/applications/${id}`, body),
  remove: (id: number) => http.delete<{ ok: boolean }>(`/applications/${id}`),
}

export const tagsApi = {
  list: () => http.get<Tag[]>('/tags'),
  create: (body: { name: string; color?: string }) => http.post<Tag>('/tags', body),
  update: (id: number, body: { name?: string; color?: string }) => http.patch<Tag>(`/tags/${id}`, body),
  remove: (id: number) => http.delete<{ ok: boolean }>(`/tags/${id}`),
}

export const metaApi = {
  get: () => http.get<Meta>('/meta'),
}

export const accountApi = {
  remove: (password: string) => http.delete<{ ok: boolean }>('/account', { password }),
}

// ── 个人数据（看板左侧「我的数据」侧边栏） ──

export interface MyStats {
  total: number
  in_progress: number
  terminal: number
  month_new: number
  by_status: { key: string; label: string; color: string; count: number }[]
}

export const statsApi = {
  mine: () => http.get<MyStats>('/me/stats'),
}

export const portalsApi = {
  list: () => http.get<Portal[]>('/portals'),
  identify: (url: string) => http.post<Portal | null>('/portals/identify', { url }),
}

export interface ConnectedSite {
  portal_id: number
  name: string
  domain: string
  url: string
  parsed_count: number
  parse_status: string
  login_suspect: boolean
  last_at: string | null
}

export const extApi = {
  /** 生成 6 位扩展配对码（10 分钟有效，一次性） */
  createPairCode: () =>
    http.post<{ code: string; expires_at: string }>('/ext/pair-code'),
  /** 已连接站点（扩展访问时快照建档的门户） */
  connectedSites: () => http.get<{ sites: ConnectedSite[] }>('/portals/connected'),
}

export const bindingsApi = {
  list: () => http.get<Binding[]>('/bindings'),
  create: (portalId: number) => http.post<BindIntent>('/bindings', { portal_id: portalId }),
  intentStatus: (token: string) =>
    http.get<{ status: string; binding_id?: number; synced?: boolean; detail?: string | null }>(
      `/bindings/intents/${token}`,
    ),
  refresh: (id: number) => http.post<{ ok: boolean; fetched: number; created: number; updated: number }>(`/bindings/${id}/refresh`),
  relogin: (id: number) => http.post<BindIntent>(`/bindings/${id}/relogin`),
  remove: (id: number) => http.delete<{ ok: boolean }>(`/bindings/${id}`),
}

export interface SampleBrief {
  id: number
  url: string | null
  status: string
  portal_id: number | null
  pipeline_status: string | null
  pipeline_note: string | null
  created_at: string
}

export const samplesApi = {
  createIntent: () => http.post<{ id: number; token: string; expires_at: string }>('/samples/intents'),
  mine: () => http.get<SampleBrief[]>('/samples/mine'),
}

// ── 管理后台（role=admin 可见，纯只读 + 快照干跑重解析） ──

export interface AdminOverview {
  users_total: number
  applications_total: number
  snapshots_total: number
  window: {
    days: string[]
    new_users: number[]
    new_applications: number[]
    snapshots: number[]
    snapshots_parsed: number[]
    llm_cost_cny: number[]
    capture_ok_rate: number | null
    parse_rate: number | null
  }
  llm: { month_cost_cny: number; budget_cny: number }
}

export interface AdminUserRow {
  id: number
  email: string
  role: string
  created_at: string
  applications_count: number
  snapshots_count: number
  sites_count: number
  last_active_at: string | null
}

export interface AdminAppStats {
  total: number
  by_source: Record<string, number>
  by_status: { key: string; label: string; color: string; count: number }[]
  top_companies: { company: string; count: number }[]
  top_portals: { portal_id: number; portal_name: string; count: number }[]
}

export interface AdminLlmCall {
  id: number
  task: string
  provider: string
  model: string
  prompt_version: string
  tokens_in: number
  tokens_out: number
  cost_cny: number
  latency_ms: number
  ok: boolean
  error: string | null
  created_at: string
}

export interface AdminSnapshotRow {
  id: number
  user_email: string
  url: string
  domain: string
  portal_id: number | null
  portal_name: string | null
  parse_status: string
  parse_route: string | null
  parsed_count: number
  login_suspect: boolean
  parse_note: string | null
  created_at: string
}

export interface AdminSnapshotStats {
  total: number
  parsed: number
  capture_ok: number
  login_suspect: number
  by_domain: Record<string, { total: number; parsed: number; capture_ok: number; login_suspect: number }>
  by_route: Record<string, number>
}

export const adminApi = {
  overview: () => http.get<AdminOverview>('/admin/overview'),
  users: () => http.get<AdminUserRow[]>('/admin/users'),
  appStats: () => http.get<AdminAppStats>('/admin/applications-stats'),
  llmUsage: () => http.get<{ month_cost_cny: number; budget_cny: number }>('/admin/llm-usage'),
  llmCalls: () => http.get<AdminLlmCall[]>('/admin/llm-calls'),
  snapshots: () => http.get<AdminSnapshotRow[]>('/admin/snapshots'),
  snapshotStats: () => http.get<AdminSnapshotStats>('/admin/snapshots/stats'),
  reparse: (id: number) =>
    http.post<{ status: string; parsed_count: number; route: string | null; portal_id: number | null; note: string | null }>(
      `/admin/snapshots/${id}/reparse`,
    ),
}
