import { defineStore } from 'pinia'
import { authApi } from '../api'
import type { User } from '../types'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    ready: false,
  }),
  actions: {
    async init() {
      try {
        this.user = await authApi.me()
      } catch {
        this.user = null
      } finally {
        this.ready = true
      }
    },
    async logout() {
      await authApi.logout().catch(() => {})
      this.user = null
    },
  },
})
