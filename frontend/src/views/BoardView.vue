<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { SelectOption } from 'naive-ui'
import { useBoardStore } from '../stores/board'
import AppHeader from '../components/AppHeader.vue'
import StatusBar from '../components/StatusBar.vue'
import AppCard from '../components/AppCard.vue'
import DetailDrawer from '../components/DetailDrawer.vue'
import AppFormModal from '../components/AppFormModal.vue'
import ConnectWizard from '../components/ConnectWizard.vue'
import type { Application } from '../types'

const store = useBoardStore()

const creating = ref(false)
const wizardShow = ref(false)
const drawerShow = ref(false)
const drawerAppId = ref<number | null>(null)

let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => store.filters.q,
  () => {
    clearTimeout(searchTimer)
    searchTimer = setTimeout(() => store.loadApplications(), 300)
  },
)
watch(
  () => [store.filters.batch, store.filters.tagId, store.filters.source, store.filters.status] as const,
  () => store.loadApplications(),
)

onMounted(async () => {
  await Promise.all([store.loadMeta(), store.loadTags()])
  await Promise.all([store.loadApplications(), store.loadBindings().catch(() => {})])
})

const columns = computed(() =>
  [...(store.meta?.statuses ?? [])]
    .sort((a, b) => a.order - b.order)
    .map((s) => ({
      ...s,
      apps: store.applications.filter((a) => a.current_status === s.key),
    })),
)

const tagFilterOptions = computed<SelectOption[]>(() =>
  store.tags.map((t) => ({ label: t.name, value: t.id })),
)

const batchFilterOptions = computed<SelectOption[]>(() =>
  (store.meta?.batches ?? []).map((b) => ({ label: b, value: b })),
)

function openDetail(id: number) {
  drawerAppId.value = id
  drawerShow.value = true
}

function refresh() {
  store.loadApplications()
  store.loadBindings().catch(() => {})
}

function hasActiveFilters(): boolean {
  const f = store.filters
  return !!(f.q || f.batch || f.tagId || f.source || f.status)
}

function clearFilters() {
  store.filters.q = ''
  store.filters.batch = null
  store.filters.tagId = null
  store.filters.source = null
  store.filters.status = null
}
</script>

<template>
  <div class="board-page">
    <AppHeader />

    <StatusBar />

    <!-- 登录态失效提醒（M2）-->
    <n-alert
      v-if="store.expiredBindings.length"
      type="warning"
      class="expired-banner"
      :show-icon="true"
    >
      {{ store.expiredBindings.length }} 个公司的登录态已失效，自动追踪暂停：
      <span v-for="(b, i) in store.expiredBindings" :key="b.id">
        {{ b.portal.name }}<span v-if="i < store.expiredBindings.length - 1">、</span>
      </span>
      —— 请到「设置 → 自动追踪」重新登录。
    </n-alert>

    <div class="toolbar">
      <n-input
        :value="store.filters.q"
        size="small"
        clearable
        placeholder="搜索公司 / 岗位 / 部门"
        style="width: 220px"
        @update:value="(v: string) => (store.filters.q = v)"
      />
      <n-select
        v-model:value="store.filters.batch"
        size="small"
        clearable
        placeholder="批次"
        :options="batchFilterOptions"
        style="width: 120px"
      />
      <n-select
        v-model:value="store.filters.tagId"
        size="small"
        clearable
        placeholder="标签"
        :options="tagFilterOptions"
        style="width: 140px"
      />
      <n-select
        v-model:value="store.filters.source"
        size="small"
        clearable
        placeholder="来源"
        :options="[
          { label: '手动', value: 'manual' },
          { label: '自动', value: 'auto' },
        ]"
        style="width: 110px"
      />
      <n-button v-if="hasActiveFilters()" size="small" quaternary @click="clearFilters">清除筛选</n-button>
      <div style="flex: 1"></div>
      <n-button size="small" tertiary @click="wizardShow = true">接入追踪</n-button>
      <n-button type="primary" size="small" @click="creating = true">+ 记录投递</n-button>
    </div>

    <div v-if="store.applications.length === 0 && !store.loading" class="empty-wrap">
      <n-empty description="还没有投递记录">
        <template #extra>
          <n-button size="small" type="primary" @click="creating = true">记录第一条投递</n-button>
        </template>
      </n-empty>
    </div>

    <div v-else class="board-scroll">
      <div class="board-columns">
        <section
          v-for="col in columns"
          :key="col.key"
          class="board-col"
          :class="{ 'is-terminal': col.group === 'terminal' }"
          :aria-label="col.label"
        >
          <div class="board-col-head">
            <span class="col-dot" :style="{ background: col.color }"></span>
            <span class="board-col-title">{{ col.label }}</span>
            <span class="board-col-count">{{ col.apps.length }}</span>
          </div>
          <div class="board-col-body">
            <AppCard v-for="app in col.apps" :key="app.id" :app="app" @open="openDetail" />
          </div>
        </section>
      </div>
    </div>

    <DetailDrawer v-model:show="drawerShow" :app-id="drawerAppId" @changed="refresh" />
    <AppFormModal v-model:show="creating" :initial="null" @saved="refresh" />
    <ConnectWizard v-model:show="wizardShow" @bound="refresh" />
  </div>
</template>

<style scoped>
.board-page { height: 100%; display: flex; flex-direction: column; }
.expired-banner { margin: 14px 24px 0; }
.toolbar {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 14px 24px 0;
  flex-wrap: wrap;
}
.empty-wrap { flex: 1; display: flex; align-items: center; justify-content: center; }
.col-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
</style>
