import { beforeEach, expect, it, vi } from 'vitest'
import { canonicalDecisionBody as canonical, decisionBodyHash as hash } from './decisionOriginals'
import { settingsApi } from './decisionSettings'
import actualSignedExample from '../../../../artifacts/d2-manual-paper-execution/signed-api-pure-example.json'
import actualEthExample from '../../../../artifacts/d2-manual-paper-execution/eth-wire-example.json'

const owner = 'manual-ui-owner'
const ref = (name: string) => ({ ref: name, hash: 'a'.repeat(64) })
const identity = { partition: { account_kind: 'paper' as const, account_id: 'explicit-account', instrument_id: 'okx:BTC-USDT-SWAP' as const, profile_ref: 'profile' },
  settings_revision: ref('saved-settings'), settings_state_version: 1, revision: ref('published-revision'),
  expected_plan_state_version: 1, account_revision: ref('actual-ledger-original'), account_state_version: 1, protocol_ref: ref('manual-protocol') }
const command = { ...identity, schema_version: 'perpetual-manual-intent-command.v1', request_id: 'explicit-confirmation',
  display_receipt: ref('actual-display-original'), max_entry_notional_usdt: '80', stop_price_usdt: '9000', take_profit_price_usdt: '12000',
  exit_at: '2026-10-18T12:05:00+00:00', simulation_acknowledged: true }

it('reads the exact ETH queue execution wire and rejects a resealed BTC scope substitution', async () => {
  const module = await api()
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: actualEthExample.execution.owner_id, csrf_token: 'signed-csrf', expires_at: '2099-01-01T00:00:00Z' })
  const original = await module.makeManualPaperIntent(actualEthExample.command)
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(actualEthExample.capabilities)))
    .mockResolvedValueOnce(new Response(JSON.stringify(actualEthExample.execution)))
  vi.stubGlobal('fetch', fetch)
  const capability = await module.perpetualManualPaper.capabilities()
  expect(capability.schema_version).toBe('perpetual-manual-capabilities.v2')
  const value = await module.perpetualManualPaper.execution(original, actualEthExample.execution.intent.id)
  expect(value.intent.scope_id).toBe('perp-book:okx:ETH-USDT-SWAP')
  expect(value.fill!.filled_contracts).toBe('0.07')
  expect(value.episode!.remaining_contracts).toBe('0')
  expect(value.outcome!.net_pnl_usdt).toBeNull()
  const substituted = structuredClone(actualEthExample.execution)
  substituted.intent.scope_id = 'perp-book:okx:BTC-USDT-SWAP'
  const { payload_hash: _oldHash, ...body } = substituted.intent
  substituted.intent.payload_hash = await hash(body)
  fetch.mockResolvedValueOnce(new Response(JSON.stringify(substituted)))
  await expect(module.perpetualManualPaper.execution(original, value.intent.id)).rejects.toThrow(/identity/)
  expect(fetch.mock.calls.every(([, init]) => (init as RequestInit).method === 'GET')).toBe(true)
})

async function api() {
  const files = import.meta.glob('./perpetualManualPaper.ts')
  expect(files['./perpetualManualPaper.ts'], 'signed manual-paper transport is missing').toBeDefined()
  return await files['./perpetualManualPaper.ts']() as typeof import('./perpetualManualPaper')
}
async function receipt(value = command) {
  const digest = await hash({ owner_id: owner, command: value })
  const intentBody = { schema_version: 'perpetual-manual-execution-intent.v1', id: 'perp_intent_unit', owner_id: owner, account_id: identity.partition.account_id,
    account_key: 'perppaper_unit', protocol_ref: identity.protocol_ref, scope_id: 'perp-book:okx:BTC-USDT-SWAP', event_key: 'unit', plan_id: 'plan',
    revision: identity.revision, candidate_id: 'reviewed-open-long', target_side: 'long', phase: 'entry', command_request_id: value.request_id,
    command_hash: digest, command_json: canonical(value), accepted_at: '2026-10-18T12:00:00Z', eligible_at: '2026-10-18T12:00:01Z',
    quote_deadline: '2026-10-18T12:00:31Z', sampling_generation: 0, episode_ref: null, trigger_ref: null, native_reviewed: false, financial_qualified: false,
    policy_authority: 'user_explicit_paper_simulation' }
  const body = { schema_version: 'action-command-receipt.v1', owner_id: owner, request_id: value.request_id, plan_id: 'plan', operation: 'perp_manual_intent', payload_hash: digest,
    response: { schema_version: 'perpetual-manual-command-result.v1', status: 'queued', command: value, native_reviewed: false, financial_qualified: false,
      policy_authority: 'user_explicit_paper_simulation', server_time: '2026-10-18T12:00:00+00:00', intent: { ...intentBody, payload_hash: await hash(intentBody) } }, accepted_at: '2026-10-18T12:00:00+00:00' }
  return { ...body, receipt_hash: await hash(body) }
}
beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: owner, csrf_token: 'signed-csrf', expires_at: '2026-10-20T00:00:00Z' } as ReturnType<typeof settingsApi.session>)
})

it('persists exact explicit80 intent before signed POST and recovers timeout only with original GET', async () => {
  const module = await api(), intent = await module.makeManualPaperIntent(command)
  const wire = await receipt()
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError('timeout')).mockResolvedValueOnce(new Response(JSON.stringify(wire), { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  await module.saveManualPaperIntent(intent)
  await expect(module.perpetualManualPaper.command(intent)).rejects.toThrow()
  const original = await module.readManualPaperIntent(owner)
  expect(original).toEqual(intent)
  const recovered = await module.perpetualManualPaper.receipt(original!)
  expect(recovered.response.status).toBe('queued')
  expect(fetch).toHaveBeenCalledTimes(2)
  const [postUrl, post] = fetch.mock.calls[0], [getUrl, get] = fetch.mock.calls[1]
  expect(postUrl).toContain('/paper-execution/intents')
  expect(post.method).toBe('POST')
  expect(post.credentials).toBe('include')
  expect(new Headers(post.headers).get('X-Settings-CSRF')).toBe('signed-csrf')
  expect(JSON.parse(post.body).max_entry_notional_usdt).toBe('80')
  expect(getUrl).toContain('/commands/explicit-confirmation')
  expect(get.method).toBe('GET')
})

it('rejects receipt command substitution and changed signed owner', async () => {
  const module = await api(), intent = await module.makeManualPaperIntent(command)
  await module.saveManualPaperIntent(intent)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(await receipt({ ...command, max_entry_notional_usdt: '100' })), { status: 200 })))
  await expect(module.perpetualManualPaper.receipt(intent)).rejects.toThrow(/identity/)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: 'other-owner', csrf_token: 'other', expires_at: '2026-10-20T00:00:00Z' } as ReturnType<typeof settingsApi.session>)
  await expect(module.perpetualManualPaper.command(intent)).rejects.toThrow(/owner/)
})

it('refuses a POST without durable original intent and rejects stale settings/revision scope', async () => {
  const module = await api(), intent = await module.makeManualPaperIntent(command)
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  await expect(module.perpetualManualPaper.command(intent)).rejects.toThrow(/saved/)
  expect(fetch).not.toHaveBeenCalled()
  expect(module.manualIntentMatches(intent, { owner_id: owner, partition: identity.partition, settings_revision: identity.settings_revision,
    settings_state_version: 2, horizon: '4h' }, identity.revision)).toBe(false)
})

it('parses all three exact actual signed SQLite API receipt bodies', async () => {
  const module = await api()
  for (const original of actualSignedExample.commands) {
    vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: original.owner_id, csrf_token: 'signed-csrf', expires_at: '2026-10-20T00:00:00Z' } as ReturnType<typeof settingsApi.session>)
    const intent = await module.makeManualPaperIntent(original.response.command)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(original), { status: 200 })))
    expect(await module.perpetualManualPaper.receipt(intent)).toEqual(original)
  }
})

it('reads old guides without adding a deadline and validates the new entry deadline against original T1', async () => {
  const module = await api()
  const prepared = actualSignedExample.commands.find(row => row.operation === 'perp_prepare')!
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: prepared.owner_id, csrf_token: 'signed-csrf', expires_at: '2099-01-01T00:00:00Z' })
  const original = await module.makeManualPaperIntent(prepared.response.command)
  const protocol = prepared.response.protocol!
  const oldGuide = { schema_version: 'perpetual-manual-input-guide.v1', owner_id: prepared.owner_id,
    prepare_request_id: prepared.request_id, context: prepared.response.context, target_side: 'long', tick_sz: '0.1',
    latest_exit_at: protocol.T1, server_time: protocol.T0, quote: { status: 'pending' } }
  const entry = new Date(Date.parse(protocol.T1) - 60_000).toISOString()
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(oldGuide)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...oldGuide, latest_entry_at: entry })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...oldGuide, latest_entry_at: new Date(Date.parse(protocol.T1) + 1000).toISOString() })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...oldGuide, latest_exit_at: new Date(Date.parse(protocol.T1) + 1000).toISOString() })))
  vi.stubGlobal('fetch', fetcher)
  const legacy = await module.perpetualManualPaper.guide(original, prepared as unknown as import('./perpetualManualPaper').ManualPaperReceipt)
  expect(legacy).not.toHaveProperty('latest_entry_at')
  expect(legacy.latest_exit_at).toBe(protocol.T1)
  const current = await module.perpetualManualPaper.guide(original, prepared as unknown as import('./perpetualManualPaper').ManualPaperReceipt)
  expect(current.latest_entry_at).toBe(entry)
  await expect(module.perpetualManualPaper.guide(original, prepared as unknown as import('./perpetualManualPaper').ManualPaperReceipt)).rejects.toThrow(/identity/)
  await expect(module.perpetualManualPaper.guide(original, prepared as unknown as import('./perpetualManualPaper').ManualPaperReceipt)).rejects.toThrow(/identity/)
  expect(fetcher.mock.calls.every(([, init]) => (init as RequestInit).method === 'GET')).toBe(true)
})
