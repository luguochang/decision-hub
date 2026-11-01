// @vitest-environment jsdom
import { act } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiRequestError } from '../api/client'
import { perpetualAdviceApi } from '../api/perpetualAdvice'
import { settingsApi, settingsIntentKey, type SettingsOptions, type SettingsIntent } from '../api/decisionSettings'
import { DecisionSettingsPanel } from './DecisionSettingsPanel'
import { perpetualHorizonPreferenceKey, readPerpetualHorizonPreference, savePerpetualHorizonPreference } from './perpetualHorizonPreference'
import fixture from '../api/decisionSettingsReceiptFixture.json'
import { decisionSettingsCurrentViewV1Schema, instrumentContractV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const hash = 'a'.repeat(64)
const contract = instrumentContractV1Schema.parse({ schema_version: 'instrument-contract.v1', venue: 'okx', inst_id: 'BTC-USDT-SWAP', instrument_kind: 'perpetual', linear_or_inverse: 'linear', base: 'BTC', quote: 'USDT', settle: 'USDT', ct_val: '0.01', ct_mult: '1', ct_val_ccy: 'BTC', lot_sz: '0.01', min_sz: '0.01', tick_sz: '0.1', price_basis_refs: { trade: null, mark: null, index: null }, rule_source_ref: 'fixture-metadata', rule_source_hash: hash, funding_schedule_ref: null, margin_tier_ref: null, effective_at: null, effective_at_source: 'unknown', known_at: '2026-10-19T00:00:00Z', state: 'live' })
const options: SettingsOptions = { schema_version: 'decision-settings-options.v1', owner_id: 'owner', declared_account_id: 'declared_pc', availability: 'available', reason: null, paper_available: false, paper_reason: 'decision_settings_paper_account_unavailable', paper_accounts: [], instruments: [{ instrument_id: 'okx:BTC-USDT-SWAP', instrument: { ref: 'real-contract', hash }, execution_profile: { ref: 'server-profile', hash }, contract }] }
const savedCurrent = decisionSettingsCurrentViewV1Schema.parse({ schema_version: 'decision-settings-current-view.v1', state_version: 1, reevaluation_intent_state: 'pending', scan_generation: 1, scan_after_plan_id: null,
  revision: { schema_version: 'decision-settings-revision.v1', revision_id: fixture.receipt.revision.ref, settings_key: fixture.receipt.settings_key, owner_id: 'owner', partition: { ...fixture.intent.command.partition, account_id: 'declared_pc', profile_ref: 'server-profile' }, revision_no: 1, parent: null, command_hash: fixture.receipt.command_hash, operation: 'upsert', values: { ...fixture.intent.command.values, execution_profile: { ref: 'server-profile', hash }, account_snapshot: { ...fixture.intent.command.values.account_snapshot, owner_id: 'owner', account_id: 'declared_pc' } }, created_at: fixture.receipt.accepted_at, payload_hash: fixture.receipt.revision.hash } })
const settle = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })

async function render(current: typeof savedCurrent | null = null, owner = 'owner') {
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: owner, csrf_token: 'private-csrf', expires_at: '2099-01-01T00:00:00Z' })
  vi.spyOn(settingsApi, 'options').mockResolvedValue(options)
  vi.spyOn(settingsApi, 'current').mockResolvedValue(current)
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  await act(async () => root.render(<QueryClientProvider client={client}><DecisionSettingsPanel /></QueryClientProvider>)); await settle(); await settle()
  return { host, click: async (label: string) => { const button = [...host.querySelectorAll('button')].find(item => item.textContent === label); expect(button).toBeDefined(); await act(async () => button!.click()); await settle() }, close: async () => { await act(async () => root.unmount()); client.clear(); host.remove() } }
}

it('stores a strict horizon preference by signed owner and exact partition only', () => {
  const btc = savedCurrent.revision.partition
  const eth = { ...btc, instrument_id: 'okx:ETH-USDT-SWAP' as const }
  savePerpetualHorizonPreference('owner', btc, '24h')
  expect(readPerpetualHorizonPreference('owner', btc)).toBe('24h')
  expect(readPerpetualHorizonPreference('owner', eth)).toBe('4h')
  const original = sessionStorage.getItem(perpetualHorizonPreferenceKey('owner', btc))!
  sessionStorage.setItem(perpetualHorizonPreferenceKey('other-owner', btc), original)
  expect(readPerpetualHorizonPreference('other-owner', btc)).toBe('4h')
  sessionStorage.setItem(perpetualHorizonPreferenceKey('owner', eth), '{"horizon":"30m"}')
  expect(readPerpetualHorizonPreference('owner', eth)).toBe('4h')
  expect(readPerpetualHorizonPreference('owner', btc)).toBe('24h')
})

it('restores BTC 24h before the first Advice read after remount without a mutation', async () => {
  const advice = vi.spyOn(perpetualAdviceApi, 'current').mockRejectedValue(new Error('read stopped'))
  const mutate = vi.spyOn(settingsApi, 'command')
  const first = await render(savedCurrent)
  try {
    const selector = first.host.querySelector('select[aria-label="永续预测期限"]') as HTMLSelectElement
    expect(selector.value).toBe('4h')
    await act(async () => { selector.value = '24h'; selector.dispatchEvent(new Event('change', { bubbles: true })) })
    await settle()
    expect(selector.value).toBe('24h')
  } finally { await first.close() }

  advice.mockClear()
  const reopened = await render(savedCurrent)
  try {
    const selector = reopened.host.querySelector('select[aria-label="永续预测期限"]') as HTMLSelectElement
    expect(selector.value).toBe('24h')
    expect(advice.mock.calls.length).toBeGreaterThan(0)
    expect(advice.mock.calls.every(([selection]) => selection.horizon === '24h')).toBe(true)
    expect(mutate).not.toHaveBeenCalled()
  } finally { await reopened.close() }
})

it('submits explicit unknown inputs and keeps a conflicted original intent without claiming saved', async () => {
  const post = vi.spyOn(settingsApi, 'command').mockRejectedValue(new ApiRequestError('decision_settings_state_conflict', 'http', 409))
  vi.spyOn(settingsApi, 'receipt').mockRejectedValue(new ApiRequestError('decision_settings_command_missing', 'http', 404))
  const page = await render()
  try {
    expect(page.host.textContent).toContain('尚未保存')
    expect(page.host.querySelector('select[aria-label="当前仓位"] option[value="unknown"]')?.textContent).toBe('未知（仅假设空仓比较）')
    expect(page.host.textContent).toContain('模拟账户暂不可用')
    await page.click('保存设置')
    expect(post).toHaveBeenCalledTimes(1)
    const intent = post.mock.calls[0][0]
    expect(intent.command.values?.account_snapshot.equity).toBeNull()
    expect(intent.command.values?.position_context.state).toBe('unknown')
    expect(intent.command.values?.position_context.signed_contracts).toBeNull()
    expect(page.host.textContent).toContain('版本已变化')
    expect(page.host.textContent).not.toContain('设置已保存')
    expect(sessionStorage.getItem(settingsIntentKey('owner'))).toContain(intent.command.request_id)
    await page.click('放弃未保存的原操作')
    expect(page.host.textContent).not.toContain('原操作结果待确认')
    expect(sessionStorage.getItem(settingsIntentKey('owner'))).toBeNull()
    expect((page.host.querySelector('button[data-save]') as HTMLButtonElement).disabled).toBe(false)
  } finally { await page.close() }
})

it('reopens a server saved tombstone and displays pending comparison only', async () => {
  const page = await render()
  try {
    vi.mocked(settingsApi.current).mockResolvedValue({ schema_version: 'decision-settings-current-view.v1', state_version: 2, reevaluation_intent_state: 'pending', scan_generation: 2, scan_after_plan_id: null, revision: { schema_version: 'decision-settings-revision.v1', revision_id: 'settingsrev_' + hash, settings_key: 'dsettings_' + hash, owner_id: 'owner', partition: { account_kind: 'user_declared', account_id: 'declared_pc', instrument_id: 'okx:BTC-USDT-SWAP', profile_ref: 'server-profile' }, revision_no: 2, parent: { ref: 'settingsrev_' + hash, hash }, command_hash: hash, operation: 'remove', values: null, created_at: '2026-10-19T00:00:00Z', payload_hash: hash } })
    await page.click('重新读取设置')
    expect(page.host.textContent).toContain('活跃设置已删除')
    expect(page.host.textContent).toContain('比较待更新')
    expect(page.host.textContent).toContain('历史原件保留')
  } finally { await page.close() }
})

it('preserves the original request through page reopen and only reads its receipt', async () => {
  // Obtain a real canonical command through the same public form first.
  const post = vi.spyOn(settingsApi, 'command').mockRejectedValue(new Error('network'))
  vi.spyOn(settingsApi, 'receipt').mockRejectedValue(new ApiRequestError('decision_settings_command_missing', 'http', 404))
  const first = await render(); await first.click('保存设置')
  const original = JSON.parse(sessionStorage.getItem(settingsIntentKey('owner'))!) as SettingsIntent
  await first.close(); post.mockClear()
  const reopened = await render()
  try {
    expect(settingsApi.receipt).toHaveBeenLastCalledWith(original)
    expect(post).not.toHaveBeenCalled()
    expect(reopened.host.textContent).toContain('原操作结果待确认')
    expect((reopened.host.querySelector('button[data-save]') as HTMLButtonElement).disabled).toBe(true)
  } finally { await reopened.close() }
})

it('keeps the exact saved partition readable and removable when latest metadata becomes unavailable', async () => {
  const page = await render()
  const command = fixture.intent.command
  const view = decisionSettingsCurrentViewV1Schema.parse({ schema_version: 'decision-settings-current-view.v1', state_version: 1, reevaluation_intent_state: 'pending', scan_generation: 1, scan_after_plan_id: null,
    revision: { schema_version: 'decision-settings-revision.v1', revision_id: fixture.receipt.revision.ref, settings_key: fixture.receipt.settings_key, owner_id: 'owner', partition: { ...command.partition, account_id: 'declared_pc', profile_ref: 'server-profile' }, revision_no: 1, parent: null, command_hash: fixture.receipt.command_hash, operation: 'upsert', values: { ...command.values, execution_profile: { ref: 'server-profile', hash }, account_snapshot: { ...command.values.account_snapshot, owner_id: 'owner', account_id: 'declared_pc' } }, created_at: fixture.receipt.accepted_at, payload_hash: fixture.receipt.revision.hash } })
  try {
    vi.mocked(settingsApi.current).mockResolvedValue(view)
    await page.click('重新读取设置')
    vi.mocked(settingsApi.options).mockResolvedValue({ ...options, availability: 'unavailable', reason: 'decision_settings_instrument_original_unavailable', instruments: [] })
    await act(async () => window.dispatchEvent(new Event('decision-hub-paper-accounts-changed'))); await settle(); await settle()
    expect(page.host.textContent).toContain(command.values.instrument.ref)
    expect((page.host.querySelector('button[data-save]') as HTMLButtonElement).disabled).toBe(true)
    const remove = [...page.host.querySelectorAll('button')].find(button => button.textContent === '删除活跃设置')!
    expect(remove.disabled).toBe(false)
    const post = vi.spyOn(settingsApi, 'command').mockRejectedValue(new Error('network'))
    vi.spyOn(settingsApi, 'receipt').mockRejectedValue(new ApiRequestError('decision_settings_command_missing', 'http', 404))
    await page.click('删除活跃设置')
    expect(post.mock.calls[0][0].command.operation).toBe('remove')
    expect(post.mock.calls[0][0].command.partition.account_id).toBe('declared_pc')
  } finally { await page.close() }
})

it('saves paper risk against the authenticated projection and does not expose editable paper funds', async () => {
  const page = await render()
  const original = { ref: 'paper-ledger-original', hash }
  const position = { ...fixture.intent.command.values.position_context, state: 'flat', signed_contracts: '0', isolated_margin: '0', margin_mode: 'isolated', position_mode: 'net', leverage: '1', source: 'paper_policy', ref: original.ref, hash, account_ref: original }
  const account = { ...fixture.intent.command.values.account_snapshot, owner_id: 'owner', account_kind: 'paper', account_id: 'paper-account', source: 'paper_policy', account_mode: 'single_currency', positions: [position], equity: '123', available_margin: '123', original }
  const paperOptions = { ...options, paper_available: true, paper_reason: null, paper_accounts: [{ partition: { account_kind: 'paper', account_id: 'paper-account', instrument_id: 'okx:BTC-USDT-SWAP', profile_ref: 'server-profile' }, account_snapshot: account, position_context: position, execution_profile: options.instruments[0].execution_profile }] } as SettingsOptions
  try {
    vi.mocked(settingsApi.options).mockResolvedValue(paperOptions)
    await act(async () => window.dispatchEvent(new Event('decision-hub-paper-accounts-changed'))); await settle()
    await act(async () => { const select = page.host.querySelector('select[aria-label="设置账户来源"]') as HTMLSelectElement; select.value = 'paper'; select.dispatchEvent(new Event('change', { bubbles: true })) }); await settle(); await settle()
    expect(page.host.textContent).toContain('权益：123 USDT')
    expect(page.host.querySelector('input[aria-label="账户权益（USDT，自报）"]')).toBeNull()
    const post = vi.spyOn(settingsApi, 'command').mockRejectedValue(new Error('network'))
    vi.spyOn(settingsApi, 'receipt').mockRejectedValue(new ApiRequestError('decision_settings_command_missing', 'http', 404))
    await page.click('保存设置')
    expect(post).toHaveBeenCalledTimes(1)
    const saved = post.mock.calls[0][0].command
    expect(saved.partition.account_kind).toBe('paper')
    expect(saved.values?.account_snapshot.equity).toBe('123')
    expect(saved.values?.account_snapshot.source).toBe('paper_policy')
    expect(saved.values?.utility_policy.account_ref).toEqual(original)
  } finally { await page.close() }
})
