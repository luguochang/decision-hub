import { artifactViewSchema, deskSummarySchema, evolutionJobSchema, evolutionOverviewSchema, operationsOverviewSchema, productHealthSchema, promotionDecisionResultSchema, promotionReviewSchema, researchMemoSchema, researchRunCommandResultSchema, researchRunDetailViewSchema, researchRunViewSchema, runInspectorSchema, runViewSchema, type ArtifactView, type EvolutionJob, type EvolutionOverview, type OperationsOverview, type PromotionDecisionCommand, type PromotionReview, type PromotionReviewRequest, type ResearchMemo, type ResearchRunCommand, type ResearchRunCommandResult, type ResearchRunDetailView, type ResearchRunView, type RunInspector, type RunView } from './schemas'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL?.trim() || '').replace(/\/+$/, '')

export function apiUrl(path: string, baseUrl = API_BASE_URL): string {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const normalizedBase = baseUrl.trim().replace(/\/+$/, '')
  return `${normalizedBase}${normalizedPath}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { headers: { Accept: 'application/json', ...init?.headers }, ...init })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : `API ${response.status}`
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  summary: async () => deskSummarySchema.parse(await request<unknown>('/v1/decision-desk/summary')),
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
  researchRuns: async (): Promise<ResearchRunView[]> => researchRunViewSchema.array().parse(await request<unknown>('/v1/research/runs')),
  researchRun: async (id: string): Promise<ResearchRunDetailView> => researchRunDetailViewSchema.parse(await request<unknown>(`/v1/research/runs/${encodeURIComponent(id)}`)),
  researchCommand: async (id: string, payload: ResearchRunCommand, owner: string): Promise<ResearchRunCommandResult> => researchRunCommandResultSchema.parse(await request<unknown>(`/v1/research/runs/${encodeURIComponent(id)}/commands`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': payload.request_id, 'X-Owner-Id': owner }, body: JSON.stringify(payload) })),
  submitObservation: async (text: string): Promise<{ event_id: string; run_id: string; status_url: string }> => request('/v1/observations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ text, source_id: 'decision-desk', language: 'zh' }) }),
  submitResearch: async (text: string): Promise<{ event_id: string; run_id: string; status: string; status_url: string }> => request('/v1/research/observations', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ text, source_id: 'decision-desk', source_type: 'manual', language: 'zh' }) }),
  promotionReview: async (domainPackRef: string, payload: PromotionReviewRequest): Promise<PromotionReview> => promotionReviewSchema.parse(await request<unknown>(`/v1/evolution/${encodeURIComponent(domainPackRef)}/promotion-reviews`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })),
  promotionDecision: async ({ domain_pack_ref, ...payload }: PromotionDecisionCommand & { domain_pack_ref: string }) => promotionDecisionResultSchema.parse(await request<unknown>(`/v1/evolution/${encodeURIComponent(domain_pack_ref)}/decisions`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Owner-Id': payload.owner }, body: JSON.stringify(payload) })),
}
