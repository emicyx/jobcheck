<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ items: { label: string; count: number; color?: string }[] }>()

const max = computed(() => Math.max(1, ...props.items.map((i) => i.count)))
</script>

<template>
  <div class="bar-list">
    <div v-if="items.length === 0" class="empty">暂无数据</div>
    <div v-for="item in items" :key="item.label" class="row" :title="`${item.label} × ${item.count}`">
      <span class="row-label">{{ item.label }}</span>
      <span class="row-track">
        <span
          class="row-bar"
          :style="{ width: (item.count / max) * 100 + '%', background: item.color ?? 'var(--brand)' }"
        ></span>
      </span>
      <span class="row-count">{{ item.count }}</span>
    </div>
  </div>
</template>

<style scoped>
.bar-list { display: flex; flex-direction: column; gap: 8px; }
.empty { color: var(--ink-3); font-size: 12px; }
.row { display: flex; align-items: center; gap: 10px; }
.row-label {
  width: 128px;
  flex: none;
  font-size: 12px;
  color: var(--ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: right;
}
.row-track { flex: 1; height: 12px; background: var(--brand-soft); border-radius: 6px; overflow: hidden; }
.row-bar { display: block; height: 100%; border-radius: 6px; opacity: 0.85; }
.row-count { width: 44px; flex: none; text-align: right; font-variant-numeric: tabular-nums; font-size: 12px; color: var(--ink-2); }
</style>
