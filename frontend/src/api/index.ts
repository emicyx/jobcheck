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

export const portalsApi = {
  list: () => http.get<Portal[]>('/portals'),
  identify: (url: string) => http.post<Portal | null>('/portals/identify', { url }),
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
  created_at: string
}

export const samplesApi = {
  createIntent: () => http.post<{ id: number; token: string; expires_at: string }>('/samples/intents'),
  mine: () => http.get<SampleBrief[]>('/samples/mine'),
}
