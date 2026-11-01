import {
  riskScopesViewSchema, riskSummaryViewSchema, riskTimelineViewSchema, riskAlertViewSchema,
  riskReceiptCommandSchema, riskReceiptViewSchema, riskMonitorCommandSchema,
  riskMonitorCommandReceiptSchema,
  type RiskScopesView, type RiskSummaryView, type RiskTimelineView, type RiskAlertView,
  type RiskReceiptView, type RiskMonitorCommandReceipt,
} from '@decision-hub/contracts-ts'
import { request } from './client'

export type { RiskScope, RiskSummaryView, RiskTimelineView, RiskAlertView, RiskMonitorStatus } from '@decision-hub/contracts-ts'

const sameScope = (actual: string, expected: string) => {
  if (actual !== expected) throw new Error('返回的数据与当前资产不一致，请重新读取。')
}
function checkedPrice(price: string) {
  const value = Number(price)
  if (!Number.isFinite(value) || value <= 0) throw new Error('行情价格无法核对，当前状态未确认。')
}
const OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const post = (body: unknown, requestId: string) => ({ method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId, 'X-Owner-Id': OWNER_ID }, body: JSON.stringify(body) })

export function riskErrorMessage(error: unknown): string {
  const detail = error instanceof Error ? error.message : ''
  const messages: Record<string, string> = {
    risk_monitor_not_enabled: '自动风险监测尚未启用，当前无法恢复。请在管理与审计核对运行状态。',
    risk_monitor_not_found: '这项监控计划已不可用，请刷新后重新选择。',
    risk_alert_not_found: '这条告警记录已不可用，请重新读取告警列表。',
    owner_identity_required: '当前操作权限尚未确认，请核对管理与审计中的运行状态。',
    owner_identity_mismatch: '当前操作权限无法核对，请核对管理与审计中的运行状态。',
    risk_command_identity_conflict: '原操作记录存在冲突，请重新读取当前状态后核对。',
  }
  return messages[detail] ?? (/^[\u4e00-\u9fff]/.test(detail) ? detail : '操作结果尚未确认，请刷新核对后重试；重试沿用原操作记录。')
}

async function receipt(alertId: string, kind: 'display-receipt' | 'ack', requestId: string, clientTime: string): Promise<RiskReceiptView> {
  const body = riskReceiptCommandSchema.parse({ request_id: requestId, client_time: clientTime })
  const view = riskReceiptViewSchema.parse(await request(`/v1/risk/alerts/${encodeURIComponent(alertId)}/${kind}`, post(body, requestId)))
  if (view.alert_id !== alertId || view.request_id !== requestId) throw new Error('告警回执与原操作不一致，尚未确认。')
  return view
}

export const riskApi = {
  scopes: async (signal?: AbortSignal): Promise<RiskScopesView> => riskScopesViewSchema.parse(await request('/v1/risk/scopes', { signal })),
  summary: async (scopeId: string, signal?: AbortSignal): Promise<RiskSummaryView> => {
    const view = riskSummaryViewSchema.parse(await request(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/summary`, { signal }))
    sameScope(view.scope_id, scopeId)
    view.monitors.forEach(item => sameScope(item.scope_id, scopeId))
    view.recent_alerts.forEach(item => sameScope(item.scope_id, scopeId))
    if (view.quote) checkedPrice(view.quote.price)
    return view
  },
  timeline: async (scopeId: string, start: string, end: string, signal?: AbortSignal): Promise<RiskTimelineView> => {
    const params = new URLSearchParams({ start, end })
    const view = riskTimelineViewSchema.parse(await request(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/timeline?${params}`, { signal }))
    sameScope(view.scope_id, scopeId)
    if (Date.parse(view.start) !== Date.parse(start) || Date.parse(view.end) !== Date.parse(end)) throw new Error('价格路径时间范围与请求不一致。')
    let previous = -Infinity
    view.points.forEach(point => {
      checkedPrice(point.price)
      if ((point.low === undefined) !== (point.high === undefined)) throw new Error('分钟高低价缺少配对数据。')
      if (point.low !== undefined && point.high !== undefined) {
        checkedPrice(point.low); checkedPrice(point.high)
        if (Number(point.low) > Number(point.price) || Number(point.price) > Number(point.high)) throw new Error('分钟高低价与收盘价不一致。')
      }
      const time = Date.parse(point.at)
      if (time <= previous || time < Date.parse(start) || time > Date.parse(end)) throw new Error('价格路径的时间顺序无法核对。')
      previous = time
    })
    view.missing_ranges.forEach(range => {
      if (Date.parse(range.start_at) >= Date.parse(range.end_at)) throw new Error('价格路径缺口范围无法核对。')
    })
    view.alerts.forEach(item => sameScope(item.scope_id, scopeId))
    if (view.aggregation && (view.aggregation.output_points !== view.points.length || view.aggregation.source_points < view.points.length || (view.aggregation.method === 'full_minutes' && view.aggregation.source_points !== view.points.length))) throw new Error('路径聚合信息与显示点数不一致。')
    view.markers?.forEach(marker => {
      if (Date.parse(marker.at) < Date.parse(start) || Date.parse(marker.at) > Date.parse(end)) throw new Error('关键记录的系统时间不在请求范围内。')
    })
    return view
  },
  alert: async (alertId: string, signal?: AbortSignal): Promise<RiskAlertView> => {
    const view = riskAlertViewSchema.parse(await request(`/v1/risk/alerts/${encodeURIComponent(alertId)}`, { signal }))
    if (view.alert_id !== alertId) throw new Error('返回的告警与当前选择不一致。')
    return view
  },
  displayReceipt: (alertId: string, requestId: string, clientTime = new Date().toISOString()) => receipt(alertId, 'display-receipt', requestId, clientTime),
  acknowledge: (alertId: string, requestId: string, clientTime = new Date().toISOString()) => receipt(alertId, 'ack', requestId, clientTime),
  monitorCommand: async (planId: string, action: 'pause' | 'resume', requestId: string): Promise<RiskMonitorCommandReceipt> => {
    const body = riskMonitorCommandSchema.parse({ action, request_id: requestId })
    const view = riskMonitorCommandReceiptSchema.parse(await request(`/v1/risk/monitors/${encodeURIComponent(planId)}/command`, post(body, requestId)))
    if (view.plan_id !== planId || view.monitor.plan_id !== planId || view.action !== action || view.request_id !== requestId) throw new Error('监控回执与原操作不一致，尚未确认。')
    return view
  },
}

// Keep an uncertain operation's identity through refresh; confirmed monitor
// commands remove their identity so the next pause/resume cycle is a new action.
const inMemoryIds = new Map<string, string>()
const inMemoryReceipts = new Map<string, { request_id: string; client_time: string }>()
export function riskRequestId(key: string): string {
  const storageKey = `decision-hub:risk:${key}`
  const cached = inMemoryIds.get(storageKey)
  if (cached) return cached
  try {
    const old = sessionStorage.getItem(storageKey)
    if (old) { inMemoryIds.set(storageKey, old); return old }
    const id = crypto.randomUUID()
    inMemoryIds.set(storageKey, id)
    sessionStorage.setItem(storageKey, id)
    return id
  } catch {
    const id = inMemoryIds.get(storageKey) ?? crypto.randomUUID()
    inMemoryIds.set(storageKey, id)
    return id
  }
}
export function clearRiskRequestId(key: string) {
  const storageKey = `decision-hub:risk:${key}`
  inMemoryIds.delete(storageKey)
  try { sessionStorage.removeItem(storageKey) } catch { /* storage can be disabled */ }
}
export function riskReceiptIntent(key: string): { request_id: string; client_time: string } {
  const storageKey = `decision-hub:risk:receipt:${key}`
  const cached = inMemoryReceipts.get(storageKey)
  if (cached) return cached
  try {
    const old = sessionStorage.getItem(storageKey)
    if (old) {
      const intent = riskReceiptCommandSchema.parse(JSON.parse(old))
      inMemoryReceipts.set(storageKey, intent)
      return intent
    }
  } catch { /* corrupt or unavailable storage cannot supply an operation identity */ }
  const intent = { request_id: riskRequestId(key), client_time: new Date().toISOString() }
  inMemoryReceipts.set(storageKey, intent)
  try { sessionStorage.setItem(storageKey, JSON.stringify(intent)) } catch { /* in-memory retry is still stable */ }
  return intent
}
