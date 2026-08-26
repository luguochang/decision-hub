import { z } from 'zod'

export const runStatusSchema = z.enum(['admitted', 'running', 'completed', 'degraded', 'failed', 'cancelled'])
export const gateStatusSchema = z.enum(['publish', 'degraded', 'research_only', 'reject'])
export const directionSchema = z.enum(['long', 'short', 'neutral', 'no_trade'])

export const forecastSchema = z.object({
  forecast_id: z.string(), artifact_id: z.string(), instrument: z.string(), horizon: z.string(),
  direction: directionSchema, probability: z.number().min(0).max(1), trigger: z.string(),
  invalidation: z.string(), expires_at: z.string(),
})

export const runViewSchema = z.object({
  run_id: z.string(), event_id: z.string(), status: runStatusSchema, strategy_version: z.string(),
  snapshot_id: z.string().nullable(), artifact_id: z.string().nullable(), created_at: z.string(),
  updated_at: z.string(), finished_at: z.string().nullable(), latency_ms: z.number().nullable(),
  cost_usd: z.number(), error_code: z.string().nullable(), headline: z.string().nullable(),
  gate_status: gateStatusSchema.nullable(),
})

export const artifactViewSchema = z.object({
  artifact_id: z.string(), run_id: z.string(), event_id: z.string(), gate_status: gateStatusSchema,
  headline: z.string(), summary: z.string(), facts: z.array(z.string()), inferences: z.array(z.string()),
  counter_thesis: z.string(), uncertainty: z.array(z.string()), transmission_chain: z.array(z.string()),
  citations: z.array(z.string()), forecasts: z.array(forecastSchema),
  gate_decisions: z.array(z.object({ rule_id: z.string(), status: z.string(), reason_code: z.string(), input_hash: z.string() })),
  created_at: z.string(),
})

export const deskSummarySchema = z.object({
  inbox: z.object({ pending_count: z.number(), running_count: z.number(), latest: z.array(runViewSchema) }),
  published_count_30d: z.number(), forecast_count: z.number(), evaluated_count: z.number(),
  health_status: z.string(), active_strategy: z.string(), active_pack: z.string(),
})

export type Forecast = z.infer<typeof forecastSchema>
export type GateStatus = z.infer<typeof gateStatusSchema>
export type RunView = z.infer<typeof runViewSchema>
export type ArtifactView = z.infer<typeof artifactViewSchema>
export type DeskSummary = z.infer<typeof deskSummarySchema>
