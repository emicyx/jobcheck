<script setup lang="ts">
import { computed } from 'vue'
import type { StageColumn } from '../utils/stages'
import { useBoardStore } from '../stores/board'
import AppCard from './AppCard.vue'

const props = defineProps<{ col: StageColumn; collapsible?: boolean }>()
const emit = defineEmits<{ (e: 'open', id: number): void; (e: 'collapse'): void }>()

const store = useBoardStore()

const visibleChips = computed(() => props.col.statuses.filter((s) => s.count > 0))
const isFiltered = computed(() => {
  const cur = store.filters.status
  return !!cur && props.col.statuses.some((s) => s.key === cur)
})

function toggleStatus(key: string) {
  store.filters.status = store.filters.status === key ? null : key
}
</script>

<template>
  <section
    class="board-col"
    :class="{ 'is-terminal': col.terminal, 'is-filtered': isFiltered }"
    :style="{ '--col-color': col.color }"
    :data-statuses="col.statuses.map((s) => s.key).join(' ')"
    :aria-label="col.label"
  >
    <header class="board-col-head">
      <span class="col-dot" :style="{ background: col.color }"></span>
      <span class="board-col-title">{{ col.label }}</span>
      <span class="board-col-count">{{ col.total }}</span>
      <button
        v-if="collapsible"
        class="col-collapse-btn"
        title="收起此列"
        aria-label="收起此列"
        @click="emit('collapse')"
      >»</button>
    </header>

    <div v-if="visibleChips.length" class="board-col-sub" aria-label="按细分状态筛选">
      <button
        v-for="st in visibleChips"
        :key="st.key"
        class="sub-chip"
        :class="{ 'is-active': store.filters.status === st.key }"
        :style="{ '--chip-color': st.color }"
        :title="`${st.label} ${st.count} 条 · 点击筛选`"
        @click="toggleStatus(st.key)"
      >
        {{ st.short }}<em>{{ st.count }}</em>
      </button>
    </div>

    <div class="board-col-body">
      <AppCard
        v-for="app in col.apps"
        :key="app.id"
        :app="app"
        :show-status="col.multi"
        @open="emit('open', app.id)"
      />
      <div v-if="col.apps.length === 0" class="board-col-empty">暂无</div>
    </div>
  </section>
</template>
