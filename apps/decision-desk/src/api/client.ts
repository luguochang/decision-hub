import { artifactViewSchema, deskSummarySchema, runViewSchema, type ArtifactView, type DeskSummary, type RunView } from './schemas'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { Accept: 'application/json', ...init?.headers }, ...init })
  if (!response.ok) throw new Error(`API ${response.status}`)
  return response.json() as Promise<T>
}

export const api = {
  summary: async () => deskSummarySchema.parse(await request<unknown>('/v1/decision-desk/summary')),
  run: async (id: string) => runViewSchema.parse(await request<unknown>(`/v1/runs/${id}`)),
  runDetail: async (id: string) => {
    const value = await request<{ run: unknown; artifact: unknown | null }>(`/v1/runs/${id}/view`)
    return { run: runViewSchema.parse(value.run), artifact: value.artifact ? artifactViewSchema.parse(value.artifact) : null }
  },
}

export const fallbackSummary: DeskSummary = {
  inbox: {
    pending_count: 2,
    running_count: 1,
    latest: [
      { run_id: 'run_demo_01', event_id: 'evt_fomc_01', status: 'completed', strategy_version: 'baseline.v1', snapshot_id: 'snap_01', artifact_id: 'art_demo_01', created_at: new Date(Date.now() - 1000 * 60 * 18).toISOString(), updated_at: new Date(Date.now() - 1000 * 60 * 17).toISOString(), finished_at: new Date(Date.now() - 1000 * 60 * 17).toISOString(), latency_ms: 4120, cost_usd: 0, error_code: null, headline: '政策措辞偏鹰，BTC 短线反应仍需确认', gate_status: 'publish' },
      { run_id: 'run_demo_02', event_id: 'evt_cpi_02', status: 'degraded', strategy_version: 'baseline.v1', snapshot_id: 'snap_02', artifact_id: 'art_demo_02', created_at: new Date(Date.now() - 1000 * 60 * 55).toISOString(), updated_at: new Date(Date.now() - 1000 * 60 * 54).toISOString(), finished_at: new Date(Date.now() - 1000 * 60 * 54).toISOString(), latency_ms: 6880, cost_usd: 0, error_code: null, headline: 'CPI 输入缺少跨资产确认', gate_status: 'degraded' },
    ],
  },
  published_count_30d: 14,
  forecast_count: 42,
  evaluated_count: 31,
  health_status: 'ok',
  active_strategy: 'baseline.v1',
  active_pack: 'crypto_macro.v1',
}
