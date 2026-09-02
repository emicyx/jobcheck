/**
 * 看板阶段列映射（纯展示层）。
 *
 * 后端 /api/meta 仍是状态定义的单一事实来源（标签/颜色/顺序）；
 * 这里只决定「哪几个状态合并展示为一列」，把 15 个细分状态压缩成
 * 6 个阶段列 + 1 个可折叠的终态列，让看板在常规屏宽下无需横向滚动。
 * 细分信息不丢失：列头有按状态的筛选片，卡片带状态徽标。
 */
import type { Application, Meta, StatusMeta } from '../types'

export interface StageDef {
  id: string
  label: string
  /** 多状态列的强调色；单状态列直接取该状态的 meta color */
  color?: string
  keys: string[]
  terminal?: boolean
}

export const STAGE_DEFS: StageDef[] = [
  { id: 'pending_confirm', label: '待确认', keys: ['pending_confirm'] },
  { id: 'applied', label: '已投递', keys: ['applied'] },
  { id: 'screening', label: '简历评估', keys: ['screening'] },
  { id: 'testing', label: '测评/笔试', color: '#3e9e8c', keys: ['assessment', 'written_test'] },
  {
    id: 'interviewing',
    label: '面试中',
    color: '#d89c2e',
    keys: ['interview_1', 'interview_2', 'interview_3', 'hr_interview', 'interview_unknown'],
  },
  { id: 'offer', label: 'Offer / 入职', color: '#4f9e57', keys: ['offer', 'onboarded'] },
  { id: 'ended', label: '已结束', color: '#c25a5a', keys: ['rejected', 'withdrawn', 'expired'], terminal: true },
]

/** 筛选片 / 卡片徽标用的短标签（完整标签仍以后端 label 为准） */
const SHORT_LABELS: Record<string, string> = {
  hr_interview: 'HR面',
  interview_unknown: '轮次未知',
  onboarded: '已入职',
}

export function shortLabel(key: string, fallback: string): string {
  return SHORT_LABELS[key] ?? fallback
}

export function isTerminalStatus(key: string): boolean {
  return STAGE_DEFS.some((d) => d.terminal && d.keys.includes(key))
}

export interface StageStatus {
  key: string
  label: string
  short: string
  color: string
  order: number
  count: number
}

export interface StageColumn {
  id: string
  label: string
  color: string
  terminal: boolean
  /** 列内含多个细分状态 → 列头显示筛选片、卡片显示状态徽标 */
  multi: boolean
  statuses: StageStatus[]
  apps: Application[]
  total: number
}

/** 列内排序：走得最远的在前，同级按更新时间倒序 */
function sortApps(apps: Application[], orderOf: Map<string, number>): Application[] {
  return [...apps].sort(
    (a, b) =>
      (orderOf.get(b.current_status) ?? 0) - (orderOf.get(a.current_status) ?? 0) ||
      b.updated_at.localeCompare(a.updated_at),
  )
}

export function buildStageColumns(meta: Meta | null, applications: Application[]): StageColumn[] {
  const byKey = new Map<string, StatusMeta>((meta?.statuses ?? []).map((s) => [s.key, s]))

  const columns: StageColumn[] = STAGE_DEFS.map((def) => {
    const statuses: StageStatus[] = def.keys
      .filter((k) => byKey.has(k))
      .map((k) => {
        const s = byKey.get(k)!
        return { key: k, label: s.label, short: shortLabel(k, s.label), color: s.color, order: s.order, count: 0 }
      })
    const orderOf = new Map(statuses.map((s) => [s.key, s.order]))
    const keySet = new Set(def.keys)
    const apps = sortApps(
      applications.filter((a) => keySet.has(a.current_status)),
      orderOf,
    )
    for (const a of apps) {
      const st = statuses.find((s) => s.key === a.current_status)
      if (st) st.count++
    }
    return {
      id: def.id,
      label: def.label,
      color: def.color ?? statuses[0]?.color ?? '#9aa4b0',
      terminal: !!def.terminal,
      multi: statuses.length > 1,
      statuses,
      apps,
      total: apps.length,
    }
  })

  // 兜底：后端新增（或数据异常）而映射未覆盖的状态独立成列，保证记录永远可见
  const known = new Set(STAGE_DEFS.flatMap((d) => d.keys))
  const extraDefs: StatusMeta[] = [...byKey.values()].filter((s) => !known.has(s.key))
  for (const a of applications) {
    if (!byKey.has(a.current_status) && !extraDefs.some((s) => s.key === a.current_status)) {
      extraDefs.push({
        key: a.current_status,
        label: a.current_status,
        group: 'progress',
        order: 9999,
        color: '#9aa4b0',
      })
    }
  }
  extraDefs.sort((a, b) => a.order - b.order)
  for (const s of extraDefs) {
    const apps = sortApps(applications.filter((a) => a.current_status === s.key), new Map([[s.key, s.order]]))
    columns.push({
      id: `extra-${s.key}`,
      label: s.label,
      color: s.color,
      terminal: false,
      multi: false,
      statuses: [{ key: s.key, label: s.label, short: s.label, color: s.color, order: s.order, count: apps.length }],
      apps,
      total: apps.length,
    })
  }

  return columns
}
