// @vitest-environment jsdom

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'
import type { EvolutionJob, OperationsOverview } from './api/types'
import { EvolutionJobQueue, OperationsPage } from './OperationsPage'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const operations: OperationsOverview = {
  schema_version: 'operations-overview.v1',
  checked_at: '2026-08-29T01:00:00Z',
  services: [
    { service_id: 'hub-api', role: 'api', instance_id: 'local:1:api', version: '0.1.0', mode: 'fake', status: 'online', interval_seconds: 10, started_at: '2026-08-29T00:59:00Z', heartbeat_at: '2026-08-29T01:00:00Z', last_error_code: null },
    { service_id: 'hub-realtime-worker', role: 'realtime_worker', instance_id: null, version: null, mode: null, status: 'offline', interval_seconds: null, started_at: null, heartbeat_at: null, last_error_code: null },
    { service_id: 'hub-evolution-worker', role: 'evolution_worker', instance_id: null, version: null, mode: null, status: 'offline', interval_seconds: null, started_at: null, heartbeat_at: null, last_error_code: null },
  ],
  runtime: { runtime_id: 'fake', runtime_version: 'fake.v1', mode: 'fake', provider_configured: false, live_canary_status: 'not_run', model: null, api_mode: null },
  sources: [], capabilities: [],
  jobs: { queued: 0, running: 0, retry_wait: 0, pending_owner_review: 1, completed: 0, failed: 0, cancelled: 0 },
  recent_jobs: [],
}

const job: EvolutionJob = {
  job_id: 'job-1', schema_version: 'evolution-job.v1', trigger_key: 'feedback:1', trigger_type: 'feedback', domain_pack_ref: 'crypto_macro.v1', status: 'pending_owner_review', stage: 'review', input_refs: ['feedback:1'], candidate_id: 'candidate-1', experiment_refs: ['replay-1', 'holdout-1', 'shadow-1'], result_refs: ['result-1'], attempt: 1, max_attempts: 3, lease_owner: null, lease_expires_at: null, next_attempt_at: null, last_error_code: null, created_at: '2026-08-29T00:00:00Z', updated_at: '2026-08-29T01:00:00Z', finished_at: null,
}

let container: HTMLDivElement | null = null

afterEach(() => {
  container?.remove()
  container = null
})

describe('Operations and Evolution observability', () => {
  it('separates fake runtime, provider configuration and absent workers', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<OperationsPage data={operations} loading={false} failed={false} />) })

    expect(container.textContent).toContain('fake')
    expect(container.textContent).toContain('Provider 已配置')
    expect(container.textContent).toContain('当前未调用远端模型')
    expect(container.textContent).toContain('无 heartbeat')
    expect(container.textContent).not.toContain('Provider Ready')
    expect(container.textContent).not.toContain('{"')
    await act(async () => { root.unmount() })
  })

  it('shows durable job trigger, current stage and owner review state', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<EvolutionJobQueue jobs={[job]} loading={false} failed={false} />) })

    expect(container.textContent).toContain('Owner 反馈')
    expect(container.textContent).toContain('待 owner 审查')
    expect(container.textContent).toContain('review')
    expect(container.textContent).toContain('candidate-1')
    await act(async () => { root.unmount() })
  })
})
