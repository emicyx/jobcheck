<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import GuideModal from './GuideModal.vue'

const router = useRouter()
const auth = useAuthStore()
const guideShow = ref(false)

async function logout() {
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header class="app-header">
    <div class="left" role="link" tabindex="0" @click="router.push({ name: 'board' })" @keydown.enter="router.push({ name: 'board' })">
      <span class="brand-dot" aria-hidden="true"></span>
      <span class="brand-name">JobCheck</span>
      <span class="tagline">秋招投递追踪</span>
    </div>
    <nav class="nav">
      <router-link :to="{ name: 'board' }">看板</router-link>
      <router-link :to="{ name: 'settings' }">设置</router-link>
      <a class="guide-link" @click="guideShow = true">指南</a>
      <span class="email">{{ auth.user?.email }}</span>
      <n-button quaternary size="small" @click="logout">登出</n-button>
    </nav>
    <GuideModal v-model:show="guideShow" />
  </header>
</template>

<style scoped>
.app-header {
  height: 56px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  border-bottom: 1px solid var(--line);
  background: var(--card);
}
.left { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.left:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; border-radius: 4px; }
.brand-dot {
  width: 12px; height: 12px; border-radius: 4px;
  background: linear-gradient(135deg, #6188d8 0%, #3e9e8c 50%, #d89c2e 100%);
}
.brand-name { font-weight: 600; letter-spacing: 0.14em; color: var(--brand); }
.tagline { color: var(--ink-3); font-size: 12px; }
.nav { display: flex; align-items: center; gap: 18px; }
.nav a { color: var(--ink-2); text-decoration: none; padding: 4px 2px; }
.nav a.router-link-active { color: var(--ink); font-weight: 600; border-bottom: 2px solid var(--brand); }
.guide-link { cursor: pointer; }
.guide-link:hover { color: var(--ink); }
.email { color: var(--ink-3); font-size: 12px; }
</style>
