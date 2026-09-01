<script setup lang="ts">
import { computed } from 'vue'
import { useBoardStore } from '../stores/board'

const store = useBoardStore()

const segments = computed(() => {
  const counts = new Map<string, number>()
  for (const a of store.applications) counts.set(a.current_status, (counts.get(a.current_status) ?? 0) + 1)
  const total = store.applications.length
  const segs = (store.meta?.statuses ?? [])
    .map((s) => ({ ...s, count: counts.get(s.key) ?? 0 }))
    .filter((s) => s.count > 0)
  return { segs, total }
})

function toggle(key: string) {
  store.filters.status = store.filters.status === key ? null : key
}
</script>

<template>
  <div class="status-bar-wrap">
    <div class="status-bar" role="listbox" aria-label="投递状态分布">
      <div
        v-for="seg in segments.segs"
        :key="seg.key"
        class="status-bar-seg"
        :class="{ 'is-active': store.filters.status === seg.key }"
        :style="{ width: (seg.count / segments.total) * 100 + '%', background: seg.color }"
        role="option"
        :aria-selected="store.filters.status === seg.key"
        :title="`${seg.label} × ${seg.count}（点击筛选）`"
        @click="toggle(seg.key)"
      ></div>
    </div>
    <div class="status-bar-caption">
      <span>共 {{ segments.total }} 条投递</span>
      <span v-if="store.filters.status" class="filtering">
        正在筛选「{{ store.statusLabel(store.filters.status) }}」
        <a @click="store.filters.status = null">清除</a>
      </span>
    </div>
  </div>
</template>

<style scoped>
.status-bar-wrap { padding: 14px 24px 0; }
.status-bar-caption {
  display: flex; justify-content: space-between;
  font-size: 12px; color: var(--ink-3); margin-top: 6px;
  font-variant-numeric: tabular-nums;
}
.status-bar-caption a { color: var(--brand); cursor: pointer; }
</style>
