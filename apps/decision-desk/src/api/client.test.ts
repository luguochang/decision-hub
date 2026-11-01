import { describe, expect, it, vi } from 'vitest'
import { sourceHealthDisplay } from '../App'
import { api, apiUrl } from './client'
import { operationsOverviewSchema, productHealthSchema, runInspectorSchema } from './schemas'

const inspectorFixture = {
  run: { run_id: 'run-1', event_id: 'event-1', status: 'completed', strategy_version: 'baseline.v1', runtime_version: 'langgraph.v1', snapshot_id: 'snapshot-1', artifact_id: null, created_at: '2026-08-27T01:00:00Z', updated_at: '2026-08-27T01:00:01Z', finished_at: '2026-08-27T01:00:01Z', latency_ms: 1000, cost_usd: null, error_code: null, headline: null, gate_status: null },
  timeline: [{ sequence_no: 1, event_type: 'snapshot.frozen', occurred_at: '2026-08-27T01:00:00Z', reference_type: 'snapshot', reference_id: 'snapshot-1' }],
  steps: [], calls: [], artifact: null, evaluation_count: 0, evaluations: [],
  snapshot_cutoff_at: '2026-08-27T01:00:00Z', snapshot_hash: 'a'.repeat(64),
  evidence_lineage: [{ evidence_id: 'evidence-1', source_id: 'fixture', source_type: 'manual_text', observed_at: '2026-08-27T00:59:00Z', published_at: null, received_at: '2026-08-27T01:00:00Z', cutoff_at: '2026-08-27T01:00:00Z', content_hash: 'b'.repeat(64) }],
  versions: { strategy_version: 'baseline.v1', runtime_version: 'langgraph.v1', pack_version: 'crypto_macro.v1', provider_ids: [], models: [], schema_versions: [], pricing_versions: [] },
  orchestration: { mode: 'fixed_graph', supervisor_role: null, planned_capabilities: ['policy_delta'], required_capabilities: ['policy_delta'], specialist_coverage: ['policy_delta'], missing_capabilities: [], replan_count: 0, experiment_refs: [] },
}

describe('Decision Desk API fixtures', () => {
  it('uses an explicit observation identity and rejects malformed admission receipts', async () => {
    const receipt = { event_id: 'event-receipt', run_id: 'run-receipt', status_url: '/v1/runs/run-receipt' }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(receipt))).mockResolvedValueOnce(new Response('{}'))
    try {
      expect(await api.submitObservation('文本', 'observation-submit:same')).toEqual(receipt)
      const [url, init] = fetchMock.mock.calls[0]
      expect(url).toBe('/v1/observations')
      expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('observation-submit:same')
      expect(JSON.parse(String(init?.body))).toEqual({ text: '文本', source_id: 'decision-desk', language: 'zh' })
      await expect(api.submitObservation('文本', 'observation-submit:same')).rejects.toThrow()
    } finally { fetchMock.mockRestore() }
  })
  it('requests all-family ledger history and keeps its authoritative total', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200, headers: { 'X-Total-Count': '1024' } }))
    try {
      expect(await api.ledgerRunsPage({ limit: 20, offset: 500, kind: 'baseline', status: 'failed', q: '100%', created_before: '2026-09-30T03:00:00Z' })).toEqual({ items: [], total: 1024 })
      const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://localhost')
      expect(url.pathname).toBe('/v1/decision-desk/runs')
      expect(Object.fromEntries(url.searchParams)).toEqual({ limit: '20', offset: '500', kind: 'baseline', status: 'failed', q: '100%', created_before: '2026-09-30T03:00:00Z' })
    } finally { fetchMock.mockRestore() }
  })

  it('requests data job paging from the server instead of filtering a capped list', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200, headers: { 'X-Total-Count': '523' } }))
    try {
      expect(await api.dataJobsPage({ limit: 20, offset: 500, status: 'failed', dataset: 'macro.fred.dgs2', created_before: '2026-09-30T00:00:00Z' })).toEqual({ items: [], total: 523 })
      const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://localhost')
      expect(url.pathname).toBe('/v1/data-jobs')
      expect(Object.fromEntries(url.searchParams)).toEqual({ limit: '20', offset: '500', status: 'failed', dataset: 'macro.fred.dgs2', created_before: '2026-09-30T00:00:00Z' })
    } finally { fetchMock.mockRestore() }
  })

  it('loads audited source poll attempts using an encoded source identity and offset', async () => {
    const attempt = { attempt_id: 'attempt-1', source_id: 'source/one', trigger_type: 'scheduled', status: 'empty', started_at: '2026-09-27T01:00:00Z', completed_at: '2026-09-27T01:00:01Z', cursor_before: null, cursor_after: null, received_count: 0, accepted_count: 0, duplicate_count: 0, revision_count: 0, latency_ms: 1000, error_code: null }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([attempt]), { status: 200 }))
    try {
      expect(await api.sourcePollAttempts('source/one', 21, 40)).toEqual([attempt])
      expect(String(fetchMock.mock.calls[0][0])).toBe('/v1/sources/source%2Fone/poll-attempts?limit=21&offset=40')
    } finally {
      fetchMock.mockRestore()
    }
  })

  it('requests a filtered research history page and reads the server total', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200, headers: { 'X-Total-Count': '731' } }))
    try {
      const page = await api.researchRunsPage({ limit: 50, offset: 100, q: '利率', status: 'failed', origin: 'automatic' })
      expect(page).toEqual({ items: [], total: 731 })
      const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://localhost')
      expect(url.pathname).toBe('/v1/research/runs')
      expect(Object.fromEntries(url.searchParams)).toEqual({ limit: '50', offset: '100', q: '利率', status: 'failed', origin: 'automatic' })
    } finally {
      fetchMock.mockRestore()
    }
  })

  it('reads an older macro event directly by ID without relying on the first list page', async () => {
    const event = { schema_version: 'macro-event-view.v1', macro_event_id: 'old/event', family: 'CPI', scheduled_at: '2025-01-01T12:00:00Z', source_id: 'official', source_url: null, source_authority: 'official', unit: '%', prior_value: 2.2, consensus_value: 2.1, actual_value: 2.0, surprise: -0.1, surprise_z: null, revision_flag: false, status: 'released', updated_at: '2025-01-01T12:01:00Z' }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify([event]), { status: 200, headers: { 'X-Total-Count': '201' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(event), { status: 200 }))
    try {
      const page = await api.macroEventsPage({ limit: 50, offset: 150, scope: 'history', q: 'CPI', family: 'CPI' })
      const detail = await api.macroEvent('old/event')
      expect(page.total).toBe(201)
      expect(page.items[0].macro_event_id).toBe('old/event')
      expect(detail.macro_event_id).toBe('old/event')
      const listUrl = new URL(String(fetchMock.mock.calls[0][0]), 'http://localhost')
      expect(Object.fromEntries(listUrl.searchParams)).toEqual({ limit: '50', offset: '150', scope: 'history', q: 'CPI', family: 'CPI' })
      expect(String(fetchMock.mock.calls[1][0])).toBe('/v1/macro-events/old%2Fevent')
    } finally {
      fetchMock.mockRestore()
    }
  })

  it('keeps same-origin API paths by default and supports an isolated target', () => {
    expect(apiUrl('/v1/health', '')).toBe('/v1/health')
    expect(apiUrl('v1/health', 'http://127.0.0.1:8030/')).toBe('http://127.0.0.1:8030/v1/health')
    expect(apiUrl('https://example.test/v1/health', 'http://127.0.0.1:8030')).toBe('https://example.test/v1/health')
  })

  it('submits an empty latest-advice form through the fixed snapshot entry', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          schema_version: 'research-run-queued.v1',
          event_id: 'event-latest',
          run_id: 'run-latest',
          status: 'queued',
          status_url: '/v1/runs/run-latest',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    try {
      await api.submitResearch('')
      const init = fetchMock.mock.calls[0][1]
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      expect(body).toMatchObject({
        text: '获取最新操作建议',
        source_id: 'decision-desk-latest-advice',
        source_type: 'manual',
        event_hint: 'crypto_market_snapshot',
      })
      expect(new Headers(init?.headers).get('Idempotency-Key')).toMatch(/^latest-advice:/)
    } finally {
      fetchMock.mockRestore()
    }
  })

  it('accepts nullable offline heartbeat without inventing an instance', () => {
    const operations = operationsOverviewSchema.parse({
      schema_version: 'operations-overview.v1',
      checked_at: '2026-08-29T01:00:00Z',
      services: [{ service_id: 'hub-evolution-worker', role: 'evolution_worker', instance_id: null, version: null, mode: null, status: 'offline', interval_seconds: null, started_at: null, heartbeat_at: null, last_error_code: null }],
      runtime: { runtime_id: 'fake', runtime_version: 'fake.v1', mode: 'fake', provider_configured: false, live_canary_status: 'not_run', model: null, api_mode: null },
      sources: [], capabilities: [],
      jobs: { queued: 0, running: 0, retry_wait: 0, pending_owner_review: 0, completed: 0, failed: 0, cancelled: 0 },
      recent_jobs: [],
    })
    expect(operations.services[0].status).toBe('offline')
    expect(operations.services[0].instance_id).toBeNull()
    expect(operations.runtime.provider_configured).toBe(false)
  })

  it('turns a degraded source into a compact health summary', () => {
    const health = productHealthSchema.parse({
      status: 'degraded',
      running_runs: 0,
      failed_runs: 0,
      sources: [
        {
          source_id: 'fed-press',
          status: 'healthy',
          cursor: '12',
          last_success_at: '2026-08-27T01:00:00Z',
          last_error_at: null,
          consecutive_failures: 0,
          latency_ms: 30,
          error_code: null,
          next_poll_at: '2026-08-27T01:01:00Z',
        },
        {
          source_id: 'bls-calendar',
          status: 'degraded',
          cursor: '9',
          last_success_at: null,
          last_error_at: '2026-08-27T01:00:00Z',
          consecutive_failures: 1,
          latency_ms: 1_000,
          error_code: 'source_timeout',
          next_poll_at: '2026-08-27T01:02:00Z',
        },
      ],
    })

    expect(sourceHealthDisplay(health)).toEqual({
      healthyCount: 1,
      degraded: [health.sources[1]],
      detail: 'bls-calendar: source_timeout',
    })
  })

  it('accepts the typed inspector lineage contract', () => {
    const inspector = runInspectorSchema.parse(inspectorFixture)
    expect(inspector.evidence_lineage[0].received_at).toBe('2026-08-27T01:00:00Z')
    expect(inspector.orchestration.mode).toBe('fixed_graph')
    expect(inspector.versions.pack_version).toBe('crypto_macro.v1')
  })

  it('rejects raw event payloads at the inspector boundary', () => {
    const unsafe = structuredClone(inspectorFixture)
    Object.assign(unsafe.timeline[0], { raw_payload: { prompt: 'must not reach the desk' } })
    expect(() => runInspectorSchema.parse(unsafe)).toThrow()
  })
})


it('reads the home snapshot directly and rejects malformed counts', async () => {
  const snapshot = { latest_report: null, latest_report_at: null, latest_report_headline: null, latest_run: null, active_runs: [], active_count: 0, generated_at: '2026-09-30T12:00:00Z' }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(snapshot)))
  try {
    expect(await api.researchHome()).toEqual(snapshot)
    expect(String(fetchMock.mock.calls[0][0])).toBe('/v1/research/home')
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...snapshot, active_count: -1 })))
    await expect(api.researchHome()).rejects.toThrow()
  } finally { fetchMock.mockRestore() }
})
