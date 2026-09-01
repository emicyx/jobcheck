import { defineStore } from 'pinia'
import { appsApi, bindingsApi, metaApi, tagsApi } from '../api'
import type { Application, Binding, Meta, Tag } from '../types'

export interface BoardFilters {
  q: string
  batch: string | null
  tagId: number | null
  source: string | null
  status: string | null
}

export const useBoardStore = defineStore('board', {
  state: () => ({
    meta: null as Meta | null,
    applications: [] as Application[],
    tags: [] as Tag[],
    bindings: [] as Binding[],
    filters: {
      q: '',
      batch: null,
      tagId: null,
      source: null,
      status: null,
    } as BoardFilters,
    loading: false,
  }),
  getters: {
    expiredBindings(state): Binding[] {
      return state.bindings.filter((b) => b.status === 'expired' || b.status === 'paused')
    },
    statusMap(state): Record<string, Meta['statuses'][number]> {
      const map: Record<string, Meta['statuses'][number]> = {}
      for (const s of state.meta?.statuses ?? []) map[s.key] = s
      return map
    },
  },
  actions: {
    async loadMeta() {
      if (!this.meta) this.meta = await metaApi.get()
    },
    async loadTags() {
      this.tags = await tagsApi.list()
    },
    async loadBindings() {
      this.bindings = await bindingsApi.list()
    },
    async loadApplications() {
      this.loading = true
      try {
        this.applications = await appsApi.list({
          q: this.filters.q || undefined,
          batch: this.filters.batch ?? undefined,
          tag_id: this.filters.tagId ?? undefined,
          source: this.filters.source ?? undefined,
          status: this.filters.status ?? undefined,
        })
      } finally {
        this.loading = false
      }
    },
    statusLabel(key: string): string {
      return this.statusMap[key]?.label ?? key
    },
    statusColor(key: string): string {
      return this.statusMap[key]?.color ?? '#9aa4b0'
    },
  },
})
