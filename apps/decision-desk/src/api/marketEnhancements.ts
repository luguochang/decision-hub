import { z } from 'zod'
import { request } from './client'

const at = z.string().datetime({ offset: true })
const identity = z.string().min(1)
const count = z.number().int().nonnegative()
export const enhancementRoutes = ['MACRO_STATE_V1', 'CROWDING_V1', 'EVENT_SURPRISE_V1'] as const
const route = z.enum(enhancementRoutes)
const feature = z.object({
  name: identity, value: z.number().finite().nullable(), unit: identity,
  status: z.enum(['available', 'unknown']), reason_code: identity.nullable(),
}).strict().refine(value => (value.value === null) === (value.status === 'unknown'), '数据状态无法核对')
export const enhancementViewSchema = z.object({
  schema_version: z.literal('market-enhancements-view.v1'), scope_id: identity,
  instrument: z.enum(['BTC-USDT', 'ETH-USDT']), as_of: at,
  integrity: z.literal('stored_payload_hash'),
  routes: z.object({
    route, registration_id: identity.nullable(), registered_at: at.nullable(),
    data_status: z.enum(['not_registered', 'awaiting_data', 'available', 'partial', 'unavailable']),
    admission_state: z.enum(['not_assessed', 'not_admitted', 'descriptive_only', 'research_qualified']),
    receipt_id: identity.nullable(), saved_at: at.nullable(), cutoff_at: at.nullable(),
    source_known_at: at.nullable(), observation_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable(),
    features: feature.array(), missing_reasons: identity.array(), original_count: count,
    excluded_source_count: count, scan_truncated: z.boolean(), data_pit_verified: z.boolean().nullable(),
    probability: z.null(), probability_qualified: z.literal(false),
    statistical_status: z.literal('insufficient_evidence'), promotion: z.literal(false),
  }).strict().array().length(3),
}).strict().superRefine((value, context) => {
  if (value.scope_id !== `crypto:spot:okx:${value.instrument}:trade:1m`
    || new Set(value.routes.map(item => item.route)).size !== 3) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '补充数据与当前资产身份无法核对' })
  }
})
export type EnhancementView = z.infer<typeof enhancementViewSchema>
export const marketEnhancementsApi = {
  summary: async (scopeId: string, signal?: AbortSignal): Promise<EnhancementView> => {
    const value = enhancementViewSchema.parse(await request<unknown>(
      `/v1/risk/scopes/${encodeURIComponent(scopeId)}/enhancements`, { signal },
    ))
    if (value.scope_id !== scopeId) throw new Error('补充数据与当前资产不一致。')
    return value
  },
}
export function enhancementErrorMessage(_error: unknown) {
  return '暂时无法核对补充数据，请稍后刷新。当前数据不具备预测优势的证明。'
}
