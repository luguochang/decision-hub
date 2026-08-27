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
  cost_usd: z.number().nullable(), error_code: z.string().nullable(), headline: z.string().nullable(),
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

export const callViewSchema = z.object({
  call_id: z.string(), run_id: z.string(), role: z.string(), status: z.string(), attempt: z.number(),
  started_at: z.string(), finished_at: z.string().nullable(), latency_ms: z.number().nullable(),
  runtime_id: z.string().nullable(), runtime_version: z.string().nullable(), provider_id: z.string().nullable(),
  model: z.string().nullable(), api_mode: z.string().nullable(), schema_version: z.string().nullable(),
  prompt_tokens: z.number().nullable(), completion_tokens: z.number().nullable(), total_tokens: z.number().nullable(),
  cost_usd: z.number().nullable(), cost_status: z.string(), pricing_version: z.string().nullable(),
  error_code: z.string().nullable(), retryable: z.boolean(),
})

export const stepViewSchema = z.object({
  step_id: z.string(), run_id: z.string(), step_name: z.string(), status: z.string(),
  attempt: z.number(), started_at: z.string(), finished_at: z.string().nullable(),
  latency_ms: z.number().nullable(), error_code: z.string().nullable(),
})

export const runInspectorSchema = z.object({
  run: runViewSchema,
  timeline: z.array(z.record(z.string(), z.unknown())),
  steps: z.array(stepViewSchema),
  calls: z.array(callViewSchema),
  artifact: artifactViewSchema.nullable(),
  evaluation_count: z.number(),
  evaluations: z.array(z.object({
    evaluation_id: z.string(), forecast_id: z.string(), brier_score: z.number(),
    net_return_pct: z.number(), direction_correct: z.boolean(), label_status: z.string(), evaluated_at: z.string(),
  })),
  snapshot_cutoff_at: z.string().nullable(),
  snapshot_hash: z.string().nullable(),
})

export type Forecast = z.infer<typeof forecastSchema>
export type GateStatus = z.infer<typeof gateStatusSchema>
export type RunView = z.infer<typeof runViewSchema>
export type ArtifactView = z.infer<typeof artifactViewSchema>
export type DeskSummary = z.infer<typeof deskSummarySchema>
export type CallView = z.infer<typeof callViewSchema>
export type StepView = z.infer<typeof stepViewSchema>
export type RunInspector = z.infer<typeof runInspectorSchema>
