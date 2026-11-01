import type { FactVersionView } from '@decision-hub/contracts-ts'
import { z } from 'zod'

type Fact = FactVersionView['fact']
const decimal = /^[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?$/i
const day = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value
const instant = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.test(value) && day(value.slice(0, 10)) && Number.isFinite(Date.parse(value))
const sameInstant = (left: unknown, right: unknown) => instant(left) && instant(right) && Date.parse(left) === Date.parse(right)
const number = (value: Fact['value']): number | null => typeof value === 'number' ? Number.isFinite(value) ? value : null : typeof value === 'string' && decimal.test(value.trim()) && Number.isFinite(Number(value)) ? Number(value) : null
const percentFields = new Set(['return_30m', 'return_24h', 'realized_vol_30m'])
const percentConvention = (fact: Fact) => fact.unit === 'fraction' && fact.metric_family === 'crypto.spot' && fact.payload_schema_ref === 'okx.spot-ledger-fact.v1'

export function numericFactDisplay(fact: Fact): { value: number; unit: string } | null {
  const raw = number(fact.value)
  if (raw === null || fact.field === 'path') return null
  if (percentFields.has(fact.field)) {
    if (!percentConvention(fact) || (fact.field === 'realized_vol_30m' && raw < 0)) return null
    if (!Number.isFinite(raw * 100)) return null
    return { value: raw * 100, unit: fact.field === 'realized_vol_30m' ? '%（未年化）' : '%' }
  }
  return { value: raw, unit: fact.unit }
}

export function formattedNumeric(value: number, unit: string): string {
  if ((unit === '%' || unit === '%（未年化）') && value !== 0 && Math.abs(value) < 0.01) return value < 0 ? '幅度小于0.01（负向）' : '小于0.01'
  if (unit === '%' || unit === '%（未年化）' || unit === 'yield_percent') return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return value.toLocaleString('zh-CN', value !== 0 && Math.abs(value) < 0.001 ? { maximumSignificantDigits: 8 } : { maximumFractionDigits: 6 })
}

const decimalValue = z.union([z.number().finite(), z.string().regex(decimal).refine(value => Number.isFinite(Number(value)))])
const timestamp = z.string().refine(instant)
const sha = z.string().regex(/^[a-f0-9]{64}$/)
const scope = z.object({ schema_version: z.literal('market-path-scope.v1'), scope_id: z.string(), asset_class: z.literal('crypto'), instrument_kind: z.literal('spot'), venue: z.string(), instrument: z.enum(['BTC-USDT', 'ETH-USDT']), quote_currency: z.literal('USDT'), price_type: z.literal('trade'), price_unit: z.string(), volume_unit: z.enum(['BTC', 'ETH']), timezone: z.literal('UTC'), calendar_ref: z.literal('crypto-24x7.v1'), interval_seconds: z.literal(60) }).strict().refine(value => value.scope_id === `crypto:spot:${value.venue}:${value.instrument}:trade:1m` && value.price_unit === `USDT/${value.instrument.split('-')[0]}` && value.volume_unit === value.instrument.split('-')[0])
const crossing = z.object({ direction: z.enum(['long', 'short']), threshold: decimalValue, event_presence: z.boolean().nullable(), firstness: z.enum(['verified', 'unknown', 'not_applicable']), interval_start_at: timestamp.nullable(), interval_end_at: timestamp.nullable(), resolution_seconds: z.literal(60) }).strict()
const path = z.object({ schema_version: z.literal('path-risk-result.v1'), scope, window_start_at: timestamp, window_end_at: timestamp, as_of: timestamp, anchor_at: timestamp, anchor_price: decimalValue.nullable(), anchor_status: z.enum(['verified', 'unknown']), window_status: z.enum(['pending', 'complete', 'partial', 'unknown']), observed_count: z.number().int().nonnegative(), expected_count: z.number().int().positive().max(1440), pending_count: z.number().int().nonnegative(), missing_open_at: z.array(timestamp), missing_ranges: z.array(z.tuple([timestamp, timestamp])), terminal_return: decimalValue.nullable(), mae_long: decimalValue.nullable(), mae_short: decimalValue.nullable(), mdd_close: decimalValue.nullable(), quality: z.enum(['observed', 'observed_lower_bound', 'unknown']), crossings: z.array(crossing), same_bar_order_ambiguous: z.boolean(), reasons: z.array(z.string()), semantic_hash: sha, manifest_hash: sha }).strict()

export function pathSummary(fact: Fact): string | null {
  if (fact.field !== 'path') return null
  if (fact.unit !== 'path_manifest' || fact.payload_schema_ref !== 'okx.spot-ledger-fact.v1' || fact.metric_family !== 'crypto.spot' || typeof fact.value !== 'string' || fact.value.length > 262144) return '路径记录待核验'
  try {
    const parsed = path.safeParse(JSON.parse(fact.value))
    if (!parsed.success) return '路径记录待核验'
    const p = parsed.data, minutes = (Date.parse(p.window_end_at) - Date.parse(p.window_start_at)) / 60000
    if (p.scope.instrument !== fact.instrument || p.scope.venue !== fact.venue || !sameInstant(p.window_start_at, fact.window_start_at) || !sameInstant(p.window_end_at, fact.window_end_at) || p.expected_count !== minutes || Date.parse(p.as_of) < Date.parse(p.window_start_at) || p.observed_count > p.expected_count || p.pending_count > p.expected_count || !sameInstant(p.anchor_at, p.window_start_at)) return '路径记录待核验'
    if (p.window_status === 'complete' && (Date.parse(p.as_of) < Date.parse(p.window_end_at) || p.observed_count !== p.expected_count || p.pending_count !== 0 || p.missing_open_at.length !== 0 || p.missing_ranges.length !== 0 || p.quality !== 'observed' || p.anchor_status !== 'verified' || p.anchor_price === null || Number(p.anchor_price) <= 0 || p.terminal_return === null)) return '路径记录待核验'
    const state = { complete: '路径完整', partial: '路径不完整', pending: '窗口尚未完成', unknown: '路径完整性未知' }[p.window_status]
    return `${state} · ${p.observed_count}/${p.expected_count} 根1分钟K线`
  } catch { return '路径记录待核验' }
}

export function numericPresentation(fact: Fact): { value: string; unit: string } | null {
  const summary = pathSummary(fact)
  if (summary !== null) return { value: summary, unit: '路径记录' }
  if (!percentFields.has(fact.field)) return null
  if (!percentConvention(fact)) return { value: '数值口径待核验', unit: '原值见审计' }
  if (fact.value === null) return { value: '未提供数值', unit: fact.field === 'realized_vol_30m' ? '%（未年化）' : '%' }
  const numeric = numericFactDisplay(fact)
  return numeric === null ? { value: '数值口径待核验', unit: '原值见审计' } : { value: formattedNumeric(numeric.value, numeric.unit), unit: numeric.unit }
}

export type FactTemporal = { primaryLabel: string; primaryValue: string; period: string | null; sourceUpdatedAt: string | null; sourceUpdatedLabel: string | null; observedLabel: string; note: string | null }
export function temporalPresentation(fact: Fact): FactTemporal {
  const attrs = fact.attributes
  const isMacro = fact.metric_family.startsWith('macro.')
  const result: FactTemporal = { primaryLabel: isMacro ? '来源观测期' : '数据观察时间', primaryValue: isMacro ? '来源观测期未记录' : fact.observed_at, period: null, sourceUpdatedAt: null, sourceUpdatedLabel: null, observedLabel: isMacro ? '本次采集时间' : '数据观察时间', note: null }
  const frequencies: Record<string, string> = { WRESBAL: 'weekly_ending_wednesday', DFII10: 'daily', T10YIE: 'daily' }
  const families: Record<string, string> = { WRESBAL: 'macro.reserve_balances', DFII10: 'macro.real_yield', T10YIE: 'macro.inflation_compensation', DGS2: 'macro.rates', DGS10: 'macro.rates', DTWEXBGS: 'macro.usd' }
  const units: Record<string, string> = { WRESBAL: 'million_usd', DFII10: 'yield_percent', T10YIE: 'yield_percent', DGS2: 'yield_percent', DGS10: 'yield_percent', DTWEXBGS: 'index' }
  const official = fact.source_id === 'fred.current_macro' && fact.payload_schema_ref === 'official-fred-current-series.v1' && typeof fact.instrument === 'string' && Object.hasOwn(frequencies, fact.instrument)
  const csv = fact.source_id === 'fred-public' && fact.payload_schema_ref === 'fred.series-observation.v2' && ['DGS2', 'DGS10', 'DTWEXBGS'].includes(fact.instrument ?? '')
  if (!official && !csv) return result
  const date = attrs.observation_date
  if (!day(date) || !instant(fact.observed_at) || !instant(fact.received_at) || Date.parse(fact.observed_at) > Date.parse(fact.received_at) || date > new Date(fact.observed_at).toISOString().slice(0, 10) || attrs.series !== fact.instrument || fact.metric_family !== families[fact.instrument!] || fact.unit !== units[fact.instrument!] || (fact.instrument === 'WRESBAL' && new Date(`${date}T00:00:00Z`).getUTCDay() !== 3)) return { ...result, note: '留存时间字段不一致，未推断来源观测期。' }
  if (official && (attrs.frequency !== frequencies[fact.instrument!] || attrs.parser_ref !== 'official-fred-current-series.v1' || attrs.publication_time_kind !== 'fred_series_updated' || attrs.vintage_proven !== false || !sameInstant(attrs.source_updated_at, fact.published_at) || Date.parse(String(attrs.source_updated_at)) > Date.parse(fact.observed_at))) return { ...result, note: '留存时间字段不一致，未推断来源观测期。' }
  if (csv && (attrs.time_schema !== 'fred.observation-time.v2' || attrs.observation_time_precision !== 'date' || attrs.public_time_precision !== 'unknown' || fact.published_at !== null)) return { ...result, note: '留存时间字段不一致，未推断来源观测期。' }
  return { ...result, primaryValue: date, period: date, sourceUpdatedAt: official ? fact.published_at : null, sourceUpdatedLabel: official ? 'FRED页面更新' : null, note: official ? '页面更新表示这份页面版本的更新时间，未证明历史发布版本。' : '这份CSV没有提供公开发布时间或历史发布版本。' }
}
