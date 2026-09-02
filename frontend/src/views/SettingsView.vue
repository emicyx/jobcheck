<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { accountApi, bindingsApi, extApi, tagsApi } from '../api'
import type { ConnectedSite } from '../api'
import { useBoardStore } from '../stores/board'
import { useAuthStore } from '../stores/auth'
import { useBindFlow } from '../composables/useBindFlow'
import { fmtDateTime, parseServerDate } from '../utils/format'
import type { Binding } from '../types'
import AppHeader from '../components/AppHeader.vue'

const message = useMessage()
const router = useRouter()
const board = useBoardStore()
const auth = useAuthStore()

// ── 扩展同步（新链路：手动快照建档 + 每小时后台自动同步）──
const pairCode = ref<string | null>(null)
const pairExpiresAt = ref<string | null>(null)
const pairing = ref(false)
const nowTs = ref(Date.now())
const sites = ref<ConnectedSite[]>([])

let tickTimer: ReturnType<typeof setInterval> | null = null
function ensureTick() {
  if (tickTimer) return
  tickTimer = setInterval(() => {
    nowTs.value = Date.now()
    if (pairExpiresAt.value && parseServerDate(pairExpiresAt.value).getTime() < nowTs.value) {
      pairCode.value = null
      pairExpiresAt.value = null
    }
  }, 1000)
}
onBeforeUnmount(() => { if (tickTimer) clearInterval(tickTimer) })

const pairCountdown = computed(() => {
  if (!pairExpiresAt.value) return ''
  const sec = Math.max(0, Math.floor((parseServerDate(pairExpiresAt.value).getTime() - nowTs.value) / 1000))
  return `${Math.floor(sec / 60)} 分 ${String(sec % 60).padStart(2, '0')} 秒`
})

async function genPairCode() {
  pairing.value = true
  try {
    const res = await extApi.createPairCode()
    pairCode.value = res.code
    pairExpiresAt.value = res.expires_at
    nowTs.value = Date.now()
    ensureTick()
  } catch (e: any) {
    message.error(e?.message || '生成配对码失败')
  } finally {
    pairing.value = false
  }
}

async function loadSites() {
  try {
    sites.value = (await extApi.connectedSites()).sites
  } catch {
    /* 静默 */
  }
}
loadSites()

// ── 自动追踪绑定（旧链路，已停用）──
const bindings = ref<Binding[]>([])
const refreshingId = ref<number | null>(null)
const { phase: reloginPhase, start: startRelogin } = useBindFlow()

const STATUS_META: Record<string, { label: string; type: 'success' | 'warning' | 'error' | 'default' }> = {
  active: { label: '追踪中', type: 'success' },
  pending: { label: '待激活', type: 'warning' },
  expired: { label: '需重新登录', type: 'error' },
  paused: { label: '已暂停', type: 'warning' },
}

async function loadBindings() {
  try {
    bindings.value = await bindingsApi.list()
    board.bindings = bindings.value
  } catch {
    /* 静默 */
  }
}
loadBindings()

watch(reloginPhase, async (p) => {
  if (p === 'success') {
    message.success('重新登录成功，已恢复追踪')
    await loadBindings()
  }
})

async function refreshBinding(b: Binding) {
  refreshingId.value = b.id
  try {
    const res = await bindingsApi.refresh(b.id)
    message.success(`已同步：抓取 ${res.fetched} 条，新增 ${res.created}，更新 ${res.updated}`)
    await loadBindings()
    board.loadApplications().catch(() => {})
  } catch (e: any) {
    message.error(e?.message || '刷新失败')
    await loadBindings()
  } finally {
    refreshingId.value = null
  }
}

async function reloginBinding(b: Binding) {
  try {
    const intent = await bindingsApi.relogin(b.id)
    await startRelogin(intent)
  } catch (e: any) {
    message.error(e?.message || '发起重新登录失败')
  }
}

async function removeBinding(b: Binding) {
  await bindingsApi.remove(b.id)
  message.success(`已断开「${b.portal.name}」的自动追踪，历史记录保留并转为手动维护`)
  await loadBindings()
}

// ── 标签 ──
const PRESET_COLORS = [
  '#223a5e', '#6188d8', '#4aa8c0', '#3e9e8c', '#4f9e57',
  '#d89c2e', '#d97b28', '#c25a5a', '#c96a95', '#8a8f98',
]

const newTag = reactive({ name: '', color: PRESET_COLORS[1] })
const adding = ref(false)

async function addTag() {
  const name = newTag.name.trim()
  if (!name) {
    message.warning('标签名不能为空')
    return
  }
  adding.value = true
  try {
    await tagsApi.create({ name, color: newTag.color })
    newTag.name = ''
    await board.loadTags()
    message.success('标签已创建')
  } catch (e: any) {
    message.error(e?.message || '创建失败')
  } finally {
    adding.value = false
  }
}

async function renameTag(id: number, name: string) {
  try {
    await tagsApi.update(id, { name })
    await board.loadTags()
  } catch (e: any) {
    message.error(e?.message || '重命名失败')
    await board.loadTags()
  }
}

async function recolorTag(id: number, color: string) {
  await tagsApi.update(id, { color }).catch(() => message.error('保存颜色失败'))
  await board.loadTags()
}

async function removeTag(id: number) {
  await tagsApi.remove(id)
  await board.loadTags()
  message.success('标签已删除')
}

board.loadTags().catch(() => {})

const deleteShow = ref(false)
const deletePassword = ref('')
const deleting = ref(false)

async function deleteAccount() {
  if (!deletePassword.value) {
    message.warning('请输入密码确认')
    return
  }
  deleting.value = true
  try {
    await accountApi.remove(deletePassword.value)
    message.success('账号及全部数据已删除')
    auth.user = null
    auth.ready = true
    router.push({ name: 'login' })
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  } finally {
    deleting.value = false
    deleteShow.value = false
    deletePassword.value = ''
  }
}
</script>

<template>
  <div class="settings-page">
    <AppHeader />

    <div class="settings-body">
      <section class="panel">
        <h2>扩展同步 <n-tag size="small" round type="success" :bordered="false">推荐</n-tag></h2>
        <p class="hint">
          安装浏览器扩展并配对一次，之后到任意公司招聘站的「我的投递」页点插件「同步当前页」，投递记录即同步进看板并建立该站连接；
          已连接站点插件每小时静默刷新一次。Cookie 始终留在浏览器里。
        </p>

        <div class="pair-area">
          <div v-if="pairCode" class="pair-code-show">
            <div class="pair-code">{{ pairCode }}</div>
            <p class="hint" style="margin: 6px 0 0">{{ pairCountdown }} 内有效 · 在插件弹窗中输入即可完成配对</p>
          </div>
          <n-button v-else size="small" type="primary" :loading="pairing" @click="genPairCode">生成配对码</n-button>
        </div>

        <ol class="pair-steps">
          <li>安装/更新插件：未安装时在插件页「加载已解压」<code>extension/</code> 目录（或从「接入追踪」下载，版本需 ≥ 0.5.1），装完刷新招聘站页面</li>
          <li>点浏览器工具栏的 JobCheck 图标，在面板里输入上方配对码</li>
          <li>打开目标公司招聘站的「我的投递 / 应聘进度」页，等几秒——卡片自动出现即接入成功</li>
        </ol>

        <template v-if="sites.length">
          <h3 class="sites-title">已连接站点</h3>
          <div class="binding-list">
            <div v-for="s in sites" :key="s.portal_id" class="binding-row">
              <div class="binding-main">
                <b>{{ s.name }}</b>
                <n-tag size="small" round :type="s.login_suspect ? 'warning' : 'success'" :bordered="false">
                  {{ s.login_suspect ? '疑似未登录' : '正常' }}
                </n-tag>
                <span class="binding-meta">
                  {{ s.domain }}<template v-if="s.last_at"> · 最近同步 {{ fmtDateTime(s.last_at) }}</template>
                </span>
              </div>
              <div class="binding-actions">
                <n-button size="small" tag="a" :href="s.url" target="_blank">打开投递页</n-button>
              </div>
            </div>
          </div>
          <p class="hint" style="margin-bottom: 0">「疑似未登录」的站点：打开它的投递页重新登录一次即可恢复自动同步。</p>
        </template>
      </section>

      <section class="panel">
        <h2>自动追踪（旧版服务端轮询）<n-tag size="small" round type="default" :bordered="false">已停用</n-tag></h2>
        <p class="hint">旧链路（上传登录 Cookie 由平台轮询）已停用，不再自动同步；历史绑定数据保留备查。新接入请用上方「扩展同步」。</p>

        <div v-if="bindings.length" class="binding-list">
          <div v-for="b in bindings" :key="b.id" class="binding-row">
            <div class="binding-main">
              <b>{{ b.portal.name }}</b>
              <n-tag size="small" round :type="STATUS_META[b.status]?.type ?? 'default'" :bordered="false">
                {{ STATUS_META[b.status]?.label ?? b.status }}
              </n-tag>
              <span class="binding-meta">
                {{ b.applications_count }} 条投递
                <template v-if="b.last_check_at">· 上次同步 {{ fmtDateTime(b.last_check_at) }}</template>
              </span>
              <div v-if="b.last_error" class="binding-error">{{ b.last_error }}</div>
            </div>
            <div class="binding-actions">
              <n-button
                v-if="b.status === 'active' || b.status === 'paused'"
                size="small"
                :loading="refreshingId === b.id"
                @click="refreshBinding(b)"
              >立即同步</n-button>
              <n-button
                v-if="b.status !== 'pending'"
                size="small"
                :type="b.status === 'expired' ? 'primary' : 'default'"
                secondary
                @click="reloginBinding(b)"
              >{{ b.status === 'expired' ? '重新登录' : '换号重登' }}</n-button>
              <n-popconfirm @positive-click="removeBinding(b)">
                <template #trigger>
                  <n-button size="small" quaternary type="error">断开</n-button>
                </template>
                断开自动追踪？已同步的记录会保留并转为手动维护。
              </n-popconfirm>
            </div>
          </div>
        </div>
        <n-empty v-else description="还没有接入任何门户，在看板页点「接入追踪」开始" style="padding: 24px 0" />
      </section>

      <section class="panel">
        <h2>标签</h2>
        <p class="hint">标签用于在看板上二次分类（如「高优」「技术岗」「内推」），可与批次、来源叠加筛选。</p>

        <div class="tag-add">
          <n-input v-model:value="newTag.name" size="small" placeholder="新标签名" style="width: 180px" maxlength="32" @keyup.enter="addTag" />
          <div class="swatches">
            <button
              v-for="c in PRESET_COLORS"
              :key="c"
              class="swatch"
              :class="{ active: newTag.color === c }"
              :style="{ background: c }"
              :aria-label="`选择颜色 ${c}`"
              @click="newTag.color = c"
            ></button>
          </div>
          <n-button size="small" type="primary" :loading="adding" @click="addTag">新增标签</n-button>
        </div>

        <div v-if="board.tags.length" class="tag-list">
          <div v-for="t in board.tags" :key="t.id" class="tag-row">
            <span class="tag-dot" :style="{ background: t.color }"></span>
            <n-input
              :default-value="t.name"
              size="small"
              style="width: 180px"
              maxlength="32"
              @change="(v: string) => renameTag(t.id, v)"
            />
            <div class="swatches">
              <button
                v-for="c in PRESET_COLORS"
                :key="c"
                class="swatch"
                :class="{ active: t.color === c }"
                :style="{ background: c }"
                :aria-label="`改色 ${c}`"
                @click="recolorTag(t.id, c)"
              ></button>
            </div>
            <n-popconfirm @positive-click="removeTag(t.id)">
              <template #trigger>
                <n-button size="tiny" quaternary type="error">删除</n-button>
              </template>
              删除标签「{{ t.name }}」？投递记录本身不受影响。
            </n-popconfirm>
          </div>
        </div>
        <n-empty v-else description="还没有标签" style="padding: 24px 0" />
      </section>

      <section class="panel">
        <h2>账号</h2>
        <div class="kv"><span>邮箱</span><b>{{ auth.user?.email }}</b></div>
        <div class="kv"><span>角色</span><b>{{ auth.user?.role === 'admin' ? '管理员' : '用户' }}</b></div>

        <div class="danger-zone">
          <div>
            <b>注销账号</b>
            <p class="hint">删除账号、全部投递记录、状态历史与标签，不可恢复。</p>
          </div>
          <n-button size="small" type="error" secondary @click="deleteShow = true">注销账号</n-button>
        </div>
      </section>
    </div>

    <n-modal v-model:show="deleteShow" preset="card" title="确认注销账号" :style="{ width: '380px' }">
      <p style="color: var(--ink-2); margin-top: 0">此操作将删除你的全部数据且不可恢复。输入密码确认：</p>
      <n-input v-model:value="deletePassword" type="password" show-password-on="click" placeholder="密码" @keyup.enter="deleteAccount" />
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 12px">
          <n-button size="small" @click="deleteShow = false">取消</n-button>
          <n-button size="small" type="error" :loading="deleting" @click="deleteAccount">确认注销</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.settings-page { height: 100%; display: flex; flex-direction: column; }
.settings-body { max-width: 760px; width: 100%; margin: 0 auto; padding: 28px 24px 60px; display: flex; flex-direction: column; gap: 20px; }
.panel { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 22px 26px; }
.panel h2 { margin: 0 0 4px; font-size: 16px; }
.hint { color: var(--ink-3); font-size: 13px; margin: 0 0 16px; }
.tag-add { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 18px; }
.tag-list { display: flex; flex-direction: column; gap: 10px; }
.tag-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.tag-dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.swatches { display: flex; gap: 6px; flex-wrap: wrap; }
.swatch {
  width: 16px; height: 16px; border-radius: 50%;
  border: 2px solid var(--card); cursor: pointer; padding: 0;
  box-shadow: 0 0 0 1px var(--line);
}
.swatch.active { box-shadow: 0 0 0 2px var(--ink); }
.swatch:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.kv { display: flex; gap: 14px; padding: 5px 0; font-size: 14px; }
.kv span { color: var(--ink-3); width: 48px; flex: none; }
.pair-area { margin: 4px 0 12px; }
.pair-code-show { text-align: center; padding: 10px 0 4px; }
.pair-code {
  font-size: 34px; font-weight: 700; letter-spacing: .3em; text-indent: .3em;
  font-variant-numeric: tabular-nums; color: var(--ink);
}
.pair-steps { color: var(--ink-3); font-size: 13px; margin: 0 0 4px; padding-left: 20px; display: grid; gap: 6px; }
.pair-steps code { background: var(--line); border-radius: 4px; padding: 0 4px; font-size: 12px; }
.sites-title { margin: 18px 0 0; font-size: 14px; }
.danger-zone {
  margin-top: 22px; padding: 16px;
  border: 1px solid #f0d5d5; background: #fdf7f7; border-radius: 10px;
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
}
.binding-list { display: flex; flex-direction: column; }
.binding-row {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 14px 0; border-bottom: 1px solid var(--line); flex-wrap: wrap;
}
.binding-row:last-child { border-bottom: none; }
.binding-main { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.binding-meta { color: var(--ink-3); font-size: 12px; font-variant-numeric: tabular-nums; }
.binding-error { width: 100%; color: #c25a5a; font-size: 12px; }
.binding-actions { display: flex; gap: 8px; align-items: center; }
</style>
