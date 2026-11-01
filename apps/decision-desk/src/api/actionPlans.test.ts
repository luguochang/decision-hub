// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { ACTION_OWNER, actionPlansApi, actionIntentKey, saveActionIntent, readActionIntent, type ActionIntent } from './actionPlans'
import { ACTION_SCOPE, currentActionFixture, actionReceiptFixture, actionDetailFixture, actionReviewFixture } from '../product/actionPlanTestFixtures'

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })
const response = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
const intent = (): ActionIntent => ({ owner_id: ACTION_OWNER, plan_id: 'plan-flat', command: { request_id: 'original-command', expected_revision_id: 'revision-flat', expected_state_version: 1, operation: 'pause', payload: {} } })

it('validates the canonical scope, per-branch identity and null probability', async () => {
  const view = currentActionFixture()
  const fetcher = vi.fn().mockImplementation(() => response(view)); vi.stubGlobal('fetch', fetcher)
  expect((await actionPlansApi.current(ACTION_SCOPE, '24h')).branches[0].probability).toBeNull()
  expect(new Headers(fetcher.mock.calls[0][1].headers).get('X-Owner-Id')).toBe(ACTION_OWNER)
  view.scope_id = 'other-scope'
  await expect(actionPlansApi.current(ACTION_SCOPE, '24h')).rejects.toThrow('action_response_identity_mismatch')
})

it('preserves the exact requested revision and refuses silent latest substitution', async () => {
  const view = actionDetailFixture()
  const fetcher = vi.fn().mockImplementation(() => response(view)); vi.stubGlobal('fetch', fetcher)
  await actionPlansApi.plan('plan-flat', 'revision-flat', 'earlier-page')
  expect(fetcher.mock.calls[0][0]).toContain('revision_id=revision-flat&cursor=earlier-page')
  await expect(actionPlansApi.plan('plan-flat', 'old-revision')).rejects.toThrow('action_response_identity_mismatch')
})

it('stores the full original command, sends owner/key and restores its receipt', async () => {
  const original = intent(); const key = actionIntentKey(ACTION_SCOPE, '24h')
  saveActionIntent(key, original); expect(readActionIntent(key)).toEqual(original)
  const fetcher = vi.fn().mockImplementation(() => response(actionReceiptFixture(original))); vi.stubGlobal('fetch', fetcher)
  await actionPlansApi.command(original); await actionPlansApi.receipt(original)
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(original.command)
  expect(new Headers(fetcher.mock.calls[0][1].headers).get('Idempotency-Key')).toBe('original-command')
  expect(fetcher.mock.calls[1][0]).toBe('/v1/actions/commands/original-command')
})

it('rejects another owner or operation receipt instead of clearing the saved request', async () => {
  const original = intent(); const receipt = actionReceiptFixture(original)
  receipt.owner_id = 'another-owner'
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => response(receipt)))
  await expect(actionPlansApi.command(original)).rejects.toThrow('action_receipt_identity_mismatch')
})

it('accepts a readable earlier published revision with a newer current head', async () => {
  const view = currentActionFixture(); view.branches[0].head.current_revision_id = 'newer-revision'
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => response(view)))
  expect((await actionPlansApi.current(ACTION_SCOPE, '24h')).branches[0].revision.revision_id).toBe('revision-flat')
})

it('uses the bounded tracking cursor and rejects another scope', async () => {
  const view = { schema_version: 'decision-tracking-view.v1', scope_id: ACTION_SCOPE, horizon: '24h',
    as_of: '2026-10-13T00:00:00Z', count: 0, items: [], next_cursor: null }
  const fetcher = vi.fn().mockImplementation(() => response(view)); vi.stubGlobal('fetch', fetcher)
  await actionPlansApi.tracking(ACTION_SCOPE, '24h', 'next-tracking')
  expect(fetcher.mock.calls[0][0]).toContain('cursor=next-tracking')
  view.scope_id = 'another-scope'
  await expect(actionPlansApi.tracking(ACTION_SCOPE, '24h')).rejects.toThrow('action_response_identity_mismatch')
})

it('binds retained and updated branches to their own real decision without using the latest for both', async () => {
  const view = currentActionFixture()
  view.decision!.id = 'decision-new'
  view.decision!.run_id = 'run-new'
  view.branches[0].decision = { ...view.decision! }
  view.branches[0].revision.decision_id = 'decision-new'
  const fetcher = vi.fn().mockImplementation(() => response(view)); vi.stubGlobal('fetch', fetcher)
  const read = await actionPlansApi.current(ACTION_SCOPE, '24h')
  expect(fetcher.mock.calls[0][0]).toContain('/v2/actions/current?')
  expect(read.branches[0].decision.run_id).toBe('run-new')
  expect(read.branches[1].decision.run_id).toBe('run-original')
  view.branches[1].decision = { ...view.decision! }
  await expect(actionPlansApi.current(ACTION_SCOPE, '24h')).rejects.toThrow('action_response_identity_mismatch')
})

it.each(['scope', 'opportunity', 'position'])('refuses mixed branch %s corruption', async field => {
  const view = currentActionFixture()
  if (field === 'scope') view.branches[1].decision.scope_id = 'another-scope'
  if (field === 'opportunity') view.branches[1].decision.opportunity_id = 'another-opportunity'
  if (field === 'position') view.branches[1] = structuredClone(view.branches[0])
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => response(view)))
  await expect(actionPlansApi.current(ACTION_SCOPE, '24h')).rejects.toThrow('action_response_identity_mismatch')
})

it('reads the canonical review result with the same opportunity and scope', async () => {
  const view = actionReviewFixture()
  const fetcher = vi.fn().mockImplementation(() => response(view)); vi.stubGlobal('fetch', fetcher)
  const read = await actionPlansApi.reviews('opportunity-old', ACTION_SCOPE, '24h')
  expect(read.review!.branches[0].applied).toBe(false)
  expect(read.review!.branches[1].applied).toBe(true)
  expect(fetcher.mock.calls[0][0]).toBe('/v1/actions/reviews?opportunity_id=opportunity-old')
})

it.each(['scope', 'opportunity', 'position', 'applied', 'mechanism', 'run', 'cutoff', 'head'] as const)('rejects review %s mismatch', async field => {
  const view = actionReviewFixture(), branch = view.review!.branches[0]
  if (field === 'scope') view.scope_id = 'other-scope'
  if (field === 'opportunity') view.opportunity_id = 'other-opportunity'
  if (field === 'position') view.review!.branches[1] = structuredClone(branch)
  if (field === 'applied') branch.applied = true
  if (field === 'mechanism') branch.mechanism = null
  if (field === 'run') branch.mechanism!.run_id = 'other-run'
  if (field === 'cutoff') branch.mechanism!.cutoff = '2026-10-13T00:01:00Z'
  if (field === 'head') branch.current_head.plan_id = 'other-plan'
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => response(view)))
  await expect(actionPlansApi.reviews('opportunity-old', ACTION_SCOPE, '24h')).rejects.toThrow('action_response_identity_mismatch')
})
