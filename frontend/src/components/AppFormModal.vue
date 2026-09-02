<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { appsApi } from '../api'
import type { Application } from '../types'
import { useBoardStore } from '../stores/board'

const props = defineProps<{
  show: boolean
  initial: Application | null // null = 新建
}>()
const emit = defineEmits<{ (e: 'update:show', v: boolean): void; (e: 'saved'): void }>()

const message = useMessage()
const store = useBoardStore()

const visible = computed({
  get: () => props.show,
  set: (v: boolean) => emit('update:show', v),
})

const submitting = ref(false)
const form = reactive({
  company: '',
  job_title: '',
  department: '',
  work_location: '',
  applied_at: '' as string | null,
  batch: '',
  current_status: '',
  raw_status_text: '',
  note: '',
  tag_ids: [] as number[],
})

watch(
  () => props.show,
  (v) => {
    if (!v) return
    const it = props.initial
    form.company = it?.company ?? ''
    form.job_title = it?.job_title ?? ''
    form.department = it?.department ?? ''
    form.work_location = it?.work_location ?? ''
    form.applied_at = it?.applied_at ?? new Date().toISOString().slice(0, 10)
    form.batch = it?.batch ?? store.meta?.default_batch ?? '正式批'
    form.current_status = it?.current_status ?? store.meta?.default_status ?? 'screening'
    form.raw_status_text = it?.raw_status_text ?? ''
    form.note = it?.note ?? ''
    form.tag_ids = it?.tags.map((t) => t.id) ?? []
  },
)

const batchOptions = computed<SelectOption[]>(() =>
  (store.meta?.batches ?? []).map((b) => ({ label: b, value: b })),
)

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

const tagOptions = computed<SelectOption[]>(() =>
  store.tags.map((t) => ({ label: t.name, value: t.id })),
)

async function submit() {
  if (!form.company.trim() || !form.job_title.trim() || !form.applied_at) {
    message.warning('公司、岗位和投递日期为必填项')
    return
  }
  submitting.value = true
  try {
    const body = {
      company: form.company.trim(),
      job_title: form.job_title.trim(),
      department: form.department.trim() || null,
      work_location: form.work_location.trim() || null,
      applied_at: form.applied_at,
      batch: form.batch,
      current_status: form.current_status,
      raw_status_text: form.raw_status_text.trim() || null,
      note: form.note.trim() || null,
      tag_ids: form.tag_ids,
    }
    if (props.initial) {
      await appsApi.update(props.initial.id, body)
      message.success('已保存')
    } else {
      await appsApi.create(body)
      message.success('已记录一条投递')
    }
    visible.value = false
    emit('saved')
  } catch (e: any) {
    message.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <n-modal v-model:show="visible" preset="card" :title="initial ? '编辑投递' : '记录一条投递'" class="app-form" :style="{ width: '560px', maxWidth: 'calc(100vw - 32px)' }">
    <n-form label-placement="left" label-width="84">
      <n-form-item label="公司" required>
        <n-input v-model:value="form.company" placeholder="如：腾讯" @keyup.enter="submit" />
      </n-form-item>
      <n-form-item label="岗位" required>
        <n-input v-model:value="form.job_title" placeholder="如：后端开发工程师" @keyup.enter="submit" />
      </n-form-item>
      <n-form-item label="部门">
        <n-input v-model:value="form.department" placeholder="如：CSIG" />
      </n-form-item>
      <n-form-item label="工作地">
        <n-input v-model:value="form.work_location" placeholder="如：深圳" />
      </n-form-item>
      <n-form-item label="投递日期" required>
        <n-date-picker v-model:formatted-value="form.applied_at" value-format="yyyy-MM-dd" type="date" style="width: 100%" />
      </n-form-item>
      <n-form-item label="批次">
        <n-select v-model:value="form.batch" :options="batchOptions" />
      </n-form-item>
      <n-form-item label="当前状态">
        <n-select v-model:value="form.current_status" :options="statusOptions" />
      </n-form-item>
      <n-form-item label="标签">
        <n-select v-model:value="form.tag_ids" multiple :options="tagOptions" placeholder="在设置中管理标签" clearable />
      </n-form-item>
      <n-form-item label="官网原文">
        <n-input v-model:value="form.raw_status_text" placeholder="官网显示的状态原文，可选" />
      </n-form-item>
      <n-form-item label="备注">
        <n-input v-model:value="form.note" type="textarea" :rows="2" placeholder="内推人、投递渠道等，可选" />
      </n-form-item>
    </n-form>
    <template #footer>
      <div style="display: flex; justify-content: flex-end; gap: 12px">
        <n-button @click="visible = false">取消</n-button>
        <n-button type="primary" :loading="submitting" @click="submit">保存</n-button>
      </div>
    </template>
  </n-modal>
</template>
