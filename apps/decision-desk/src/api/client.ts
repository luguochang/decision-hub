import { researchHomeSchema } from './researchHome'
import { z } from 'zod'
import { researchInputEvidenceSchema } from '@decision-hub/contracts-ts'
import { analystClaimSchema, artifactViewSchema, decisionGraphSchema, deskSummarySchema, documentBlockSchema, documentCorrectionSchema, documentUploadSchema, documentUploadIntentSchema, documentUploadReceiptSchema, documentUploadPolicySchema, evolutionJobSchema, evolutionOverviewSchema, forecastAuditViewSchema, macroEventViewSchema, operationsOverviewSchema, productHealthSchema, promotionDecisionResultSchema, promotionReviewSchema, researchDocumentSchema, researchDocumentVersionSchema, researchInboxViewSchema, researchMemoSchema, researchObservabilityViewSchema, researchRunCommandResultSchema, researchRunCommandReceiptSchema, researchRunComparisonViewSchema, researchRunDetailViewSchema, researchRunScopeSchema, researchRunViewSchema, researchValueEvaluationSchema, reviewScheduleViewSchema, runInspectorSchema, runViewSchema, strategyCalibrationViewSchema, type AnalystClaim, type ArtifactView, type DecisionGraph, type DocumentBlock, type DocumentCorrection, type DocumentUpload, type DocumentUploadIntent, type DocumentUploadReceipt, type EvolutionJob, type EvolutionOverview, type ForecastAuditView, type MacroEventView, type OperationsOverview, type PromotionDecisionCommand, type PromotionReview, type PromotionReviewRequest, type ResearchDocument, type ResearchDocumentVersion, type ResearchInboxView, type ResearchMemo, type ResearchObservabilityView, type ResearchRequest, type ResearchRunCommand, type ResearchRunCommandResult, type ResearchRunCommandReceipt, type ResearchRunComparisonView, type ResearchRunDetailView, type ResearchRunScope, type ResearchRunView, type ResearchValueEvaluation, type ReviewScheduleView, type RunInspector, type RunView, type StrategyCalibrationView } from './schemas'
import { dataJobViewSchema, type DataJobView } from './dataJobs'
import { sourcePollAttemptSchema, type SourcePollAttempt } from './sourceAudit'
import { researchRunQueuedSchema, type ResearchRunQueued } from './schemas'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || '').replace(/\/+$/, '')

export function apiUrl(path: string, baseUrl = API_BASE_URL): string {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const normalizedBase = baseUrl.trim().replace(/\/+$/, '')
  return `${normalizedBase}${normalizedPath}`
}

export const API_REQUEST_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 120_000
const LIBRARY_OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'

export class ApiRequestError extends Error {
  constructor(message: string, readonly kind: 'timeout' | 'network' | 'http', readonly status?: number) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

export type RequestResponseOptions = { timeoutMs?: number | null }

export async function requestResponse<T>(path: string, read: (response: Response) => Promise<T>, init?: RequestInit, onHttpError?: (status: number, detail: string) => T, options?: RequestResponseOptions): Promise<T> {
  // A caller may explicitly wait for a verified read and own cancellation via
  // its AbortSignal. Ordinary requests retain their existing finite timeout.
  const timeoutMs = options?.timeoutMs === null ? null : options?.timeoutMs ?? (init?.body instanceof FormData ? UPLOAD_TIMEOUT_MS : API_REQUEST_TIMEOUT_MS)
  if (timeoutMs !== null && (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0)) throw new RangeError('请求等待时间必须是正整数。')
  const controller = new AbortController()
  const forwardAbort = () => controller.abort(init?.signal?.reason)
  if (init?.signal?.aborted) forwardAbort()
  else init?.signal?.addEventListener('abort', forwardAbort, { once: true })
  let timedOut = false
  const timeout = timeoutMs === null ? undefined : setTimeout(() => { timedOut = true; controller.abort() }, timeoutMs)
  try {
    const headers = new Headers(init?.headers)
    if (!headers.has('Accept')) headers.set('Accept', 'application/json')
    const response = await fetch(apiUrl(path), { ...init, headers, signal: controller.signal })
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: unknown } | null
      const detail = typeof body?.detail === 'string' ? body.detail : `API ${response.status}`
      if (onHttpError) return onHttpError(response.status, detail)
      throw new ApiRequestError(detail, 'http', response.status)
    }
    return await read(response)
  } catch (error) {
    if (timedOut) throw new ApiRequestError('请求超时，尚未确认结果。请刷新核对当前状态后重试。', 'timeout')
    if (error instanceof TypeError && !controller.signal.aborted) throw new ApiRequestError('网络连接中断，尚未确认结果。请检查连接并刷新核对。', 'network')
    throw error
  } finally {
    if (timeout !== undefined) clearTimeout(timeout)
    init?.signal?.removeEventListener('abort', forwardAbort)
  }
}

export async function request<T>(path: string, init?: RequestInit, expectedStatus?: number): Promise<T> {
  return requestResponse(path, response => {
    if (expectedStatus !== undefined && response.status !== expectedStatus) throw new Error('回执响应状态无法核对，尚未确认原请求。')
    return response.json() as Promise<T>
  }, init)
}

function totalCount(response: Response): number | null {
  const raw = response.headers.get('X-Total-Count')
  if (raw === null) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value >= 0 ? value : null
}

export type PageResult<T> = { items: T[]; total: number | null }

const receiptStatusPathMatches = (receipt: { run_id: string; status_url: string }) => receipt.status_url === `/v1/runs/${receipt.run_id}`
const receiptStatusPathError = { message: '提交回执与返回的运行不匹配，尚未确认结果。', path: ['status_url'] }
const receiptIdentitiesNonblank = (receipt: { run_id: string; event_id: string }) => receipt.run_id.trim().length > 0 && receipt.event_id.trim().length > 0
const receiptIdentityError = { message: '提交回执缺少有效运行或事件身份，尚未确认结果。' }
const observationReceiptSchema = z.object({ event_id: z.string().min(1), run_id: z.string().min(1), status_url: z.string().min(1) }).refine(receiptStatusPathMatches, receiptStatusPathError).refine(receiptIdentitiesNonblank, receiptIdentityError)
const queuedResearchReceiptSchema = researchRunQueuedSchema.refine(receiptStatusPathMatches, receiptStatusPathError).refine(receiptIdentitiesNonblank, receiptIdentityError)
export type ObservationReceipt = z.infer<typeof observationReceiptSchema>

export type ResearchRunsPageQuery = { limit: number; offset: number; q?: string; status?: string; origin?: string }
export type MacroEventsPageQuery = { limit: number; offset: number; scope: 'all' | 'upcoming' | 'history'; q?: string; family?: string }

const originalInputReceiptSchema = z.object({
  schema_version: z.literal('research-original-input-receipt.v1'), owner_id: z.string().min(1), run_id: z.string().min(1),
  origin: z.literal('authorized_original_input'), public_evidence_accepted: z.literal(false), input: researchInputEvidenceSchema,
  excerpt_sha256: z.string().regex(/^[a-f0-9]{64}$/), request_hash: z.string().regex(/^[a-f0-9]{64}$/),
  request_receipt: z.object({ output_id: z.string().min(1), role: z.literal('research_task_request'), schema_version: z.string().min(1), payload_hash: z.string().regex(/^[a-f0-9]{64}$/), recorded_at: z.string().datetime({ offset: true }) }).strict(),
}).strict()
export type OriginalInputReceipt = z.infer<typeof originalInputReceiptSchema>

export const api = {
  researchOriginalInput: async (runId: string, evidenceId: string, owner: string, signal?: AbortSignal): Promise<OriginalInputReceipt> => {
    const receipt = originalInputReceiptSchema.parse(await request<unknown>(`/v1/research/runs/${encodeURIComponent(runId)}/input-receipts/${encodeURIComponent(evidenceId)}`, { headers: { 'X-Owner-Id': owner }, signal }))
    if (receipt.run_id !== runId || receipt.owner_id !== owner || receipt.input.evidence_id !== evidenceId) throw new Error('原输入回执身份无法核对，请重新读取。')
    return receipt
  },
  summary: async () => deskSummarySchema.parse(await request<unknown>('/v1/decision-desk/summary')),
  ledgerRunsPage: async (filters: { limit: number; offset: number; created_before: string; status?: string; kind?: string; q?: string }): Promise<PageResult<RunView>> => {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)) })
    return requestResponse(`/v1/decision-desk/runs?${params}`, async response => ({ items: runViewSchema.array().parse(await response.json()), total: totalCount(response) }))
  },
  health: async () => productHealthSchema.parse(await request<unknown>('/v1/health')),
  run: async (id: string) => runViewSchema.parse(await request<unknown>(`/v1/runs/${id}`)),
  runDetail: async (id: string) => {
    const value = await request<{ run: unknown; artifact: unknown | null }>(`/v1/runs/${id}/view`)
    return { run: runViewSchema.parse(value.run), artifact: value.artifact ? artifactViewSchema.parse(value.artifact) : null }
  },
  runInspector: async (id: string): Promise<RunInspector> => runInspectorSchema.parse(await request<unknown>(`/v1/runs/${id}/inspector`)),
  researchMemos: async (): Promise<ResearchMemo[]> => researchMemoSchema.array().parse(await request<unknown>('/v1/workbench/memos')),
  evolutionOverview: async (): Promise<EvolutionOverview> => evolutionOverviewSchema.parse(await request<unknown>('/v1/evolution/overview')),
  evolutionJobs: async (): Promise<EvolutionJob[]> => evolutionJobSchema.array().parse(await request<unknown>('/v1/evolution/jobs')),
  operations: async (): Promise<OperationsOverview> => operationsOverviewSchema.parse(await request<unknown>('/v1/operations')),
  sourcePollAttempts: async (sourceId: string, limit = 21, offset = 0): Promise<SourcePollAttempt[]> => sourcePollAttemptSchema.array().parse(await request<unknown>(`/v1/sources/${encodeURIComponent(sourceId)}/poll-attempts?limit=${limit}&offset=${offset}`)),
  dataJobs: async (): Promise<DataJobView[]> => dataJobViewSchema.array().parse(await request<unknown>('/v1/data-jobs?limit=500')),
  dataJobsPage: async (filters: { limit: number; offset: number; status?: string; dataset?: string; created_before: string }): Promise<PageResult<DataJobView>> => {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)) })
    return requestResponse(`/v1/data-jobs?${params}`, async response => ({ items: dataJobViewSchema.array().parse(await response.json()), total: totalCount(response) }))
  },
  researchHome: async () => researchHomeSchema.parse(await request<unknown>('/v1/research/home')),
  researchRuns: async (): Promise<ResearchRunView[]> => researchRunViewSchema.array().parse(await request<unknown>('/v1/research/runs')),
  researchRecentRuns: async (): Promise<ResearchRunView[]> => researchRunViewSchema.array().parse(await request<unknown>('/v1/research/recent-runs?limit=8')),
  researchEvidenceOriginal: async (runId: string, evidenceId: string, signal?: AbortSignal): Promise<string> => requestResponse(`/v1/research/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}/original`, response => response.text(), { signal, headers: { Accept: 'text/plain' } }),
  researchRunsPage: async ({ limit, offset, q, status, origin }: ResearchRunsPageQuery): Promise<PageResult<ResearchRunView>> => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (q) params.set('q', q)
    if (status) params.set('status', status)
    if (origin) params.set('origin', origin)
    return requestResponse(`/v1/research/runs?${params}`, async response => ({ items: researchRunViewSchema.array().parse(await response.json()), total: totalCount(response) }))
  },
  researchInbox: async (): Promise<ResearchInboxView> => researchInboxViewSchema.parse(await request<unknown>('/v1/research/inbox?limit=100')),
  macroEvents: async (scope: 'all' | 'upcoming' | 'history' = 'all', offset = 0): Promise<MacroEventView[]> => macroEventViewSchema.array().parse(await request<unknown>(`/v1/macro-events?limit=50&offset=${offset}&scope=${scope}`)),
  macroEventsPage: async ({ limit, offset, scope, q, family }: MacroEventsPageQuery): Promise<PageResult<MacroEventView>> => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset), scope })
    if (q) params.set('q', q)
    if (family) params.set('family', family)
    return requestResponse(`/v1/macro-events?${params}`, async response => ({ items: macroEventViewSchema.array().parse(await response.json()), total: totalCount(response) }))
  },
  macroEventFamilies: async (): Promise<string[]> => z.string().array().parse(await request<unknown>('/v1/macro-events/families')),
  macroEvent: async (id: string): Promise<MacroEventView> => macroEventViewSchema.parse(await request<unknown>(`/v1/macro-events/${encodeURIComponent(id)}`)),
  decisionGraph: async (runId: string): Promise<DecisionGraph> => decisionGraphSchema.parse(await request<unknown>(`/v1/research/runs/${runId}/decision-graph`)),
  strategyCalibrations: async (): Promise<StrategyCalibrationView[]> => strategyCalibrationViewSchema.array().parse(await request<unknown>('/v1/strategy-calibrations?limit=50')),
  researchObservability: async (id: string): Promise<ResearchObservabilityView> => researchObservabilityViewSchema.parse(await request<unknown>('/v1/research/runs/' + encodeURIComponent(id) + '/observability')),
  researchValueEvaluation: async (id: string): Promise<ResearchValueEvaluation | null> => {
    const value = await request<unknown>('/v1/research/runs/' + encodeURIComponent(id) + '/value-evaluation')
    return value === null ? null : researchValueEvaluationSchema.parse(value)
  },
  researchForecastAudit: async (id: string): Promise<ForecastAuditView[]> => forecastAuditViewSchema.array().parse(await request<unknown>('/v1/research/runs/' + encodeURIComponent(id) + '/forecast-audit')),
  researchReviewPlans: async (id: string): Promise<ReviewScheduleView[]> => reviewScheduleViewSchema.array().parse(await request<unknown>('/v1/research/runs/' + encodeURIComponent(id) + '/review-plans')),
  researchRun: async (id: string): Promise<ResearchRunDetailView> => researchRunDetailViewSchema.parse(await request<unknown>(`/v1/research/runs/${encodeURIComponent(id)}`)),
  researchRunScope: async (id: string): Promise<ResearchRunScope | null> => {
    const value = await request<unknown>(`/v1/research-library/context/${encodeURIComponent(id)}`)
    return value === null ? null : researchRunScopeSchema.parse(value)
  },
  researchCommand: async (id: string, payload: ResearchRunCommand, owner: string): Promise<ResearchRunCommandResult> => researchRunCommandResultSchema.parse(await request<unknown>(`/v1/research/runs/${encodeURIComponent(id)}/commands`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': payload.request_id, 'X-Owner-Id': owner }, body: JSON.stringify(payload) })),
  researchLinkedCommandReceipt: async (runId: string, owner: string, signal?: AbortSignal): Promise<ResearchRunCommandReceipt | null> => {
    const value = await request<unknown>(`/v1/research/runs/${encodeURIComponent(runId)}/linked-command-receipt`, { signal, headers: { 'X-Owner-Id': owner } })
    return value === null ? null : researchRunCommandReceiptSchema.parse(value)
  },
  researchCommandReceipt: async (runId: string, requestId: string, owner: string): Promise<ResearchRunCommandReceipt | null> => requestResponse<ResearchRunCommandReceipt | null>(`/v1/research/runs/${encodeURIComponent(runId)}/commands/${encodeURIComponent(requestId)}`, async response => researchRunCommandReceiptSchema.parse(await response.json()), { headers: { 'X-Owner-Id': owner } }, (status, detail) => {
    if (status === 404 && detail === 'research_command_receipt_not_found') return null
    throw new Error(detail)
  }),
  submitObservation: async (text: string, requestId: string = crypto.randomUUID()): Promise<ObservationReceipt> => observationReceiptSchema.parse(await request('/v1/observations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId }, body: JSON.stringify({ text, source_id: 'decision-desk', language: 'zh' }) })),
  submitResearch: async (text: string, requestId?: string, owner?: string): Promise<ResearchRunQueued> => {
    const normalized = text.trim() || '获取最新操作建议'
    const window = Math.floor(Date.now() / (15 * 60_000))
    let hash = 0
    for (const char of normalized) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0
    const key = requestId ?? `latest-advice:${window}:${Math.abs(hash).toString(36)}`
    return queuedResearchReceiptSchema.parse(await request<unknown>('/v1/research/observations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key, ...(owner === undefined ? {} : { 'X-Owner-Id': owner }) }, body: JSON.stringify({ text: normalized, source_id: 'decision-desk-latest-advice', source_type: 'manual', language: 'zh', event_hint: 'crypto_market_snapshot' }) }))
  },
  researchDocuments: async (): Promise<ResearchDocument[]> => researchDocumentSchema.array().parse(await request<unknown>('/v1/research-library/documents')),
  researchUploadPolicy: async () => documentUploadPolicySchema.parse(await request<unknown>('/v1/research-library/upload-policy')),
  researchUploadReceipt: async (requestId: string): Promise<DocumentUploadReceipt | null> => requestResponse<DocumentUploadReceipt | null>(`/v1/research-library/uploads/${encodeURIComponent(requestId)}`, async response => documentUploadReceiptSchema.parse(await response.json()), { headers: { 'X-Owner-Id': LIBRARY_OWNER_ID } }, (status, detail) => {
    if (status === 404 && detail === 'document_upload_receipt_not_found') return null
    throw new Error(detail)
  }),
  uploadResearchDocumentReceipt: async (file: File, intent: DocumentUploadIntent, requestId: string): Promise<DocumentUploadReceipt> => {
    const form = new FormData()
    form.append('file', file)
    form.append('intent', JSON.stringify(documentUploadIntentSchema.parse(intent)))
    return documentUploadReceiptSchema.parse(await request<unknown>('/v1/research-library/uploads', { method: 'POST', headers: { 'X-Owner-Id': LIBRARY_OWNER_ID, 'Idempotency-Key': requestId }, body: form }))
  },
  uploadResearchDocument: async (file: File, payload: DocumentUpload): Promise<ResearchDocumentVersion> => {
    const form = new FormData()
    form.append('file', file)
    form.append('metadata', JSON.stringify(payload))
    return researchDocumentVersionSchema.parse(await request<unknown>('/v1/research-library/documents', { method: 'POST', headers: { 'X-Owner-Id': LIBRARY_OWNER_ID }, body: form }))
  },
  importResearchAcquisition: async (receiptId: string, payload: DocumentUpload): Promise<ResearchDocumentVersion> => {
    const form = new FormData()
    form.append('receipt_id', receiptId)
    form.append('metadata', JSON.stringify(payload))
    return researchDocumentVersionSchema.parse(await request<unknown>('/v1/research-library/web-import', { method: 'POST', headers: { 'X-Owner-Id': LIBRARY_OWNER_ID }, body: form }))
  },
  researchDocumentVersion: async (id: string): Promise<ResearchDocumentVersion> => researchDocumentVersionSchema.parse(await request<unknown>(`/v1/research-library/versions/${encodeURIComponent(id)}`)),
  researchDocumentOriginalUrl: (id: string): string => apiUrl(`/v1/research-library/versions/${encodeURIComponent(id)}/original`),
  researchDocumentBlocks: async (id: string): Promise<DocumentBlock[]> => documentBlockSchema.array().parse(await request<unknown>(`/v1/research-library/versions/${encodeURIComponent(id)}/blocks`)),
  researchDocumentCorrection: async (id: string): Promise<DocumentCorrection | null> => {
    const value = await request<unknown>(`/v1/research-library/versions/${encodeURIComponent(id)}/correction`)
    return value === null ? null : documentCorrectionSchema.parse(value)
  },
  requestResearchCorrection: async (id: string): Promise<ResearchDocumentVersion> => researchDocumentVersionSchema.parse(await request<unknown>(`/v1/research-library/versions/${encodeURIComponent(id)}/correction`, { method: 'POST', headers: { 'X-Owner-Id': LIBRARY_OWNER_ID } })),
  acceptResearchCorrection: async (id: string): Promise<ResearchDocumentVersion> => researchDocumentVersionSchema.parse(await request<unknown>(`/v1/research-library/versions/${encodeURIComponent(id)}/correction/accept`, { method: 'POST', headers: { 'X-Owner-Id': LIBRARY_OWNER_ID } })),
  researchClaims: async (id: string): Promise<AnalystClaim[]> => analystClaimSchema.array().parse(await request<unknown>(`/v1/research-library/versions/${encodeURIComponent(id)}/claims`)),
  reviewResearchClaim: async (id: string, decision: 'accept' | 'reject' | 'supersede', reason: string, requestId: string = crypto.randomUUID()): Promise<AnalystClaim> => analystClaimSchema.parse(await request<unknown>(`/v1/research-library/claims/${encodeURIComponent(id)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Owner-Id': LIBRARY_OWNER_ID, 'Idempotency-Key': requestId }, body: JSON.stringify({ schema_version: 'claim-review-command.v1', request_id: requestId, decision, reason }) })),
  submitResearchWithLibrary: async (payload: ResearchRequest, requestId: string = crypto.randomUUID()): Promise<ResearchRunQueued> => queuedResearchReceiptSchema.parse(await request<unknown>('/v1/research-library/research', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId, 'X-Owner-Id': LIBRARY_OWNER_ID }, body: JSON.stringify(payload) })),
  researchRunComparison: async (id: string): Promise<ResearchRunComparisonView> => researchRunComparisonViewSchema.parse(await request<unknown>(`/v1/research/runs/${encodeURIComponent(id)}/comparison`)),
  promotionReview: async (domainPackRef: string, payload: PromotionReviewRequest): Promise<PromotionReview> => promotionReviewSchema.parse(await request<unknown>(`/v1/evolution/${encodeURIComponent(domainPackRef)}/promotion-reviews`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  promotionDecisionReceipt: async (domainPackRef: string, requestId: string) => promotionDecisionResultSchema.parse(await request<unknown>(`/v1/evolution/${encodeURIComponent(domainPackRef)}/decisions/${encodeURIComponent(requestId)}`, { headers: { 'X-Owner-Id': LIBRARY_OWNER_ID } })),
  promotionDecision: async ({ domain_pack_ref, ...payload }: PromotionDecisionCommand & { domain_pack_ref: string }) => promotionDecisionResultSchema.parse(await request<unknown>(`/v1/evolution/${encodeURIComponent(domain_pack_ref)}/decisions`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Owner-Id': payload.owner }, body: JSON.stringify(payload) })),
}
