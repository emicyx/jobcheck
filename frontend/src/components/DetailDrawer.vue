<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { appsApi } from '../api'
import type { ApplicationDetail } from '../types'
import { useBoardStore } from '../stores/board'
import { fmtDate, fmtDateTime } from '../utils/format'
import AppFormModal from './AppFormModal.vue'

const props = defineProps<{ show: boolean; appId: number | null }>()
const emit = defineEmits<{ (e: 'update:show', v: boolean): void; (e: 'changed'): void }>()

const message = useMessage()
const store = useBoardStore()

const visible = computed({
  get: () => props.show,
  set: (v: boolean) => emit('update:show', v),
})

const detail = ref<ApplicationDetail | null>(null)
const loading = ref(false)
const editing = ref(false)

watch(
  () => [props.show, props.appId] as const,
  async ([show, id]) => {
    if (show && id) {
      loading.value = true
      try {
        detail.value = await appsApi.detail(id)
      } catch (e: any) {
        message.error(e?.message || '加载失败')
        visible.value = false
      } finally {
        loading.value = false
      }
    } else {
      detail.value = null
      editing.value = false
    }
  },
)

const timeline = computed(() => (detail.value ? [...detail.value.history].reverse() : []))

const statusOptions = computed<SelectOption[]>(() => {
  const groups: Record<string, { label: string; value: string }[]> = {
    progress: [],
    terminal: [],
    special: [],
  }
  for (const s of store.meta?.statuses ?? []) {
    const g = s.group === 'fallback' ? 'progress' : s.group
    ;(groups[g] ??= []).push({ label: s.label, value: s.key })
  }
  const titles: Record<string, string> = { progress: '进行阶段', terminal: '终态', special: '特殊' }
  return Object.entries(groups)
    .filter(([, children]) => children.length > 0)
    .map(([key, children]) => ({ type: 'group', label: titles[key] ?? key, key, children }))
})

async function quickChangeStatus(key: string | null) {
  if (!detail.value || !key || key === detail.value.current_status) return
  try {
    await appsApi.update(detail.value.id, { current_status: key })
    detail.value = await appsApi.detail(detail.value.id)
    message.success(`状态已更新为「${store.statusLabel(key)}」`)
    emit('changed')
  } catch (e: any) {
    message.error(e?.message || '更新失败')
  }
}

async function remove() {
  if (!detail.value) return
  await appsApi.remove(detail.value.id)
  message.success('已删除')
  visible.value = false
  emit('changed')
}

function onSaved() {
  editing.value = false
  emit('changed')
  if (props.appId) appsApi.detail(props.appId).then((d) => (detail.value = d))
}
</script>

<template>
  <n-drawer v-model:show="visible" :width="440" placement="right">
    <n-drawer-content :title="detail ? detail.company : '投递详情'" closable>
      <div v-if="detail" class="detail">
        <div class="head">
          <div class="job">{{ detail.job_title }}</div>
          <div class="meta">
            <span>{{ detail.batch }}</span>
            <span v-if="detail.department">· {{ detail.department }}</span>
            <span v-if="detail.work_location">· {{ detail.work_location }}</span>
            <span class="src-badge" :class="{ 'is-auto': detail.source === 'auto' }">
              {{ detail.source === 'auto' ? '自动' : '手动' }}
            </span>
          </div>
        </div>

        <div class="block">
          <div class="block-label">当前状态</div>
          <div class="current-status" :style="{ '--status-color': store.statusColor(detail.current_status) }">
            <span class="dot"></span>{{ store.statusLabel(detail.current_status) }}
            <span v-if="detail.raw_status_text" class="raw">原文：{{ detail.raw_status_text }}</span>
          </div>
        </div>

        <div class="block">
          <div class="block-label">状态时间线</div>
          <div class="station-line">
            <div
              v-for="h in timeline"
              :key="h.id"
              class="station-item"
              :style="{ '--status-color': store.statusColor(h.to_status) }"
            >
              <span class="station-dot"></span>
              <div>
                <div class="station-status">
                  {{ store.statusLabel(h.to_status) }}
                  <span v-if="h.from_status" class="station-time">
                    · 由「{{ store.statusLabel(h.from_status) }}」变更
                  </span>
                </div>
                <div class="station-time">{{ fmtDateTime(h.detected_at) }}</div>
                <div v-if="h.raw_status_text && h.raw_status_text !== store.statusLabel(h.to_status)" class="station-raw">
                  {{ h.raw_status_text }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="block">
          <div class="block-label">基础信息</div>
          <div class="kv"><span>投递日期</span><b>{{ fmtDate(detail.applied_at) }}</b></div>
          <div class="kv"><span>记录时间</span><b>{{ fmtDateTime(detail.created_at) }}</b></div>
          <div v-if="detail.tags.length" class="kv">
            <span>标签</span>
            <b class="tag-list">
              <span v-for="t in detail.tags" :key="t.id" :style="{ color: t.color }">{{ t.name }}</span>
            </b>
          </div>
          <div v-if="detail.note" class="kv"><span>备注</span><b>{{ detail.note }}</b></div>
        </div>
      </div>

      <div v-else class="loading-wrap">
        <n-spin v-if="loading" />
      </div>

      <AppFormModal v-model:show="editing" :initial="detail" @saved="onSaved" />

      <template #footer>
        <div v-if="detail" class="drawer-footer">
          <n-select
            :value="detail.current_status"
            :options="statusOptions"
            size="small"
            placeholder="快速改状态"
            style="flex: 1"
            @update:value="quickChangeStatus"
          />
          <n-button size="small" @click="editing = true">编辑</n-button>
          <n-popconfirm @positive-click="remove">
            <template #trigger>
              <n-button size="small" quaternary type="error">删除</n-button>
            </template>
            删除这条投递及其全部历史？
          </n-popconfirm>
        </div>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.detail { display: flex; flex-direction: column; gap: 22px; }
.head .job { font-size: 18px; font-weight: 600; }
.head .meta { color: var(--ink-2); font-size: 13px; margin-top: 4px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.block-label { font-size: 12px; color: var(--ink-3); letter-spacing: 0.08em; margin-bottom: 10px; }
.current-status { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 15px; flex-wrap: wrap; }
.current-status .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--status-color); flex: none; }
.current-status .raw { font-weight: 400; font-size: 12px; color: var(--ink-2); }
.kv { display: flex; gap: 12px; padding: 4px 0; font-size: 13px; }
.kv > span { color: var(--ink-3); flex: none; width: 64px; }
.tag-list { display: inline-flex; gap: 10px; flex-wrap: wrap; }
.loading-wrap { padding: 40px 0; text-align: center; color: var(--ink-3); }
.drawer-footer { display: flex; gap: 10px; align-items: center; }
</style>
