<script setup lang="ts">
defineProps<{ label: string; value: string; sub?: string }>()
</script>

<template>
  <div class="metric-card">
    <div class="label">{{ label }}</div>
    <div class="value">{{ value }}</div>
    <div v-if="sub" class="sub">{{ sub }}</div>
  </div>
</template>

<style scoped>
.metric-card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 16px;
  min-width: 150px;
}
.label { font-size: 12px; color: var(--ink-3); letter-spacing: 0.04em; }
.value {
  font-size: 26px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  margin-top: 4px;
  line-height: 1.2;
}
.sub { font-size: 12px; color: var(--ink-2); margin-top: 4px; }
</style>
