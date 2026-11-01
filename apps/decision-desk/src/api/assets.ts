import {
  researchUsageViewSchema,
  dshModelDispatchBindingSchema,
  usageCollectionViewSchema,
  researchUsageSnapshotSchema, researchUsageSnapshotPageSchema,
  researchMarketCatalogSchema,
  eventAssetViewSchema,
  analyticalOutlookListViewSchema, analyticalOutlookDetailViewSchema,
  analyticalCohortListViewSchema, publicationSeparationViewSchema,
  factVersionListViewSchema, factVersionViewSchema, archivedInputViewSchema,
  studyListViewSchema, studyTimelineViewSchema, studyReportComparisonViewSchema,
  sourceHistoryViewSchema, rawCaptureViewSchema, coverageManifestSchema,
  harnessComparisonCatalogSchema, harnessComparisonViewSchema as canonicalHarnessComparisonViewSchema,
  harnessComparisonStartSchema, harnessComparisonCancelSchema, harnessOwnerSessionReplySchema, harnessOwnerSessionPairSchema,
  type HarnessComparisonCatalog, type HarnessComparisonView, type HarnessComparisonStart, type HarnessOwnerSessionReply,
} from '@decision-hub/contracts-ts'
import { z } from 'zod'
import { apiUrl, request } from './client'

export type EvidenceCapture = z.infer<typeof rawCaptureViewSchema>
export type AssetFilters = Record<string, string | number | undefined>
function query(filters: AssetFilters) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)) })
  return params.toString()
}
const usageQueueSchema = z.object({
  schema_version: z.literal('usage-queue-summary.v1'), observed_at: z.string().datetime({ offset: true }),
  session_versions: z.number().int().nonnegative(), unique_runs: z.number().int().nonnegative(),
  statuses: z.record(z.number().int().nonnegative()), due: z.number().int().nonnegative(),
  overdue_10m: z.number().int().nonnegative(), checking: z.number().int().nonnegative(),
  oldest_due_at: z.string().datetime({ offset: true }).nullable(), latest_check_at: z.string().datetime({ offset: true }).nullable(),
})
const usageQueuePageSchema = z.object({
  schema_version: z.literal('usage-queue-page.v1'), observed_at: z.string().datetime({ offset: true }),
  filter: z.string(), offset: z.number().int().nonnegative(), limit: z.number().int().positive(),
  total: z.number().int().nonnegative(), has_more: z.boolean(),
  items: z.array(z.object({ job_id: z.string(), run_id: z.string(),
    run_created_at: z.string().datetime({ offset: true }).nullable(), run_available: z.boolean(),
    generation: z.number().int(), status: z.string(), checking: z.boolean(),
    checked_at: z.string().datetime({ offset: true }).nullable(), next_check_at: z.string().datetime({ offset: true }),
    attempts: z.number().int().nonnegative(), error_code: z.string().nullable(),
  })),
})
const evolutionReadinessSchema = z.object({
  schema_version: z.literal('evolution-readiness.v1'), observed_at: z.string().datetime({ offset: true }),
  feedback_count: z.number().int().nonnegative(), actionable_feedback: z.number().int().nonnegative(),
  unregistered_feedback: z.number().int().nonnegative(), jobs: z.number().int().nonnegative(), candidates: z.number().int().nonnegative(),
  configured_samples: z.object({ counts: z.record(z.number().int().nonnegative()), cross_split_groups: z.number().int().nonnegative(), within_split_groups: z.number().int().nonnegative(), issues: z.array(z.string()) }),
  prospective_collector_available: z.boolean(),
  research_method: z.object({ research_policy_status: z.enum(['not_recorded', 'prepared', 'verified', 'invalid']).optional(), child_model_dispatch: z.object({ status: z.enum(['not_recorded', 'recorded', 'invalid']), bindings: z.array(dshModelDispatchBindingSchema) }).optional(), model_dispatch: z.object({ status: z.enum(['not_recorded', 'verified', 'invalid']), binding: dshModelDispatchBindingSchema.nullable() }).optional(), request_pack_status: z.enum(['not_recorded', 'verified', 'invalid']).optional(), recorded_receipts: z.number().int().nonnegative(), deployment_connected: z.boolean(), latest_receipt_status: z.enum(['not_recorded', 'verified', 'invalid']), latest_receipt: z.record(z.string(), z.unknown()).nullable() }).optional(),
  paired_execution: z.object({ registered_trials: z.number().int().nonnegative(), pairs: z.number().int().nonnegative(), predictions_saved: z.number().int().nonnegative(), waiting: z.number().int().nonnegative(), incomplete: z.number().int().nonnegative(), excluded: z.number().int().nonnegative(), outcome_scoring_available: z.boolean(), labels_completed: z.number().int().nonnegative().optional(), labels_waiting: z.number().int().nonnegative().optional(), labels_retry: z.number().int().nonnegative().optional() }).optional(),
})
const prospectiveInputsSchema = z.object({
  observed_at: z.string().datetime({ offset: true }), selection_version: z.string(),
  outlooks: z.number().int().nonnegative(), eligible: z.number().int().nonnegative(),
  selected: z.number().int().nonnegative(), repeated: z.number().int().nonnegative(), excluded: z.number().int().nonnegative(),
  distinct_studies: z.number().int().nonnegative(), recorded_completed: z.number().int().nonnegative(),
  completed: z.number().int().nonnegative(), pending: z.number().int().nonnegative(), retry: z.number().int().nonnegative(), unknown: z.number().int().nonnegative(),
  directional_completed: z.number().int().nonnegative(), neutral_completed: z.number().int().nonnegative(), next_due_at: z.string().datetime({ offset: true }).nullable(),
  first_records: z.number().int().nonnegative().optional(), first_record_excluded: z.number().int().nonnegative().optional(), repeated_ineligible: z.number().int().nonnegative().optional(),
})
const trialSummarySchema = z.object({ trial_id: z.string(), registered_at: z.string(), starts_at: z.string(), ends_at: z.string(), instrument: z.string(), horizon: z.string(), baseline_version: z.string(), candidate_version: z.string() })
const pairedLabelSchema = z.object({ status: z.string(), attempt: z.number(), next_attempt_at: z.string(), error_code: z.string().nullable(), result_hash: z.string().nullable(), result: z.object({ market_return_pct: z.number(), quality_status: z.string(), benchmark: z.string(), scores: z.record(z.object({ status: z.string(), direction: z.string().nullable(), direction_correct: z.boolean().nullable(), directional_return_pct: z.number().nullable() })), paired_correctness_delta: z.number().nullable() }).nullable() }).nullable()
const pairPageSchema = z.object({ offset: z.number(), has_more: z.boolean(), items: z.array(z.object({ pair_id: z.string(), status: z.string(), admitted_at: z.string(), window_start_at: z.string(), window_end_at: z.string(), exclusion_reason: z.string().nullable(), arms: z.array(z.object({ arm: z.string(), status: z.string(), direction: z.string().nullable(), error_code: z.string().nullable() })), label: pairedLabelSchema })) })

export { harnessComparisonCatalogSchema }
export const harnessComparisonViewSchema = canonicalHarnessComparisonViewSchema.superRefine((view, context) => {
  if (view.baseline.arm !== 'baseline' || view.candidate.arm !== 'candidate' ||
      view.baseline.run_id === view.candidate.run_id ||
      view.baseline.run_id === view.source_run_id || view.candidate.run_id === view.source_run_id) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: '两份新研究的身份无法核对。' })
  }
})
export type { HarnessComparisonCatalog, HarnessComparisonView, HarnessComparisonStart }
export type HarnessOwnerSession = HarnessOwnerSessionReply

export const assets = {
  harnessComparisonCatalog: async (signal?: AbortSignal): Promise<HarnessComparisonCatalog> => harnessComparisonCatalogSchema.parse(await request('/v1/harness/comparisons/catalog', { credentials: 'include', signal })),
  harnessOwnerSession: async (pairingToken: string): Promise<HarnessOwnerSession> => {
    const input = harnessOwnerSessionPairSchema.parse({ pairing_token: pairingToken })
    return harnessOwnerSessionReplySchema.parse(await request('/v1/harness/owner-session', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input) }))
  },
  harnessComparisonStart: async (input: HarnessComparisonStart, csrfToken: string): Promise<HarnessComparisonView> => {
    const command = harnessComparisonStartSchema.parse(input)
    if (!csrfToken.trim()) throw new Error('harness_owner_session_required')
    const view = harnessComparisonViewSchema.parse(await request('/v1/harness/comparisons', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Harness-CSRF': csrfToken }, body: JSON.stringify(command) }))
    if (view.request_id !== command.request_id || view.catalog_ref !== command.catalog_ref || view.candidate_ref !== command.candidate_ref) throw new Error('harness_comparison_receipt_mismatch')
    return view
  },
  harnessComparison: async (id: string, signal?: AbortSignal): Promise<HarnessComparisonView> => {
    const view = harnessComparisonViewSchema.parse(await request(`/v1/harness/comparisons/${encodeURIComponent(id)}`, { credentials: 'include', signal }))
    if (view.assignment_id !== id) throw new Error('harness_comparison_receipt_mismatch')
    return view
  },
  harnessComparisonByRequest: async (requestId: string, signal?: AbortSignal): Promise<HarnessComparisonView | null> => {
    const raw = await request(`/v1/harness/comparisons/by-request/${encodeURIComponent(requestId)}`, { credentials: 'include', signal })
    if (raw === null) return null
    const view = harnessComparisonViewSchema.parse(raw)
    if (view.request_id !== requestId) throw new Error('harness_comparison_receipt_mismatch')
    return view
  },
  harnessComparisonCancel: async (id: string, requestId: string, csrfToken: string): Promise<HarnessComparisonView> => {
    const command = harnessComparisonCancelSchema.parse({ request_id: requestId })
    if (!csrfToken.trim()) throw new Error('harness_owner_session_required')
    const view = harnessComparisonViewSchema.parse(await request(`/v1/harness/comparisons/${encodeURIComponent(id)}/cancel`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-Harness-CSRF': csrfToken }, body: JSON.stringify(command) }))
    if (view.assignment_id !== id) throw new Error('harness_comparison_receipt_mismatch')
    return view
  },
  prospectiveTrials: async () => trialSummarySchema.array().parse(await request('/v1/evolution/prospective-trials')),
  prospectivePairs: async (trialId: string, offset: number) => pairPageSchema.parse(await request(`/v1/evolution/prospective-pairs?trial_id=${encodeURIComponent(trialId)}&offset=${offset}&limit=10`)),
  prospectivePairReceipt: async (id: string) => z.record(z.unknown()).parse(await request(`/v1/evolution/prospective-pairs/${encodeURIComponent(id)}`)),
  prospectiveInputs: async () => prospectiveInputsSchema.parse(await request('/v1/evolution/observation-inputs')),
  evolutionReadiness: async () => evolutionReadinessSchema.parse(await request('/v1/evolution/readiness')),
  usageQueueSummary: async () => usageQueueSchema.parse(await request('/v1/operations/usage-collection')),
  usageQueueJobs: async (filter: string, offset: number) => usageQueuePageSchema.parse(await request(`/v1/operations/usage-collection/jobs?${query({ filter, offset, limit: 10 })}`)),
  usageCollection: async (id: string) => usageCollectionViewSchema.array().parse(await request(`/v1/research/runs/${encodeURIComponent(id)}/usage-collection`)),
  usageHistory: async (id: string, offset = 0) => researchUsageSnapshotPageSchema.parse(await request(`/v1/research/runs/${encodeURIComponent(id)}/usage-snapshots?limit=20&offset=${offset}`)),
  captureUsage: async (id: string, requestId: string) => researchUsageSnapshotSchema.parse(await request(`/v1/research/runs/${encodeURIComponent(id)}/usage-snapshots`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: requestId }) })),
  researchUsage: async (id: string) => researchUsageViewSchema.parse(await request(`/v1/research/runs/${encodeURIComponent(id)}/usage`)),
  researchMarkets: async () => researchMarketCatalogSchema.parse(await request('/v1/product/research-scopes')),
  eventArchive: async (id: string, filters: AssetFilters = {}) => eventAssetViewSchema.parse(await request(`/v1/macro-events/${encodeURIComponent(id)}/archive?${query(filters)}`)),
  sourceHistory: async () => sourceHistoryViewSchema.parse(await request('/v1/sources/history?days=14')),
  pollCaptures: async (id: string) => rawCaptureViewSchema.array().parse(await request(`/v1/source-poll-attempts/${encodeURIComponent(id)}/captures`)),
  researchEvidenceCaptures: async (runId: string, evidenceId: string, signal?: AbortSignal) => rawCaptureViewSchema.array().parse(await request(`/v1/research/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}/captures`, { signal })),
  captureText: async (id: string) => {
    const response = await fetch(apiUrl(`/v1/raw-captures/${encodeURIComponent(id)}/body`), { signal: AbortSignal.timeout(10000) })
    if (!response.ok) throw new Error(`capture_read_failed:${response.status}`)
    return response.text()
  },
  jobCoverage: async (id: string) => {
    const input = archivedInputViewSchema.parse(await request(`/v1/data-jobs/${encodeURIComponent(id)}/coverage`))
    return coverageManifestSchema.parse(input.payload)
  },
  runtimeConfigurations: async () => z.array(z.object({ service_id: z.string(), instance_id: z.string(), process_id: z.number().int().positive(), started_at: z.string().datetime({ offset: true }), recorded_at: z.string().datetime({ offset: true }), fingerprint: z.string(), settings: z.record(z.union([z.string(), z.number(), z.boolean(), z.null()])) })).parse(await request('/v1/operations/runtime-configurations?limit=200&current_only=true')),
  outlooks: async (filters: AssetFilters) => analyticalOutlookListViewSchema.parse(await request(`/v1/analytical-outlooks?${query(filters)}`)),
  outlook: async (id: string, filters: AssetFilters = {}) => analyticalOutlookDetailViewSchema.parse(await request(`/v1/analytical-outlooks/${encodeURIComponent(id)}?${query(filters)}`)),
  cohorts: async (filters: AssetFilters) => analyticalCohortListViewSchema.parse(await request(`/v1/analytical-outlooks/cohorts?${query(filters)}`)),
  publication: async (id: string) => publicationSeparationViewSchema.parse(await request(`/v1/artifacts/${encodeURIComponent(id)}/publication`)),
  factOptions: async (asOf: string) => z.object({ as_of: z.string().datetime({ offset: true }), fields: z.array(z.string()), sources: z.array(z.string()), instruments: z.array(z.object({ instrument: z.string(), metric_family: z.string() })) }).parse(await request(`/v1/facts/options?${query({ as_of: asOf })}`)),
  facts: async (filters: AssetFilters) => factVersionListViewSchema.parse(await request(`/v1/facts/versions?${query(filters)}`)),
  fact: async (id: string) => factVersionViewSchema.parse(await request(`/v1/facts/versions/${encodeURIComponent(id)}`)),
  factInput: async (id: string, partition?: number) => archivedInputViewSchema.parse(await request(`/v1/facts/versions/${encodeURIComponent(id)}/input?${query({ partition })}`)),
  studies: async (filters: AssetFilters) => studyListViewSchema.parse(await request(`/v1/studies?${query(filters)}`)),
  study: async (id: string, filters: AssetFilters = {}) => studyTimelineViewSchema.parse(await request(`/v1/studies/${encodeURIComponent(id)}?${query(filters)}`)),
  studyReportComparison: async (studyId: string, revisionId: string) => studyReportComparisonViewSchema.parse(await request(`/v1/studies/${encodeURIComponent(studyId)}/revisions/${encodeURIComponent(revisionId)}/comparison`)),
}
