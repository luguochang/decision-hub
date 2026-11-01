import { z } from 'zod'
import { perpetualPlanWatchCommandV1Schema, perpetualPlanWatchReceiptV1Schema, perpetualResearchPaperPartitionV3Schema, type PerpetualPlanWatchCommandV1 } from '../../../../packages/contracts_ts/src/generated/r2'
import type { PerpetualAdviceSelection, PerpetualAdviceView } from './perpetualAdvice'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import { settingsApi } from './decisionSettings'
import { request } from './client'

const base = '/v1/decision-settings/plans/watches'
const expirySchema = z.object({ schema_version: z.literal('perpetual-plan-expiry.v1'), watch_id: z.string(), watch_ref: z.object({ ref: z.string(), hash: z.string() }).strict(), expired_at: z.string(), observed_at: z.string(), status: z.enum(['expired', 'settings_or_revision_changed', 'slot_missed']), outbox_id: z.number().int(), request_id: z.string().nullable() }).strict()
const viewSchema = z.object({ schema_version: z.literal('perpetual-plan-watch-view.v1'), receipt: perpetualPlanWatchReceiptV1Schema.nullable(), expiry: expirySchema.nullable() }).strict()
export type PlanWatchView = z.infer<typeof viewSchema>
const continuousCommandSchema = z.object({ schema_version: z.enum(['perpetual-plan-watch-command.v2', 'perpetual-plan-watch-command.v3']), continuation: z.literal('follow_published_revisions'), condition_research: z.boolean().optional(),
  partition: perpetualResearchPaperPartitionV3Schema, input_policy: z.literal('saved_preferences_current_paper_ledger.v1') }).passthrough().transform((raw, ctx) => {
  const { continuation, input_policy, condition_research, ...body } = raw
  const v3 = raw.schema_version === 'perpetual-plan-watch-command.v3'
  if (v3 ? typeof condition_research !== 'boolean' : condition_research !== undefined) { ctx.addIssue({ code: 'custom', message: 'invalid_condition_research_consent' }); return z.NEVER }
  const parsed = perpetualPlanWatchCommandV1Schema.safeParse({ ...body, schema_version: 'perpetual-plan-watch-command.v1' })
  if (!parsed.success) { ctx.addIssue({ code: 'custom', message: 'invalid_continuous_command_fields' }); return z.NEVER }
  return { ...parsed.data, schema_version: raw.schema_version, continuation, input_policy, ...(v3 ? { condition_research: condition_research! } : {}) }
})
const continuousReceiptSchema = z.object({ schema_version: z.enum(['perpetual-plan-watch-receipt.v2', 'perpetual-plan-watch-receipt.v3']), command: continuousCommandSchema }).passthrough().transform((raw, ctx) => {
  const { continuation: _continuation, input_policy: _inputPolicy, condition_research: _conditionResearch, ...legacyCommand } = raw.command
  if ((raw.schema_version === 'perpetual-plan-watch-receipt.v3') !== (raw.command.schema_version === 'perpetual-plan-watch-command.v3')) { ctx.addIssue({ code: 'custom', message: 'invalid_continuous_receipt_version' }); return z.NEVER }
  const parsed = perpetualPlanWatchReceiptV1Schema.safeParse({ ...raw, schema_version: 'perpetual-plan-watch-receipt.v1', command: { ...legacyCommand, schema_version: 'perpetual-plan-watch-command.v1' } })
  if (!parsed.success) { ctx.addIssue({ code: 'custom', message: 'invalid_continuous_receipt_fields' }); return z.NEVER }
  return { ...parsed.data, schema_version: raw.schema_version, command: raw.command }
})
const refSchema = z.object({ ref: z.string().min(1), hash: z.string().regex(/^[a-f0-9]{64}$/) }).strict()
const progressSchema = z.object({ schema_version: z.literal('perpetual-plan-watch-progress.v1'), owner_id: z.string(), watch_id: z.string(), watch_ref: refSchema,
  sequence: z.number().int().positive(), status: z.enum(['following', 'expired', 'slot_missed', 'settings_changed', 'paused', 'invalidated']),
  observed_at: z.string().datetime({ offset: true }), plan_id: z.string(), published_at: z.string().datetime({ offset: true }), review_due_at: z.string().datetime({ offset: true }),
  auto_review_slot: z.string().datetime({ offset: true }).nullable(), result_hash: z.string().regex(/^[a-f0-9]{64}$/), request_id: z.string().nullable(), outbox_ids: z.array(z.number().int()),
  origin_run_id: z.string(), origin_artifact_id: z.string(), plan_revision_ref: refSchema, snapshot_ref: refSchema, comparison_ref: refSchema, frozen_forecast_ref: refSchema,
  publication_ref: refSchema, payload_hash: z.string().regex(/^[a-f0-9]{64}$/) }).strict()
const conditionSchema = z.object({ assumption_id: z.string().min(1), assumption_ref: refSchema.refine(value => value.ref === `decision_model_assumption_${value.hash}`, 'invalid_assumption_ref'),
  purpose: z.enum(['confirmation', 'invalidation']).nullable(), text: z.string(), truth: z.enum(['true', 'false', 'unknown']),
  status: z.enum(['unmonitored', 'waiting', 'confirmed', 'invalidated', 'not_confirmed', 'unknown']),
  streak: z.number().int().nonnegative(), required_samples: z.number().int().positive().nullable(),
  last_bar_at: z.string().datetime({ offset: true }).nullable(), observed_at: z.string().datetime({ offset: true }).nullable(),
  reason_codes: z.array(z.string()), request_id: z.string().nullable(), outbox_ids: z.array(z.number().int()) }).strict()
const conditionMonitoringSchema = z.object({ schema_version: z.literal('perpetual-condition-monitoring-view.v1'),
  source_run_id: z.string().min(1), plan_revision_ref: refSchema, state: z.enum(['unregistered', 'monitoring', 'unknown', 'invalidated']),
  new_exposure_blocked: z.boolean(), observed_at: z.string().datetime({ offset: true }).nullable(), reason_codes: z.array(z.string()).default([]),
  conditions: z.array(conditionSchema) }).strict()
const continuousViewV2Schema = z.object({ schema_version: z.literal('perpetual-plan-watch-view.v2'), receipt: continuousReceiptSchema.nullable(), progress: progressSchema.nullable() }).strict()
const continuousViewSchema = z.discriminatedUnion('schema_version', [continuousViewV2Schema,
  continuousViewV2Schema.extend({ schema_version: z.literal('perpetual-plan-watch-view.v3'), condition_monitoring: conditionMonitoringSchema.nullable() })])
export type ContinuousPlanWatchView = z.infer<typeof continuousViewSchema>
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b)
function active(selection: PerpetualAdviceSelection) {
  const session = settingsApi.session()
  if (!session || session.owner_id !== selection.owner_id) throw new Error('perpetual_watch_owner_mismatch')
  return session
}
async function checked(raw: unknown, selection: PerpetualAdviceSelection, value: PerpetualAdviceView) {
  const receipt = perpetualPlanWatchReceiptV1Schema.parse(raw), { revision, payload_hash, ...body } = receipt
  if (!value.revision || receipt.owner_id !== selection.owner_id || !same(receipt.command.partition, selection.partition)
    || receipt.command.horizon !== selection.horizon || receipt.command.plan_id !== value.revision.plan_id
    || !same(receipt.plan_revision_ref, { ref: value.revision.revision_id, hash: value.revision.payload_hash })
    || !same(receipt.command.expected_revision_ref, receipt.plan_revision_ref)
    || !same(receipt.command.expected_settings_revision, selection.settings_revision)
    || receipt.command.expected_settings_state_version !== selection.settings_state_version
    || receipt.command_hash !== await hash(receipt.command) || payload_hash !== await hash(body) || revision.hash !== payload_hash
    || receipt.watch_id !== 'perpwatch_' + await hash([receipt.owner_id, receipt.command.plan_id, receipt.plan_revision_ref])) throw new Error('perpetual_watch_original_mismatch')
  return receipt
}
async function checkedContinuous(raw: unknown, selection: PerpetualAdviceSelection) {
  const receipt = continuousReceiptSchema.parse(raw), { revision, payload_hash, ...body } = receipt, cmd = receipt.command
  if (receipt.owner_id !== selection.owner_id || !same(cmd.partition, selection.partition) || cmd.horizon !== selection.horizon
    || !same(cmd.expected_settings_revision, selection.settings_revision) || cmd.expected_settings_state_version !== selection.settings_state_version
    || receipt.request_id !== cmd.request_id || !same(receipt.plan_revision_ref, cmd.expected_revision_ref)
    || receipt.state_version !== cmd.expected_state_version + 1 || receipt.state !== (cmd.operation === 'pause' ? 'paused' : 'active')
    || receipt.command_hash !== await hash(cmd) || payload_hash !== await hash(body) || revision.hash !== payload_hash
    || revision.ref !== 'perpwatchcontinuouscmd_' + await hash([receipt.owner_id, receipt.request_id])
    || receipt.watch_id !== 'perpwatchcontinuous_' + await hash([receipt.owner_id, cmd.partition, cmd.horizon, cmd.expected_settings_revision])) throw new Error('perpetual_continuous_original_mismatch')
  return receipt
}
export const planWatchApi = {
  continuousCurrent: async (selection: PerpetualAdviceSelection) => {
    active(selection)
    const query = new URLSearchParams({ ...selection.partition, horizon: selection.horizon, settings_ref: selection.settings_revision.ref, settings_hash: selection.settings_revision.hash })
    const view = continuousViewSchema.parse(await request(`${base}/continuous/current?${query}`, { credentials: 'same-origin' }))
    const monitoring = view.schema_version === 'perpetual-plan-watch-view.v3' ? view.condition_monitoring : null
    if (view.receipt) {
      await checkedContinuous(view.receipt, selection)
      if (view.progress) {
        const { payload_hash, ...body } = view.progress
        if (view.progress.owner_id !== selection.owner_id || view.progress.watch_id !== view.receipt.watch_id
          || !same(view.progress.watch_ref, view.receipt.revision) || payload_hash !== await hash(body)) throw new Error('perpetual_continuous_progress_mismatch')
      }
      const source = view.progress ?? view.receipt
      if (monitoring && (monitoring.source_run_id !== source.origin_run_id || !same(monitoring.plan_revision_ref, source.plan_revision_ref))) throw new Error('perpetual_continuous_conditions_mismatch')
    } else if (view.progress || monitoring) throw new Error('perpetual_continuous_original_missing')
    return view
  },
  saveContinuous: async (selection: PerpetualAdviceSelection, value: PerpetualAdviceView, previous: ContinuousPlanWatchView | undefined, operation: 'watch' | 'pause', research: boolean) => {
    const session = active(selection), r = value.revision!, prior = previous?.receipt
    const legacyPause = operation === 'pause' && prior?.command.schema_version === 'perpetual-plan-watch-command.v2'
    const command = continuousCommandSchema.parse({ schema_version: legacyPause ? 'perpetual-plan-watch-command.v2' : 'perpetual-plan-watch-command.v3', continuation: 'follow_published_revisions', input_policy: 'saved_preferences_current_paper_ledger.v1', request_id: crypto.randomUUID(),
      ...(!legacyPause ? { condition_research: operation === 'pause' && prior ? prior.command.condition_research === true : research } : {}),
      partition: selection.partition, horizon: selection.horizon, plan_id: operation === 'pause' && prior ? prior.command.plan_id : r.plan_id,
      expected_revision_ref: operation === 'pause' && prior ? prior.plan_revision_ref : { ref: r.revision_id, hash: r.payload_hash },
      expected_state_version: prior?.state_version ?? 0, expected_settings_revision: selection.settings_revision, expected_settings_state_version: selection.settings_state_version,
      operation, mode: operation === 'pause' && prior ? prior.command.mode : research ? 'remind_and_research' : 'remind_only' })
    const raw = await request(`${base}/continuous/commands`, { credentials: 'same-origin', method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Settings-CSRF': session.csrf_token, 'Idempotency-Key': command.request_id }, body: JSON.stringify(command) })
    const receipt = await checkedContinuous(raw, selection)
    if (!same(receipt.command, command)) throw new Error('perpetual_continuous_command_mismatch')
    return receipt
  },
  current: async (selection: PerpetualAdviceSelection, value: PerpetualAdviceView) => {
    active(selection)
    const r = value.revision!
    const view = viewSchema.parse(await request(`${base}/current?${new URLSearchParams({ plan_id: r.plan_id, revision_ref: r.revision_id, revision_hash: r.payload_hash })}`, { credentials: 'same-origin' }))
    if (view.receipt) {
      await checked(view.receipt, selection, value)
      if (view.expiry && (view.expiry.watch_id !== view.receipt.watch_id || view.expiry.expired_at !== view.receipt.review_due_at)) throw new Error('perpetual_watch_expiry_mismatch')
    } else if (view.expiry) throw new Error('perpetual_watch_original_missing')
    return view
  },
  save: async (selection: PerpetualAdviceSelection, value: PerpetualAdviceView, previous: PlanWatchView | undefined, operation: 'watch' | 'pause', research: boolean) => {
    const session = active(selection), r = value.revision!
    const command: PerpetualPlanWatchCommandV1 = perpetualPlanWatchCommandV1Schema.parse({ schema_version: 'perpetual-plan-watch-command.v1', request_id: crypto.randomUUID(), partition: selection.partition, horizon: selection.horizon, plan_id: r.plan_id,
      expected_revision_ref: { ref: r.revision_id, hash: r.payload_hash }, expected_state_version: previous?.receipt?.state_version ?? 0,
      expected_settings_revision: selection.settings_revision, expected_settings_state_version: selection.settings_state_version, operation, mode: research ? 'remind_and_research' : 'remind_only' })
    const raw = await request(`${base}/commands`, { credentials: 'same-origin', method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Settings-CSRF': session.csrf_token, 'Idempotency-Key': command.request_id }, body: JSON.stringify(command) })
    const receipt = await checked(raw, selection, value)
    if (!same(receipt.command, command)) throw new Error('perpetual_watch_command_mismatch')
    return receipt
  },
}
