import { z } from 'zod'
import { researchMethodViewV2Schema } from '@decision-hub/contracts-ts'
import { request } from './client'
import { ACTION_OWNER } from './actionPlans'

const receipt = z.object({
  stage: z.enum(['selected', 'loaded', 'executed', 'consumed']), node_id: z.string(),
  event_ref: z.object({ event_id: z.string().min(1), payload_hash: z.string().regex(/^[a-f0-9]{64}$/) }).strict(),
  first_known_at: z.string().datetime({ offset: true }),
}).strict()
const method = z.object({
  name: z.string(), purpose: z.string(), skill_version_id: z.string(), version_hash: z.string().regex(/^[a-f0-9]{64}$/),
  origin: z.literal('baseline_registered'), financial_effect: z.literal('unverified'),
  generation: z.number().int().positive(), selected: z.boolean(), loaded: z.boolean(),
  executed: z.boolean(), consumed: z.boolean(), required_nodes: z.array(z.string()),
  executed_nodes: z.array(z.string()), receipts: z.array(receipt),
}).strict()
export const legacyMethodViewSchema = z.object({
  schema_version: z.literal('research-method-view.v1'), run_id: z.string(), revision_id: z.string().nullable(),
  run_status: z.string(), current_generation: z.number().int().positive().nullable(),
  producer_generation: z.number().int().positive().nullable(),
  status: z.enum(['not_selected', 'authenticated']),
  adoption: z.enum(['not_bound_to_action', 'adopted', 'not_adopted', 'generation_unverified']),
  methods: z.array(method), gaps: z.array(z.string()),
}).strict()
export const methodViewSchema = z.union([legacyMethodViewSchema, researchMethodViewV2Schema])
export type MethodView = z.infer<typeof methodViewSchema>

export const actionMethodsApi = {
  async read(runId: string, revisionId: string, signal?: AbortSignal): Promise<MethodView> {
    const query = new URLSearchParams({ revision_id: revisionId })
    const view = methodViewSchema.parse(await request(`/v1/research/runs/${encodeURIComponent(runId)}/methods?${query}`, {
      signal, headers: { 'X-Owner-Id': ACTION_OWNER },
    }))
    if (view.run_id !== runId || view.revision_id !== revisionId) throw new Error('method_response_identity_mismatch')
    return view
  },
}
