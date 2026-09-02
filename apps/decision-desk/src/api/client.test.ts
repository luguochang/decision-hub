import { describe, expect, it } from 'vitest'
import { sourceHealthDisplay } from '../App'
import { apiUrl } from './client'
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
  it('keeps same-origin API paths by default and supports an isolated target', () => {
    expect(apiUrl('/v1/health', '')).toBe('/v1/health')
    expect(apiUrl('v1/health', 'http://127.0.0.1:8030/')).toBe('http://127.0.0.1:8030/v1/health')
    expect(apiUrl('https://example.test/v1/health', 'http://127.0.0.1:8030')).toBe('https://example.test/v1/health')
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
