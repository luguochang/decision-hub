import { z } from 'zod'
import { researchSessionResultSchema } from '@decision-hub/contracts-ts'
import { ApiRequestError, requestResponse, type RequestResponseOptions } from './client'

const id = z.string().min(1)
const hash = z.string().regex(/^[a-f0-9]{64}$/)
const at = z.string().datetime({ offset: true })
const ref = z.object({ output_id: id, role: id, schema_version: id, payload_hash: hash, recorded_at: at }).strict()
const effect = z.enum(['unchanged', 'increased', 'reduced', 'reversed', 'unknown'])
const scope = z.enum(['direction', 'path_risk', 'execution', 'report'])
const challenge = z.object({
  schema_version: z.literal('research-challenge.v1'), challenge_id: id, source_review_id: id.nullable(),
  severity: z.enum(['critical', 'important', 'minor']), blocking_scope: scope.array().min(1), evidence_refs: id.array(),
  status: z.enum(['open', 'answered', 'rejected', 'deferred']), disposition: z.enum(['accepted', 'rebutted', 'unresolved']),
  resolution_evidence_refs: id.array(), resolved_result_hash: hash.nullable(), effect_on_direction: effect,
  effect_on_tail_risk: effect, effect_on_monitor: effect, reason: z.string(), reviewer_version: z.string(),
  effect_on_report: effect.optional(),
}).strict()
const decision = z.object({
  schema_version: z.literal('quality-decision.v1'), run_id: id, scope_id: id, source_result_hash: hash,
  full_report_hash: hash, full_report_json: id, status: z.enum(['continue', 'sufficient', 'partial', 'unresolved']),
  should_continue: z.boolean(), blocks_completion: z.boolean(), publication_mode: z.enum(['complete', 'partial', 'research_only']),
  factual_status: z.enum(['complete', 'incomplete']), direction_status: z.enum(['eligible', 'blocked', 'unknown']),
  path_risk_status: z.enum(['eligible', 'blocked', 'unknown']), execution_status: z.enum(['supported', 'blocked', 'unknown']),
  context_status: z.enum(['complete', 'incomplete']), report_status: z.enum(['complete', 'incomplete', 'untrusted']),
  monitor_state: z.enum(['active', 'paused', 'degraded', 'unavailable', 'unknown']), unresolved_challenge_ids: id.array(),
  reason_codes: id.array(), retained_evidence_refs: id.array(), challenges: challenge.array(),
  key_risks: z.object({ risk_id: id, statement: id, evidence_refs: id.array(), unknown_reason: z.string().nullable(), affected_scope: scope.array().min(1) }).strict().array(),
  unknowns: z.string().array(), unmapped_inquiries: z.string().array(), decision_hash: hash,
}).strict()
const budget = z.object({
  status: z.literal('prepared_limits_only'), max_tool_calls: z.number().int().nonnegative(),
  max_subagents: z.number().int().nonnegative(), max_evidence_rounds: z.number().int().nonnegative(),
  max_estimated_cost_usd: z.number().finite().nonnegative(), deadline_at: at,
  actual_total_tokens: z.null(), actual_estimated_cost_usd: z.null(),
}).strict()

const count = z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER)
const dispatchUsage = z.object({
  generation: count.min(1), root_session_id: id, execution_session_id: id, parent_session_id: id.nullable(),
  dispatch_id: id, provider: id, model: id, input_ref: ref, finish_ref: ref.nullable(),
  finish_status: z.enum(['completed', 'interrupted', 'cancelled', 'error']).nullable(),
  usage_status: z.enum(['known', 'unknown']), input_tokens: count.nullable(), output_tokens: count.nullable(),
  total_tokens: count.nullable(), missing_codes: id.array(),
}).strict().superRefine((value, context) => {
  const tokens = [value.input_tokens, value.output_tokens, value.total_tokens]
  if (value.usage_status === 'known'
    ? tokens.some(token => token === null) || value.finish_ref === null
      || value.finish_status !== 'completed' || value.missing_codes.length > 0
      || value.total_tokens! < value.input_tokens! + value.output_tokens!
    : tokens.some(token => token !== null) || value.missing_codes.length === 0) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '调用用量与原件覆盖无法核对' })
  }
})

export const providerConversationUsageCensusSchema = z.object({
  schema_version: z.literal('research-provider-conversation-usage.v1'), run_id: id, as_of: at,
  scope: z.literal('conversation_only'), excluded_purposes: z.tuple([z.literal('compaction'), z.literal('title')]),
  coverage_status: z.enum(['complete', 'incomplete']), token_status: z.enum(['known', 'unknown']),
  cost_status: z.literal('unknown'), expected_dispatches: count.nullable(), observed_dispatches: count,
  root_dispatches: count, child_dispatches: count, covered_dispatches: count,
  input_tokens: count.nullable(), output_tokens: count.nullable(), total_tokens: count.nullable(), cost_usd: z.null(),
  missing_codes: id.array(), missing_sessions: id.array(), dispatches: dispatchUsage.array(), source_refs: ref.array(),
  census_hash: hash, proof_scope: z.literal('dsh_llm_generate_options'), model_understanding_verified: z.literal(false),
  budget_qualified: z.literal(false), financial_qualified: z.literal(false),
}).strict().superRefine((value, context) => {
  const totals = [value.input_tokens, value.output_tokens, value.total_tokens]
  const complete = value.expected_dispatches !== null && value.expected_dispatches > 0
    && value.expected_dispatches === value.observed_dispatches && value.observed_dispatches === value.covered_dispatches
  const sums = value.dispatches.reduce((sum, item) => [sum[0] + (item.input_tokens ?? 0),
    sum[1] + (item.output_tokens ?? 0), sum[2] + (item.total_tokens ?? 0)], [0, 0, 0])
  const inputs = value.dispatches.map(item => item.input_ref.output_id)
  const finishes = value.dispatches.flatMap(item => item.finish_ref ? [item.finish_ref.output_id] : [])
  const proofs = new Map(value.source_refs.map(item => [item.output_id, item]))
  const sameProof = (item: z.infer<typeof ref>) => {
    const source = proofs.get(item.output_id)
    return source !== undefined && source.role === item.role && source.schema_version === item.schema_version
      && source.payload_hash === item.payload_hash && source.recorded_at === item.recorded_at
  }
  if (value.observed_dispatches !== value.dispatches.length
    || value.root_dispatches + value.child_dispatches !== value.observed_dispatches
    || value.root_dispatches !== value.dispatches.filter(item => item.execution_session_id === item.root_session_id).length
    || value.covered_dispatches !== value.dispatches.filter(item => item.usage_status === 'known').length
    || (value.coverage_status === 'complete' ? !complete || value.token_status !== 'known'
      || value.missing_sessions.length > 0 || value.missing_codes.length !== 1
      || value.missing_codes[0] !== 'provider_pricing_authority_unavailable'
      : value.token_status !== 'unknown' || value.missing_codes.length === 0)
    || new Set(inputs).size !== inputs.length || new Set(finishes).size !== finishes.length
    || new Set(value.dispatches.map(item => JSON.stringify([item.generation, item.root_session_id,
      item.execution_session_id, item.dispatch_id]))).size !== value.dispatches.length
    || new Set(value.source_refs.map(item => item.output_id)).size !== value.source_refs.length
    || value.dispatches.some(item => !sameProof(item.input_ref) || (item.finish_ref !== null && !sameProof(item.finish_ref)))
    || value.source_refs.some(item => Date.parse(item.recorded_at) > Date.parse(value.as_of))
    || (value.token_status === 'known'
      ? value.coverage_status !== 'complete' || totals.some(token => token === null)
        || value.dispatches.some(item => item.usage_status !== 'known')
        || totals.some((token, index) => token !== sums[index])
      : totals.some(token => token !== null))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '对话用量尚未形成完整的原件对账' })
  }
})
export type ProviderConversationUsageCensus = z.infer<typeof providerConversationUsageCensusSchema>

export const researchQualitySchema = z.object({
  schema_version: z.literal('research-quality-view.v1'), run_id: id, as_of: at,
  status: z.enum(['available', 'unavailable']), reason_code: id.nullable(),
  gate_status: z.enum(['publish', 'degraded', 'research_only', 'reject']).nullable(), published_at: at.nullable(),
  publication_ref: ref.nullable(), graph_final_ref: ref.nullable(), recipe_selection_ref: ref.nullable(),
  quality_sidecar_id: id.nullable(), decision: decision.nullable(), budget: budget.nullable(),
  unknown_receipt_codes: id.array(), usage: providerConversationUsageCensusSchema.nullable().optional(),
}).strict().superRefine((value, context) => {
  const bound = value.decision !== null && value.publication_ref !== null && value.graph_final_ref !== null
    && value.recipe_selection_ref !== null && value.quality_sidecar_id !== null && value.budget !== null
    && value.gate_status !== null && value.published_at !== null
  if ((value.status === 'available') !== bound || (value.status === 'available') !== (value.reason_code === null)
    || (value.decision !== null && value.decision.run_id !== value.run_id)
    || (value.usage != null && (value.usage.run_id !== value.run_id || value.status !== 'available'))
    || (value.status === 'unavailable' && [value.decision, value.publication_ref, value.graph_final_ref,
      value.recipe_selection_ref, value.quality_sidecar_id, value.budget, value.gate_status, value.published_at].some(item => item !== null))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '质量结论与发布原件无法核对' })
  }
})
export type ResearchQualityView = z.infer<typeof researchQualitySchema>

export type ResearchQualityServingRead = {
  run_id: string
  read_mode: 'confirmed_bundle' | 'confirmed_report_snapshot' | 'legacy_full_original' | 'unknown'
  state_version: number | null
  checked_at: string
}
export type ResearchQualityReadOptions = RequestResponseOptions & {
  onServingRead?: (read: ResearchQualityServingRead) => void
  preferSnapshot?: boolean
}
export type ResearchQualityFullAudit = {
  view: ResearchQualityView
  state_version: number
  comparison: 'saved_bundle_full_quality' | 'legacy_original_only'
  checked_at: string
}

export const researchReportServingSchema = z.object({
  run_id: id,
  state: z.enum(['queued', 'prepared', 'confirmed', 'unconfirmed', 'cancelled', 'revoked', 'legacy_full_original']),
  state_version: z.number().int().nonnegative(),
  confirmed_at: at.nullable(),
  reason_code: z.string().regex(/^[a-z0-9_]{1,160}$/).nullable(),
}).strict().superRefine((value, context) => {
  if ((value.state === 'confirmed' && (value.confirmed_at === null || value.state_version === 0))
    || (value.state === 'legacy_full_original' && (value.state_version !== 0 || value.confirmed_at !== null || value.reason_code !== null))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '报告读取状态无法核对' })
  }
})
export type ResearchReportServingStatus = z.infer<typeof researchReportServingSchema>
export const reportRecoveryStatusSchema = z.object({
  schema_version: z.literal('research-report-recovery-status.v1'), run_id: id, request_id: id,
  generation: count, state_version: count.min(1),
  state: z.enum(['queued', 'prepared', 'confirmed', 'unconfirmed', 'cancelled', 'revoked']),
  reason_code: z.string().regex(/^[a-z0-9_]{1,160}$/).nullable(),
  source_abi: z.literal('publication-source-snapshot-read.v2').nullable(),
  action: z.enum(['not_applicable', 'owner_request_required', 'new_research_required', 'review_required']),
  proof_scope: z.literal('metadata_prechecks_only'),
}).strict().superRefine((value, context) => {
  if ((value.action === 'owner_request_required' && ((value.state !== 'cancelled' && !(value.state === 'unconfirmed' && value.reason_code === 'frozen_io_root_data_fallback'))
    || value.source_abi !== 'publication-source-snapshot-read.v2'))
    || (value.action === 'new_research_required' && (value.state !== 'cancelled' || value.source_abi !== null))) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '报告恢复状态与读取能力无法核对' })
  }
})
export type ReportRecoveryStatus = z.infer<typeof reportRecoveryStatusSchema>

const configuredTimeout = Number(import.meta.env.VITE_QUALITY_REQUEST_TIMEOUT_MS)
export const QUALITY_REQUEST_TIMEOUT_MS = Number.isSafeInteger(configuredTimeout) && configuredTimeout > 0 ? configuredTimeout : 45_000

const readErrors = {
  timeout: '读取质量原件超时，尚未确认核验结果。可重新读取，或继续阅读已保存报告。',
  network: '连接中断，尚未确认质量结果。请检查连接后重新读取。',
  verification: '质量原件的格式或身份暂未通过核验，当前无法确认结论资格。',
  unavailable: '质量原件暂时无法读取，当前无法确认结论资格。请稍后重新读取。',
}
export class ResearchQualityReadError extends Error {
  constructor(readonly kind: keyof typeof readErrors) {
    super(readErrors[kind])
    this.name = 'ResearchQualityReadError'
  }
}

export class ResearchReportServingError extends Error {
  constructor(readonly servingStatus: ResearchReportServingStatus) {
    super('报告正在准备或尚未确认，请读取最新状态。')
    this.name = 'ResearchReportServingError'
  }
}

async function readServingStatus(runId: string, signal?: AbortSignal): Promise<ResearchReportServingStatus> {
  const value = researchReportServingSchema.parse(await requestResponse<unknown>(
    `/v1/research/runs/${encodeURIComponent(runId)}/report/status`, response => response.json(), { signal },
  ))
  if (value.run_id !== runId) throw new ResearchQualityReadError('verification')
  return value
}

export function researchQualityReadErrorText(error: unknown): string {
  return error instanceof ResearchQualityReadError ? error.message : readErrors.unavailable
}

const confirmedReportSnapshotSchema = z.object({
  schema_version: z.literal('research-confirmed-report-snapshot.v1'),
  read_scope: z.literal('confirmed_report_snapshot'),
  material_audit: z.literal('not_checked_this_read'),
  run_id: id,
  state_version: count.min(1),
  source_bundle_sha256: hash,
  published_at: at,
  as_of: at,
  report_sha256: hash,
  report_bytes: count.min(1),
  quality_json: id,
}).strict()

async function readConfirmedReportSnapshot(runId: string, signal?: AbortSignal, options?: ResearchQualityReadOptions): Promise<ResearchQualityView> {
  try {
    const response = await requestResponse(`/v1/research/runs/${encodeURIComponent(runId)}/report/snapshot`, async result => ({
      raw: await result.json() as unknown,
      mode: result.headers.get('X-Decision-Hub-Report-Read-Mode'),
      version: result.headers.get('X-Decision-Hub-Report-State-Version'),
      audit: result.headers.get('X-Decision-Hub-Report-Material-Audit'),
    }), { signal }, undefined, { timeoutMs: options?.timeoutMs ?? QUALITY_REQUEST_TIMEOUT_MS })
    const snapshot = confirmedReportSnapshotSchema.parse(response.raw)
    const view = researchQualitySchema.parse(JSON.parse(snapshot.quality_json))
    if (snapshot.run_id !== runId || view.run_id !== runId || view.status !== 'available' || !view.decision
      || response.mode !== snapshot.read_scope || response.version !== String(snapshot.state_version)
      || response.audit !== snapshot.material_audit || view.published_at !== snapshot.published_at
      || view.as_of !== snapshot.as_of || Date.parse(snapshot.as_of) < Date.parse(snapshot.published_at)
      || view.decision.full_report_hash !== snapshot.report_sha256) throw new ResearchQualityReadError('verification')
    researchSessionResultSchema.parse(JSON.parse(view.decision.full_report_json))
    if (!globalThis.crypto?.subtle) throw new ResearchQualityReadError('verification')
    const report = new TextEncoder().encode(view.decision.full_report_json)
    const digest = Array.from(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', report)))
      .map(value => value.toString(16).padStart(2, '0')).join('')
    if (report.byteLength !== snapshot.report_bytes || digest !== snapshot.report_sha256) throw new ResearchQualityReadError('verification')
    if (!signal?.aborted) options?.onServingRead?.({ run_id: runId, read_mode: snapshot.read_scope,
      state_version: snapshot.state_version, checked_at: snapshot.as_of })
    return view
  } catch (error) {
    if (signal?.aborted || error instanceof ResearchQualityReadError) throw error
    if (error instanceof ApiRequestError) {
      if (error.kind === 'timeout' || error.kind === 'network') throw new ResearchQualityReadError(error.kind)
      if (error.status === 404 && error.message === 'research_report_snapshot_not_available') {
        const status = await readServingStatus(runId, signal)
        if (status.state !== 'legacy_full_original') throw new ResearchQualityReadError('verification')
        return researchQualityApi.read(runId, signal, { ...options, preferSnapshot: false })
      }
      if (error.status === 409 && error.message === 'research_report_snapshot_verification_failed') {
        const status = await readServingStatus(runId, signal)
        if (status.state === 'confirmed' || status.state === 'legacy_full_original') throw new ResearchQualityReadError('verification')
        throw new ResearchReportServingError(status)
      }
      throw new ResearchQualityReadError(error.status === 409 ? 'verification' : 'unavailable')
    }
    if (error instanceof z.ZodError || error instanceof SyntaxError) throw new ResearchQualityReadError('verification')
    throw new ResearchQualityReadError('unavailable')
  }
}

export const researchQualityApi = {
  recoveryStatus: async (runId: string, stateVersion: number, signal?: AbortSignal): Promise<ReportRecoveryStatus> => {
    const result = reportRecoveryStatusSchema.parse(await requestResponse<unknown>(
      `/v1/research/runs/${encodeURIComponent(runId)}/report/recovery/status`, response => response.json(), { signal }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS },
    ))
    if (result.run_id !== runId || result.state_version !== stateVersion) throw new ResearchQualityReadError('verification')
    return result
  },
  audit: async (runId: string, signal?: AbortSignal): Promise<ResearchQualityFullAudit> => {
    try {
      const result = await requestResponse(`/v1/research/runs/${encodeURIComponent(runId)}/report/audit`, async response => ({
        raw: await response.json() as unknown,
        mode: response.headers.get('X-Decision-Hub-Report-Read-Mode'),
        version: response.headers.get('X-Decision-Hub-Report-State-Version'),
        comparison: response.headers.get('X-Decision-Hub-Report-Audit-Comparison'),
      }), { method: 'POST', signal }, undefined, { timeoutMs: QUALITY_REQUEST_TIMEOUT_MS })
      const value = researchQualitySchema.parse(result.raw)
      const version = result.version !== null && /^(0|[1-9][0-9]*)$/.test(result.version) ? Number(result.version) : NaN
      if (value.run_id !== runId || value.status !== 'available' || !value.decision
        || result.mode !== 'full_original_audit' || !Number.isSafeInteger(version)
        || (result.comparison !== 'saved_bundle_full_quality' && result.comparison !== 'legacy_original_only')
        || (result.comparison === 'saved_bundle_full_quality' && version <= 0)
        || (result.comparison === 'legacy_original_only' && version !== 0)) throw new ResearchQualityReadError('verification')
      researchSessionResultSchema.parse(JSON.parse(value.decision.full_report_json))
      return { view: value, state_version: version, comparison: result.comparison, checked_at: value.as_of }
    } catch (error) {
      if (signal?.aborted || error instanceof ResearchQualityReadError) throw error
      if (error instanceof ApiRequestError) {
        if (error.kind === 'timeout' || error.kind === 'network') throw new ResearchQualityReadError(error.kind)
        if (error.status === 409 && error.message === 'research_quality_bundle_not_confirmed') {
          const status = await readServingStatus(runId, signal)
          if (status.state === 'legacy_full_original') throw new ResearchQualityReadError('verification')
          throw new ResearchReportServingError(status)
        }
        throw new ResearchQualityReadError(error.status === 409 ? 'verification' : 'unavailable')
      }
      if (error instanceof z.ZodError || error instanceof SyntaxError) throw new ResearchQualityReadError('verification')
      throw new ResearchQualityReadError('unavailable')
    }
  },
  read: async (runId: string, signal?: AbortSignal, options?: ResearchQualityReadOptions): Promise<ResearchQualityView> => {
    if (options?.preferSnapshot) return readConfirmedReportSnapshot(runId, signal, options)
    try {
      let mode: string | null = null
      let version: string | null = null
      const value = researchQualitySchema.parse(await requestResponse<unknown>(
        `/v1/research/runs/${encodeURIComponent(runId)}/quality`, response => {
          mode = response.headers.get('X-Decision-Hub-Report-Read-Mode')
          version = response.headers.get('X-Decision-Hub-Report-State-Version')
          return response.json()
        }, { signal },
        undefined, { timeoutMs: options?.timeoutMs ?? QUALITY_REQUEST_TIMEOUT_MS },
      ))
      if (value.run_id !== runId) throw new ResearchQualityReadError('verification')
      if (value.decision) researchSessionResultSchema.parse(JSON.parse(value.decision.full_report_json))
      let metadata: ResearchQualityServingRead = { run_id: runId, read_mode: 'unknown', state_version: null, checked_at: value.as_of }
      if (mode !== null || version !== null) {
        const stateVersion = version !== null && /^(0|[1-9][0-9]*)$/.test(version) ? Number(version) : NaN
        if (!Number.isSafeInteger(stateVersion) || (mode !== 'confirmed_bundle' && mode !== 'legacy_full_original')
          || (mode === 'confirmed_bundle' && (stateVersion <= 0 || value.status !== 'available'))
          || (mode === 'legacy_full_original' && stateVersion !== 0)) throw new ResearchQualityReadError('verification')
        metadata = { run_id: runId, read_mode: mode, state_version: stateVersion, checked_at: value.as_of }
      }
      if (!signal?.aborted) options?.onServingRead?.(metadata)
      return value
    } catch (error) {
      if (signal?.aborted) throw error
      if (error instanceof ResearchQualityReadError) throw error
      if (error instanceof ApiRequestError) {
        if (error.kind === 'timeout' || error.kind === 'network') throw new ResearchQualityReadError(error.kind)
        // A durable preparation is a normal state, not damaged originals.
        // This second GET reads metadata only; no creation or automatic retry.
        if (error.status === 409 && error.message === 'research_quality_bundle_not_confirmed') {
          try {
            const status = await readServingStatus(runId, signal)
            if (status.state === 'legacy_full_original') throw new ResearchQualityReadError('verification')
            throw new ResearchReportServingError(status)
          } catch (statusError) {
            if (signal?.aborted || statusError instanceof ResearchReportServingError || statusError instanceof ResearchQualityReadError) throw statusError
            if (statusError instanceof ApiRequestError && (statusError.kind === 'timeout' || statusError.kind === 'network')) throw new ResearchQualityReadError(statusError.kind)
            throw new ResearchQualityReadError(statusError instanceof z.ZodError ? 'verification' : 'unavailable')
          }
        }
        throw new ResearchQualityReadError(error.status === 409 ? 'verification' : 'unavailable')
      }
      if (error instanceof z.ZodError || error instanceof SyntaxError) throw new ResearchQualityReadError('verification')
      throw new ResearchQualityReadError('unavailable')
    }
  },
}
