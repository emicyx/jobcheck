<script setup lang="ts">
import type { Application } from '../types'
import { useBoardStore } from '../stores/board'
import { fmtDateTime } from '../utils/format'

defineProps<{ app: Application }>()
defineEmits<{ (e: 'open', id: number): void }>()

const store = useBoardStore()
</script>

<template>
  <article
    class="app-card"
    :style="{ '--status-color': store.statusColor(app.current_status) }"
    tabindex="0"
    @click="$emit('open', app.id)"
    @keydown.enter="$emit('open', app.id)"
  >
    <div class="company">
      {{ app.company }}
      <span class="src-badge" :class="{ 'is-auto': app.source === 'auto' }">
        {{ app.source === 'auto' ? '自动' : '手动' }}
      </span>
    </div>
    <div class="job">{{ app.job_title }}</div>
    <div class="meta">
      <span>{{ app.batch }}</span>
      <span v-if="app.department">· {{ app.department }}</span>
      <span v-if="app.work_location">· {{ app.work_location }}</span>
    </div>
    <div v-if="app.tags.length" class="meta tags">
      <span v-for="t in app.tags" :key="t.id" class="tag-chip" :style="{ color: t.color }">
        <span class="tag-dot" :style="{ background: t.color }"></span>{{ t.name }}
      </span>
    </div>
    <div class="time">更新于 {{ fmtDateTime(app.updated_at) }}</div>
  </article>
</template>

<style scoped>
.tags { gap: 10px; }
.tag-chip { display: inline-flex; align-items: center; gap: 4px; }
.tag-dot { width: 6px; height: 6px; border-radius: 50%; }
</style>
