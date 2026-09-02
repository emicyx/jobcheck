<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import AppHeader from '../components/AppHeader.vue'
import ChartBox from '../components/admin/ChartBox.vue'
import MetricCard from '../components/admin/MetricCard.vue'
import BarList from '../components/admin/BarList.vue'
import { adminApi } from '../api'
import type {
  AdminAppStats,
  AdminLlmCall,
  AdminOverview,
  AdminSnapshotRow,
  AdminSnapshotStats,
  AdminUserRow,
} from '../api'
import { fmtDateTime } from '../utils/format'

const message = useMessage()

// ── 标签页与数据加载（首次进入各页拉取，可手动刷新） ──
const TABS = [
  { key: 'overview', label: '概览' },
  { key: 'snapshots', label: '快照链路' },
  { key: 'users', label: '用户' },
  { key: 'apps', label: '投递数据' },
  { key: 'llm', label: 'LLM 用量' },
] as const
type TabKey = (typeof TABS)[number]['key']

const tab = ref<TabKey>('overview')
const overview = ref<AdminOverview | null>(null)
const snapStats = ref<AdminSnapshotStats | null>(null)
const snapshots = ref<AdminSnapshotRow[]>([])
const users = ref<AdminUserRow[]>([])
const appStats = ref<AdminAppStats | null>(null)
const llmCalls = ref<AdminLlmCall[]>([])
const budget = ref(0)
const loaded: Partial<Record<TabKey, boolean>> = {}
const loading = ref(false)
const reparsing = ref<number | null>(null)

const LOADERS: Record<TabKey, () => Promise<void>> = {
  overview: async () => {
    overview.value = await adminApi.overview()
  },
  snapshots: async () => {
    ;[snapStats.value, snapshots.value] = await Promise.all([adminApi.snapshotStats(), adminApi.snapshots()])
  },
  users: async () => {
    users.value = await adminApi.users()
  },
  apps: async () => {
    appStats.value = await adminApi.appStats()
  },
  llm: async () => {
    ;[llmCalls.value, { budget_cny: budget.value }] = await Promise.all([adminApi.llmCalls(), adminApi.llmUsage()])
  },
}

async function switchTab(key: TabKey, force = false) {
  tab.value = key
  if (!force && loaded[key]) return
  loading.value = true
  try {
    await LOADERS[key]()
    loaded[key] = true
  } catch (e: any) {
    message.error(e?.message ?? '加载失败')
  } finally {
    loading.value = false
  }
}
onMounted(() => switchTab('overview'))

async function reparse(id: number) {
  reparsing.value = id
  try {
    const r = await adminApi.reparse(id)
    const bits = [`#${id}`, r.status]
    if (r.parsed_count) bits.push(`${r.parsed_count} 条`)
    if (r.note) bits.push(r.note)
    message.success(bits.join(' · '))
    await LOADERS.snapshots()
  } catch (e: any) {
    message.error(e?.message ?? '重解析失败')
  } finally {
    reparsing.value = null
  }
}

// ── 概览 ──
const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const trendDays = computed(() => overview.value?.window.days ?? [])
const activityDatasets = computed(() => [
  { label: '快照上报', data: overview.value?.window.snapshots ?? [], borderColor: '#223a5e', backgroundColor: 'rgba(34,58,94,0.08)', fill: true, tension: 0.3, pointRadius: 2 },
  { label: '解析成功', data: overview.value?.window.snapshots_parsed ?? [], borderColor: '#3e9e8c', backgroundColor: 'rgba(62,158,140,0.08)', fill: true, tension: 0.3, pointRadius: 2 },
  { label: '新增投递', data: overview.value?.window.new_applications ?? [], borderColor: '#9aa4b0', tension: 0.3, pointRadius: 2 },
  { label: '新增用户', data: overview.value?.window.new_users ?? [], borderColor: '#c2a23e', tension: 0.3, pointRadius: 2 },
])
const costDatasets = computed(() => [
  { label: '日成本（¥）', data: overview.value?.window.llm_cost_cny ?? [], backgroundColor: '#8ca0b3', borderRadius: 3, maxBarThickness: 18 },
])
const budgetPct = computed(() => {
  if (!overview.value || overview.value.llm.budget_cny <= 0) return null
  return overview.value.llm.month_cost_cny / overview.value.llm.budget_cny
})

// ── 快照链路 ──
const ROUTE_LABELS: Record<string, string> = {
  hints: 'hints 命中',
  platform: '平台模板',
  heuristics: '启发式推断',
  embedded: '内嵌数据',
  dom: 'DOM 兜底',
}
const routeItems = computed(() => {
  const by = snapStats.value?.by_route ?? {}
  return Object.entries(by)
    .map(([k, v]) => ({ label: ROUTE_LABELS[k] ?? k, count: v, color: '#6188d8' }))
    .sort((a, b) => b.count - a.count)
})
const domainRows = computed(() => {
  const by = snapStats.value?.by_domain ?? {}
  return Object.entries(by)
    .map(([domain, d]) => ({ domain, ...d, rate: d.total ? d.parsed / d.total : null }))
    .sort((a, b) => b.total - a.total)
})
const PARSE_BADGE: Record<string, { text: string; cls: string }> = {
  parsed: { text: '已解析', cls: 'ok' },
  no_data: { text: '无数据', cls: 'warn' },
  pending: { text: '待解析', cls: '' },
}

// ── 投递数据 ──
const sourceItems = computed(() => {
  const s = appStats.value?.by_source ?? {}
  return [
    { label: '手动记录', count: s.manual ?? 0, color: '#8ca0b3' },
    { label: '自动同步', count: s.auto ?? 0, color: '#2e7d4f' },
  ].filter((i) => i.count > 0)
})
const statusItems = computed(() => (appStats.value?.by_status ?? []).map((s) => ({ label: s.label, count: s.count, color: s.color })))
const companyItems = computed(() =>
  (appStats.value?.top_companies ?? []).map((c) => ({ label: c.company, count: c.count, color: '#6188d8' })),
)
const portalItems = computed(() =>
  (appStats.value?.top_portals ?? []).map((p) => ({ label: p.portal_name, count: p.count, color: '#4aa8c0' })),
)

// ── LLM 用量 ──
const TASK_LABELS: Record<string, string> = { recipe_gen: '配方生成', status_classify: '状态分类' }
const llmAgg = computed(() => {
  const calls = llmCalls.value
  const byTask: Record<string, { count: number; ok: number; cost: number }> = {}
  let tokens = 0
  let cost = 0
  let ok = 0
  for (const c of calls) {
    const t = (byTask[c.task] ??= { count: 0, ok: 0, cost: 0 })
    t.count++
    t.cost += c.cost_cny
    if (c.ok) {
      t.ok++
      ok++
    }
    tokens += c.tokens_in + c.tokens_out
    cost += c.cost_cny
  }
  return {
    count: calls.length,
    okRate: calls.length ? ok / calls.length : null,
    tokens,
    cost,
    byTask: Object.entries(byTask)
      .map(([k, v]) => ({ label: TASK_LABELS[k] ?? k, count: v.count, ok: v.ok, cost: v.cost }))
      .sort((a, b) => b.count - a.count),
  }
})
</script>

<template>
  <div class="admin-page">
    <AppHeader />
    <div class="admin-tabs">
      <button
        v-for="t in TABS"
        :key="t.key"
        class="admin-tab"
        :class="{ 'is-active': tab === t.key }"
        @click="switchTab(t.key)"
      >
        {{ t.label }}
      </button>
      <button class="admin-tab refresh" title="刷新当前页" @click="switchTab(tab, true)">↻ 刷新</button>
    </div>

    <div class="admin-scroll">
      <div v-if="loading" class="hint">加载中…</div>

      <!-- 概览 -->
      <section v-show="tab === 'overview'" class="stack">
        <div class="grid-cards" v-if="overview">
          <MetricCard label="用户总数" :value="String(overview.users_total)" :sub="`近14天 +${overview.window.new_users.reduce((a, b) => a + b, 0)}`" />
          <MetricCard label="投递总数" :value="String(overview.applications_total)" :sub="`近14天 +${overview.window.new_applications.reduce((a, b) => a + b, 0)}`" />
          <MetricCard label="解析成功率 · 14天" :value="pct(overview.window.parse_rate)" sub="快照 → 投递卡片" />
          <MetricCard label="捕获成功率 · 14天" :value="pct(overview.window.capture_ok_rate)" sub="快照含 JSON 载荷比例" />
          <MetricCard label="快照总数" :value="String(overview.snapshots_total)" :sub="`近14天 ${overview.window.snapshots.reduce((a, b) => a + b, 0)} 条`" />
          <MetricCard
            label="LLM 月成本"
            :value="`¥${overview.llm.month_cost_cny.toFixed(2)}`"
            :sub="overview.llm.budget_cny > 0 ? `预算 ¥${overview.llm.budget_cny}（已用 ${pct(budgetPct)}）` : '未设预算'"
          />
        </div>
        <div class="grid-2" v-if="overview">
          <div class="panel">
            <h3>近 14 天活动趋势</h3>
            <ChartBox :labels="trendDays" :datasets="activityDatasets" :height="240" />
          </div>
          <div class="panel">
            <h3>LLM 日成本</h3>
            <ChartBox v-if="overview.window.llm_cost_cny.some((v) => v > 0)" type="bar" :labels="trendDays" :datasets="costDatasets" :height="240" y-money />
            <div v-else class="hint" style="padding-top: 90px">观察期内无 LLM 调用</div>
          </div>
        </div>
      </section>

      <!-- 快照链路 -->
      <section v-show="tab === 'snapshots'" class="stack">
        <div class="grid-cards" v-if="snapStats">
          <MetricCard label="观测样本" :value="String(snapStats.total)" sub="最近 1000 条快照" />
          <MetricCard label="捕获成功率" :value="pct(snapStats.total ? snapStats.capture_ok / snapStats.total : null)" sub="含 JSON 载荷" />
          <MetricCard label="解析成功率" :value="pct(snapStats.total ? snapStats.parsed / snapStats.total : null)" sub="parsed / 全部" />
          <MetricCard label="疑似未登录" :value="String(snapStats.login_suspect)" sub="需提醒用户重登" />
        </div>
        <div class="grid-2">
          <div class="panel">
            <h3>解析路由分布（成功样本）</h3>
            <BarList :items="routeItems" />
          </div>
          <div class="panel">
            <h3>按域解析率</h3>
            <BarList :items="domainRows.slice(0, 10).map((d) => ({ label: d.domain, count: d.parsed, color: '#3e9e8c' }))" />
          </div>
        </div>
        <div class="panel">
          <h3>按域名明细</h3>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>域名</th><th class="num">快照</th><th class="num">捕获</th><th class="num">解析</th><th>解析率</th><th class="num">疑似未登录</th></tr>
              </thead>
              <tbody>
                <tr v-for="d in domainRows" :key="d.domain">
                  <td class="strong">{{ d.domain }}</td>
                  <td class="num">{{ d.total }}</td>
                  <td class="num">{{ d.capture_ok }}</td>
                  <td class="num">{{ d.parsed }}</td>
                  <td class="num">{{ pct(d.rate) }}</td>
                  <td class="num">{{ d.login_suspect }}</td>
                </tr>
                <tr v-if="domainRows.length === 0"><td colspan="6" class="hint">暂无快照</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="panel">
          <h3>快照明细（最近 100 条）</h3>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>ID</th><th>用户</th><th>域名 / 页面</th><th>解析</th><th>路由</th><th class="num">记录数</th><th>门户</th><th>登录</th><th>时间</th><th></th></tr>
              </thead>
              <tbody>
                <tr v-for="s in snapshots" :key="s.id">
                  <td class="num">#{{ s.id }}</td>
                  <td class="muted">{{ s.user_email }}</td>
                  <td><span class="strong">{{ s.domain }}</span><span class="muted" :title="s.url"> {{ s.url?.slice(0, 36) }}</span></td>
                  <td><span class="badge" :class="PARSE_BADGE[s.parse_status]?.cls ?? ''">{{ PARSE_BADGE[s.parse_status]?.text ?? s.parse_status }}</span></td>
                  <td class="muted">{{ ROUTE_LABELS[s.parse_route ?? ''] ?? s.parse_route ?? '—' }}</td>
                  <td class="num">{{ s.parsed_count }}</td>
                  <td class="muted">{{ s.portal_name ?? '—' }}</td>
                  <td><span v-if="s.login_suspect" class="badge warn">疑似未登录</span><span v-else class="muted">正常</span></td>
                  <td class="num muted">{{ fmtDateTime(s.created_at) }}</td>
                  <td>
                    <button class="mini-btn" :disabled="reparsing === s.id" @click="reparse(s.id)">
                      {{ reparsing === s.id ? '解析中…' : '重解析' }}
                    </button>
                  </td>
                </tr>
                <tr v-if="snapshots.length === 0"><td colspan="10" class="hint">暂无快照</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 用户 -->
      <section v-show="tab === 'users'" class="stack">
        <div class="panel">
          <h3>用户数据（最近 200 个注册）</h3>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>邮箱</th><th>角色</th><th>注册时间</th><th class="num">投递数</th><th class="num">连接站点</th><th class="num">快照数</th><th>最近活跃</th></tr>
              </thead>
              <tbody>
                <tr v-for="u in users" :key="u.id">
                  <td class="strong">{{ u.email }}</td>
                  <td><span class="badge" :class="{ admin: u.role === 'admin' }">{{ u.role === 'admin' ? '管理员' : '用户' }}</span></td>
                  <td class="num muted">{{ fmtDateTime(u.created_at) }}</td>
                  <td class="num">{{ u.applications_count }}</td>
                  <td class="num">{{ u.sites_count }}</td>
                  <td class="num">{{ u.snapshots_count }}</td>
                  <td class="num muted">{{ u.last_active_at ? fmtDateTime(u.last_active_at) : '—' }}</td>
                </tr>
                <tr v-if="users.length === 0"><td colspan="7" class="hint">暂无用户</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 投递数据 -->
      <section v-show="tab === 'apps'" class="stack">
        <div class="grid-cards" v-if="appStats">
          <MetricCard label="投递总数" :value="String(appStats.total)" />
          <MetricCard label="手动记录" :value="String(appStats.by_source.manual ?? 0)" :sub="pct(appStats.total ? (appStats.by_source.manual ?? 0) / appStats.total : null)" />
          <MetricCard label="自动同步" :value="String(appStats.by_source.auto ?? 0)" :sub="pct(appStats.total ? (appStats.by_source.auto ?? 0) / appStats.total : null)" />
        </div>
        <div class="grid-2">
          <div class="panel">
            <h3>状态分布（统一状态机）</h3>
            <BarList :items="statusItems" />
          </div>
          <div class="panel">
            <h3>来源构成</h3>
            <ChartBox v-if="sourceItems.length" type="doughnut" :labels="sourceItems.map((i) => i.label)" :datasets="[{ data: sourceItems.map((i) => i.count), backgroundColor: sourceItems.map((i) => i.color), borderWidth: 0 }]" :height="220" />
            <div v-else class="hint" style="padding-top: 90px">暂无投递</div>
          </div>
        </div>
        <div class="grid-2">
          <div class="panel">
            <h3>热门公司 Top 10</h3>
            <BarList :items="companyItems" />
          </div>
          <div class="panel">
            <h3>热门站点 Top 10</h3>
            <BarList :items="portalItems" />
          </div>
        </div>
      </section>

      <!-- LLM 用量 -->
      <section v-show="tab === 'llm'" class="stack">
        <div class="grid-cards">
          <MetricCard label="本月成本" :value="`¥${llmAgg.cost.toFixed(2)}`" :sub="budget > 0 ? `预算 ¥${budget}（已用 ${pct(llmAgg.cost / budget)}）` : '未设预算'" />
          <MetricCard label="调用次数" :value="String(llmAgg.count)" sub="最近 100 条记账" />
          <MetricCard label="成功率" :value="pct(llmAgg.okRate)" />
          <MetricCard label="Token 合计" :value="llmAgg.tokens.toLocaleString()" sub="入 + 出" />
        </div>
        <div class="panel">
          <h3>按任务汇总（最近 100 条）</h3>
          <BarList :items="llmAgg.byTask.map((t) => ({ label: t.label, count: t.count, color: '#6188d8' }))" />
        </div>
        <div class="panel">
          <h3>调用明细（最近 100 条）</h3>
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr><th>时间</th><th>任务</th><th>提供者 / 模型</th><th class="num">入 tok</th><th class="num">出 tok</th><th class="num">成本 ¥</th><th class="num">延迟</th><th>结果</th></tr>
              </thead>
              <tbody>
                <tr v-for="c in llmCalls" :key="c.id">
                  <td class="num muted">{{ fmtDateTime(c.created_at) }}</td>
                  <td>{{ TASK_LABELS[c.task] ?? c.task }}</td>
                  <td class="muted">{{ c.provider || '—' }}{{ c.model ? ` / ${c.model}` : '' }}</td>
                  <td class="num">{{ c.tokens_in }}</td>
                  <td class="num">{{ c.tokens_out }}</td>
                  <td class="num">{{ c.cost_cny.toFixed(4) }}</td>
                  <td class="num">{{ c.latency_ms }}ms</td>
                  <td>
                    <span class="badge" :class="c.ok ? 'ok' : 'warn'">{{ c.ok ? '成功' : '失败' }}</span>
                    <span v-if="!c.ok && c.error" class="muted" :title="c.error">{{ c.error.slice(0, 24) }}</span>
                  </td>
                </tr>
                <tr v-if="llmCalls.length === 0"><td colspan="8" class="hint">暂无调用记录</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.admin-page { height: 100%; display: flex; flex-direction: column; }

.admin-tabs {
  flex: none;
  display: flex;
  align-items: stretch;
  gap: 2px;
  padding: 0 24px;
  border-bottom: 1px solid var(--line);
  background: var(--card);
}
.admin-tab {
  border: none;
  background: none;
  cursor: pointer;
  padding: 11px 14px;
  font: inherit;
  font-size: 13px;
  color: var(--ink-2);
  border-bottom: 2px solid transparent;
}
.admin-tab:hover { color: var(--ink); }
.admin-tab.is-active { color: var(--ink); font-weight: 600; border-bottom-color: var(--brand); }
.admin-tab.refresh { margin-left: auto; color: var(--ink-3); align-self: center; padding: 4px 10px; border: 1px solid var(--line); border-radius: 6px; }
.admin-tab.refresh:hover { color: var(--ink); border-color: var(--ink-3); }

.admin-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 20px 24px 60px; }
.stack { display: flex; flex-direction: column; gap: 14px; }

.panel { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; }
.panel h3 { margin: 0 0 14px; font-size: 13px; font-weight: 600; }

.grid-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 960px) { .grid-2 { grid-template-columns: 1fr; } }

.table-wrap { overflow-x: auto; }
.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th {
  text-align: left; font-weight: 600; color: var(--ink-2); font-size: 12px;
  padding: 8px 10px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
.table td { padding: 8px 10px; border-bottom: 1px solid #f0f3f6; vertical-align: top; }
.table tr:last-child td { border-bottom: none; }

.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.muted { color: var(--ink-2); font-size: 12px; }
.strong { font-weight: 600; }
.hint { color: var(--ink-3); font-size: 12px; text-align: center; padding: 8px 0; }

.badge {
  display: inline-block;
  font-size: 11px; line-height: 1;
  padding: 3px 6px; border-radius: 4px;
  background: var(--brand-soft); color: var(--brand);
  white-space: nowrap;
}
.badge.ok { background: #eaf3ee; color: #2e7d4f; }
.badge.warn { background: #f7f0dd; color: #b08a3e; }
.badge.admin { background: var(--ink); color: var(--card); }

.mini-btn {
  border: 1px solid var(--line);
  background: var(--card);
  border-radius: 6px;
  padding: 3px 10px;
  font: inherit; font-size: 12px;
  color: var(--ink-2);
  cursor: pointer;
}
.mini-btn:hover:not(:disabled) { color: var(--brand); border-color: var(--brand); }
.mini-btn:disabled { opacity: 0.5; cursor: wait; }
</style>
