<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useBoardStore } from '../stores/board'
import { fmtDate } from '../utils/format'

const store = useBoardStore()
const auth = useAuthStore()
const router = useRouter()

// ── 折叠：默认窄屏收起，localStorage 记忆（与终态列同一套模式） ──
const COLLAPSED_KEY = 'jobcheck.board.side-collapsed'
const collapsed = ref(
  (() => {
    const saved = localStorage.getItem(COLLAPSED_KEY)
    if (saved !== null) return saved === '1'
    return window.innerWidth < 1440
  })(),
)
watch(collapsed, (v) => localStorage.setItem(COLLAPSED_KEY, v ? '1' : '0'))

const stats = computed(() => store.stats)

// 流程分布比例条以最大计数归一（不是占比百分比，避免小值看不见）
const distMax = computed(() => Math.max(1, ...(stats.value?.by_status ?? []).map((s) => s.count)))

function toggleStatus(key: string) {
  store.filters.status = store.filters.status === key ? null : key
}

const isAdmin = computed(() => auth.user?.role === 'admin')
</script>

<template>
  <!-- 收起态：竖向细条，样式语义与看板终态列细条一致 -->
  <button
    v-if="collapsed"
    class="side-rail"
    title="展开「我的数据」"
    aria-label="展开我的数据侧边栏"
    @click="collapsed = false"
  >
    <span class="rail-arrow" aria-hidden="true">»</span>
    <span v-if="stats" class="rail-count">{{ stats.total }}</span>
    <span class="rail-label">我的数据</span>
  </button>

  <aside v-else class="side-panel">
    <div class="side-head">
      <span class="side-title">我的数据</span>
      <button
        class="side-collapse-btn"
        title="收起侧边栏"
        aria-label="收起我的数据侧边栏"
        @click="collapsed = true"
      >
        «
      </button>
    </div>

    <div class="side-body">
      <!-- 模块一：投递总览 -->
      <section class="side-section">
        <div class="total-line">
          <span class="total-num">{{ stats?.total ?? '—' }}</span>
          <span class="total-label">条投递</span>
        </div>
        <dl class="mini-stats">
          <div class="mini">
            <dt>进行中</dt>
            <dd>{{ stats?.in_progress ?? '—' }}</dd>
          </div>
          <div class="mini">
            <dt>已结束</dt>
            <dd>{{ stats?.terminal ?? '—' }}</dd>
          </div>
          <div class="mini">
            <dt>本月新增</dt>
            <dd>{{ stats?.month_new ?? '—' }}</dd>
          </div>
        </dl>
      </section>

      <!-- 模块二：流程分布（全量个人统计，不受看板筛选影响；点击=筛选看板） -->
      <section class="side-section">
        <h3 class="side-section-title">流程分布</h3>
        <p v-if="stats && stats.by_status.length === 0" class="side-empty">记录第一条投递后，这里会展示各阶段分布。</p>
        <ul v-else class="dist-list">
          <li v-for="s in stats?.by_status ?? []" :key="s.key">
            <button
              class="dist-row"
              :class="{ 'is-active': store.filters.status === s.key }"
              :style="{ '--dot-color': s.color }"
              :aria-pressed="store.filters.status === s.key"
              :title="`${s.label} × ${s.count}（点击筛选看板）`"
              @click="toggleStatus(s.key)"
            >
              <span class="dist-dot" aria-hidden="true"></span>
              <span class="dist-label">{{ s.label }}</span>
              <span class="dist-bar" aria-hidden="true">
                <span class="dist-bar-fill" :style="{ width: (s.count / distMax) * 100 + '%' }"></span>
              </span>
              <span class="dist-count">{{ s.count }}</span>
            </button>
          </li>
        </ul>
      </section>

      <!-- 模块三：个人账号 -->
      <section class="side-section">
        <h3 class="side-section-title">个人账号</h3>
        <div class="account-box">
          <div class="account-email" :title="auth.user?.email">{{ auth.user?.email }}</div>
          <div class="account-meta">
            <span v-if="isAdmin" class="role-badge">管理员</span>
            <span class="account-since">注册于 {{ fmtDate(auth.user?.created_at) }}</span>
          </div>
          <div class="account-counts">
            <span>已连接站点 {{ store.connectedSites.length }}</span>
            <span>标签 {{ store.tags.length }}</span>
          </div>
          <button class="settings-link" @click="router.push('/settings')">前往设置 →</button>
        </div>
      </section>
    </div>
  </aside>
</template>

<style scoped>
/* ── 收起态细条 ── */
.side-rail {
  flex: 0 0 44px;
  align-self: stretch;
  margin: 14px 0 24px 16px;
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  border: 1px solid var(--line); border-radius: 10px; background: var(--card);
  cursor: pointer; padding: 12px 0; font-family: inherit;
  transition: border-color 0.15s ease;
}
.side-rail:hover { border-color: #c9d2dd; }
.side-rail:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.side-rail .rail-count {
  font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums;
  background: var(--brand-soft); border-radius: 10px; padding: 1px 8px;
}
.side-rail .rail-label {
  writing-mode: vertical-rl; letter-spacing: 0.24em;
  font-size: 12px; color: var(--ink-2);
}
.side-rail .rail-arrow { color: var(--ink-3); font-size: 13px; }

/* ── 展开态面板 ── */
.side-panel {
  flex: 0 0 264px;
  align-self: stretch;
  margin: 14px 0 24px 16px;
  display: flex; flex-direction: column;
  border: 1px solid var(--line); border-radius: 10px; background: var(--card);
  min-height: 0;
}

.side-head {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px;
  border-bottom: 2px solid var(--line);
  flex: none;
}
.side-title { font-weight: 600; letter-spacing: 0.02em; }
.side-collapse-btn {
  margin-left: auto;
  border: none; background: transparent; color: var(--ink-3); cursor: pointer;
  font-size: 14px; line-height: 1; padding: 2px 6px; border-radius: 4px; flex: none;
}
.side-collapse-btn:hover { background: var(--brand-soft); color: var(--ink); }
.side-collapse-btn:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }

.side-body {
  flex: 1; overflow-y: auto; min-height: 0;
  display: flex; flex-direction: column; gap: 14px;
  padding: 14px 14px 20px;
}

.side-section + .side-section { border-top: 1px dashed var(--line); padding-top: 14px; }
.side-section-title {
  margin: 0 0 8px;
  font-size: 12px; font-weight: 600; color: var(--ink-2);
  letter-spacing: 0.08em;
}

/* 投递总览 */
.total-line { display: flex; align-items: baseline; gap: 6px; }
.total-num {
  font-size: 34px; font-weight: 700; line-height: 1.1;
  font-variant-numeric: tabular-nums; letter-spacing: 0.01em;
}
.total-label { font-size: 13px; color: var(--ink-2); }
.mini-stats {
  margin: 10px 0 0; padding: 0;
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;
}
.mini {
  background: var(--paper); border: 1px solid var(--line); border-radius: 8px;
  padding: 6px 8px; text-align: center;
}
.mini dt { font-size: 11px; color: var(--ink-3); }
.mini dd { margin: 2px 0 0; font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; }

/* 流程分布 */
.side-empty { margin: 0; font-size: 12px; color: var(--ink-3); }
.dist-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.dist-row {
  display: flex; align-items: center; gap: 8px; width: 100%;
  border: none; background: transparent; font-family: inherit; font-size: 12px;
  color: var(--ink); text-align: left; cursor: pointer;
  padding: 5px 6px; border-radius: 6px;
  transition: background 0.15s ease;
}
.dist-row:hover { background: var(--brand-soft); }
.dist-row:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }
.dist-row.is-active { background: var(--brand-soft); box-shadow: inset 0 0 0 1px var(--brand); }
.dist-dot {
  width: 8px; height: 8px; border-radius: 50%; flex: none;
  background: var(--dot-color, var(--ink-3));
}
.dist-label { flex: none; max-width: 96px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dist-bar { flex: 1; height: 4px; border-radius: 2px; background: var(--line); overflow: hidden; }
.dist-bar-fill { display: block; height: 100%; border-radius: 2px; background: var(--dot-color, var(--ink-3)); }
.dist-count { flex: none; min-width: 20px; text-align: right; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.dist-row.is-active .dist-count { color: var(--ink); font-weight: 600; }

/* 个人账号 */
.account-box { display: flex; flex-direction: column; gap: 6px; }
.account-email {
  font-size: 13px; font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.account-meta { display: flex; align-items: center; gap: 8px; }
.role-badge {
  font-size: 11px; line-height: 1; padding: 3px 6px; border-radius: 4px;
  background: var(--brand-soft); color: var(--brand);
}
.account-since { font-size: 12px; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.account-counts { display: flex; gap: 12px; font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.settings-link {
  align-self: flex-start;
  border: none; background: transparent; font-family: inherit; font-size: 12px;
  color: var(--brand); cursor: pointer; padding: 2px 0;
}
.settings-link:hover { text-decoration: underline; }
.settings-link:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }

@media (prefers-reduced-motion: reduce) {
  .dist-row { transition: none; }
}
</style>
