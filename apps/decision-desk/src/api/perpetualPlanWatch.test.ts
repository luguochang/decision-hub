import { webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import { settingsApi } from './decisionSettings'
import { decisionBodyHash as hash } from './decisionOriginals'
import { planWatchApi } from './perpetualPlanWatch'
import { continuousWatchFixture } from './perpetualPlanWatch.test-support'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function servedFixture() {
  vi.stubGlobal('crypto', webcrypto)
  const fixture = await continuousWatchFixture()
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: fixture.selection.owner_id,
    csrf_token: 'signed-session', expires_at: '2099-01-01T00:00:00Z' })
  return fixture
}

it('reads the legacy continuous receipt without claiming condition monitoring', async () => {
  const { selection, receipt, progress } = await servedFixture()
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ schema_version: 'perpetual-plan-watch-view.v2', receipt, progress }))))
  const view = await planWatchApi.continuousCurrent(selection)
  expect(view.schema_version).toBe('perpetual-plan-watch-view.v2')
  expect('condition_monitoring' in view).toBe(false)
})

it('reads condition monitoring bound to the authenticated successor progress', async () => {
  const { selection, view } = await servedFixture()
  const { payload_hash: _oldHash, ...body } = view.progress
  const nextBody = { ...body, sequence: 2, origin_run_id: 'reviewed-successor-run',
    plan_revision_ref: { ref: 'successor-plan-revision', hash: 'd'.repeat(64) } }
  const next = { ...view, progress: { ...nextBody, payload_hash: await hash(nextBody) },
    condition_monitoring: { ...view.condition_monitoring, source_run_id: 'reviewed-successor-run', plan_revision_ref: nextBody.plan_revision_ref } }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(next))))
  const result = await planWatchApi.continuousCurrent(selection)
  expect(result.schema_version === 'perpetual-plan-watch-view.v3' && result.condition_monitoring?.source_run_id).toBe('reviewed-successor-run')
})

it('rejects monitoring grafts onto another source run or plan revision', async () => {
  const { selection, view } = await servedFixture()
  for (const changed of [
    { source_run_id: 'foreign-run' },
    { plan_revision_ref: { ...view.condition_monitoring.plan_revision_ref, hash: 'f'.repeat(64) } },
  ]) {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...view,
      condition_monitoring: { ...view.condition_monitoring, ...changed } }))))
    await expect(planWatchApi.continuousCurrent(selection)).rejects.toThrow('perpetual_continuous_conditions_mismatch')
  }
})

it('rejects monitoring without the owner receipt and rejects malformed assumption references', async () => {
  const { selection, view } = await servedFixture()
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...view, receipt: null, progress: null }))))
  await expect(planWatchApi.continuousCurrent(selection)).rejects.toThrow('perpetual_continuous_original_missing')
  for (const assumption_ref of [
    { ref: 'decision_model_assumption_' + 'f'.repeat(64), hash: 'a'.repeat(64) },
    { ref: 'decision_model_assumption_bad', hash: 'bad' },
  ]) {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...view, condition_monitoring: {
      ...view.condition_monitoring, conditions: [{ ...view.condition_monitoring.conditions[0], assumption_ref }] } }))))
    await expect(planWatchApi.continuousCurrent(selection)).rejects.toThrow()
  }
})

it('uses receipt source binding before a first progress original exists', async () => {
  const { selection, view } = await servedFixture()
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ...view, progress: null }))))
  const result = await planWatchApi.continuousCurrent(selection)
  expect(result.schema_version === 'perpetual-plan-watch-view.v3' && result.condition_monitoring?.plan_revision_ref.ref).toBe(view.receipt.plan_revision_ref.ref)
})

it('preserves an aggregate registration gap when no condition declarations are available', async () => {
  const { selection, view } = await servedFixture()
  const raw = { ...view, condition_monitoring: { ...view.condition_monitoring, state: 'unregistered', conditions: [],
    reason_codes: ['prepared_condition_declarations_unavailable'] } }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(raw))))
  const result = await planWatchApi.continuousCurrent(selection)
  expect(result.schema_version === 'perpetual-plan-watch-view.v3' && result.condition_monitoring?.reason_codes).toEqual(['prepared_condition_declarations_unavailable'])
})
