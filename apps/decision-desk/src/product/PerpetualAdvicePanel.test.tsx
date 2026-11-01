// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { saveSettingsSelection, settingsApi } from '../api/decisionSettings'
import settingsFixture from '../api/decisionSettingsReceiptFixture.json'
import { perpetualAdviceApi, type PerpetualAdviceSelection } from '../api/perpetualAdvice'
import { PerpetualAdvicePanel } from './PerpetualAdvicePanel'
import { decisionBodyHash } from '../api/decisionOriginals'
import presentationFixture from '../../../../artifacts/d2-perp-advice-ui/pure-presentation-fixture.json'
import persistedExample from '../../../../artifacts/d2-perpetual-advice/available-pure-persisted-example.json'
import { decisionSettingsCurrentViewV1Schema, decisionSettingsPartitionV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { DecisionSettingsPanel } from './DecisionSettingsPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const selection: PerpetualAdviceSelection = { owner_id: settingsFixture.intent.owner_id, partition: decisionSettingsPartitionV1Schema.parse(settingsFixture.intent.command.partition),
  settings_revision: settingsFixture.receipt.revision, settings_state_version: settingsFixture.receipt.state_version, horizon: '4h' }
const empty = (status = 'not_started') => ({ schema_version: 'perpetual-advice-current.v1', ...selection, status, run_id: null, report_id: null,
  snapshot: null, revision: null, comparison: null, frozen_forecast: null, target: null, cost_assumptions: null, cost_assumptions_ref: null,
  event_probability: null, market_distribution: null, candidate_probability: null,
  context: { account_snapshot: null, position_context: null, utility_policy: null }, gaps: [], updated_at: '2026-10-19T00:00:00Z', qualification: 'research_trial', execution: 'paper_only' })
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id, csrf_token: 'private-csrf', expires_at: '2099-01-01T00:00:00Z' }) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); localStorage.clear() })

it('reads only the exact signed saved partition and refuses substituted unavailable identities', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify(empty())))
  vi.stubGlobal('fetch', fetcher)
  expect((await perpetualAdviceApi.current(selection)).status).toBe('not_started')
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
  const query = new URL(url, 'http://local').searchParams
  expect(query.get('account_id')).toBe(selection.partition.account_id)
  expect(query.get('expected_settings_ref')).toBe(selection.settings_revision.ref)
  expect(query.get('expected_settings_hash')).toBe(selection.settings_revision.hash)
  expect(query.get('expected_settings_state_version')).toBe(String(selection.settings_state_version))
  expect(query.get('horizon')).toBe('4h')
  expect(init.method).toBe('GET'); expect(init.credentials).toBe('include')
  expect(new Headers(init.headers).has('X-Owner-Id')).toBe(false)
  for (const changed of [{ owner_id: 'foreign' }, { partition: { ...selection.partition, account_id: 'foreign' } }, { settings_revision: { ...selection.settings_revision, hash: 'b'.repeat(64) } }, { settings_state_version: 99 }, { horizon: '24h' }]) {
    fetcher.mockImplementation(async () => new Response(JSON.stringify({ ...empty(), ...changed })))
    await expect(perpetualAdviceApi.current(selection)).rejects.toThrow()
  }
})

it('reads selected non-four-hour advice by explicit version and rejects another horizon', async () => {
  const selected = { ...selection, horizon: '24h' as const }
  const value = { ...empty(), schema_version: 'perpetual-advice-current.v2', horizon: '24h' }
  const fetcher = vi.fn(async () => new Response(JSON.stringify(value)))
  vi.stubGlobal('fetch', fetcher)
  expect((await perpetualAdviceApi.current(selected)).status).toBe('not_started')
  expect(new URL(String((fetcher.mock.calls[0] as unknown as [string])[0]), 'http://local').searchParams.get('horizon')).toBe('24h')
  for (const changed of [{ horizon: '72h' }, { schema_version: 'perpetual-advice-current.v1' }]) {
    fetcher.mockImplementation(async () => new Response(JSON.stringify({ ...value, ...changed })))
    await expect(perpetualAdviceApi.current(selected)).rejects.toThrow()
  }
})

it('shows an unavailable real read without invented probabilities, amounts or research mutation', async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify(empty('awaiting_publication'))))
  const adviceCalls = () => fetcher.mock.calls.map(call => call as unknown as [string, RequestInit])
    .filter(([url]) => String(url).startsWith('/v1/decision-settings/advice/current?'))
  vi.stubGlobal('fetch', fetcher)
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><PerpetualAdvicePanel selection={selection} /></QueryClientProvider>))
    for (let i = 0; i < 30 && !host.textContent?.includes('等待建议发布'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(host.textContent).toContain('等待建议发布')
    expect(host.querySelector('table')).toBeNull()
    expect(host.textContent).not.toContain('10000')
    expect(adviceCalls()).toHaveLength(1)
    expect(fetcher.mock.calls.every(call => ((call as unknown as [string, RequestInit])[1].method ?? 'GET') === 'GET')).toBe(true)
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})

it('renders pure-calculator originals as three distinct research comparisons with exact 4h scope and unknown actual account outcomes', async () => {
  const original = presentationFixture.value, selected = presentationFixture.selection as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: selected.owner_id, csrf_token: 'read-session', expires_at: '2099-01-01T00:00:00Z' })
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(original))))
  const accepted = await perpetualAdviceApi.current(selected)
  // Legal noncanonical timestamp spellings belong to the raw frozen forecast.
  expect(accepted.market_distribution!.direction_distribution.as_of).toContain('.000Z')
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><PerpetualAdvicePanel selection={selected} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !host.querySelector('table'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    const rows = [...host.querySelectorAll('tbody tr')]
    expect(rows).toHaveLength(3)
    expect(rows.map(row => row.querySelector('th')!.textContent)).toEqual(expect.arrayContaining([expect.stringContaining('开多'), expect.stringContaining('开空'), expect.stringContaining('不变')]))
    for (const result of original.comparison.candidate_results) expect(rows.some(row => row.textContent?.includes(result.unit_expected_net_if_assumed_fills_usdt))).toBe(true)
    expect(host.textContent).toContain('未估计，保持未知')
    expect(host.textContent).toContain('市场方向概率')
    expect(host.textContent).toContain('净盈利概率')
    expect(host.textContent).toContain('原4小时预测窗口终点')
    expect(host.textContent).toContain('假设成交和原固定成本')
    expect(host.textContent).toContain('手动金额、止损、止盈或提前退出未另行计算盈利概率')
    expect(host.textContent).toContain('BASE 资金费现金假设：0 USDT')
    expect(host.textContent).toContain('实际未来资金费未知')
    expect(host.textContent).toContain('权益：未知')
    expect(host.textContent).toContain('本次研究冻结的账户原件')
    expect(host.textContent).toContain('不表示当前账本余额')
    expect(host.textContent).toContain('仅按假设空仓')
    expect(host.textContent).not.toContain('72 小时')
    expect(host.querySelector('a')!.getAttribute('href')).toBe('#/report?run=run-pure')
    const expired = structuredClone(original); expired.status = 'expired'; expired.updated_at = '2026-01-01T05:00:00Z'
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(expired)))
    await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '重新读取建议原件')!.click())
    for (let i = 0; i < 50 && !host.textContent?.includes('已到期'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(host.textContent).toContain('已到期')
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})

it('rejects substituted comparison cash, original forecast and a validly shaped legacy horizon before presentation', async () => {
  const selected = presentationFixture.selection as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: selected.owner_id, csrf_token: 'read-session', expires_at: '2099-01-01T00:00:00Z' })
  for (const mutation of [
    (value: typeof presentationFixture.value) => { value.comparison.candidate_results[0].unit_expected_net_if_assumed_fills_usdt = '9999' },
    (value: typeof presentationFixture.value) => { value.market_distribution.direction_distribution.as_of = '2026-01-01T00:01:00Z' },
    (value: typeof presentationFixture.value) => { value.target.horizon = '24h'; value.target.duration_seconds = 86400 },
  ]) {
    const value = structuredClone(presentationFixture.value); mutation(value)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(value))))
    await expect(perpetualAdviceApi.current(selected)).rejects.toThrow()
  }
})

it('keeps invalidated advice originals and watch readback visible while rejecting tampered originals', async () => {
  const selected = presentationFixture.selection as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: selected.owner_id, csrf_token: 'read-session', expires_at: '2099-01-01T00:00:00Z' })
  const invalidated = { ...structuredClone(presentationFixture.value), status: 'invalidated',
    gaps: [...presentationFixture.value.gaps, 'perpetual_condition_invalidated_new_exposure'] }
  let response = invalidated
  vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request) => new Response(JSON.stringify(
    String(url).startsWith('/v1/decision-settings/advice/current?') ? response
      : String(url).includes('/plans/watches/continuous/') ? { schema_version: 'perpetual-plan-watch-view.v3', receipt: null, progress: null, condition_monitoring: null }
        : String(url).includes('/plans/watches/current?') ? { schema_version: 'perpetual-plan-watch-view.v1', receipt: null, expiry: null }
          : { detail: 'not_found' }), { status: String(url).includes('/manual') || String(url).includes('/options') ? 404 : 200 })))
  const accepted = await perpetualAdviceApi.current(selected)
  expect(accepted.status).toBe('invalidated')
  expect(accepted.revision!.payload_hash).toBe(presentationFixture.value.revision.payload_hash)
  expect(accepted.frozen_forecast).toEqual(presentationFixture.value.frozen_forecast)
  response = { ...invalidated, revision: { ...invalidated.revision, payload_hash: 'f'.repeat(64) } }
  await expect(perpetualAdviceApi.current(selected)).rejects.toThrow('perpetual_advice_original_hash_mismatch')
  response = invalidated
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><PerpetualAdvicePanel selection={selected} /></QueryClientProvider>))
    await waitForResultUi(() => [...host.querySelectorAll('button')].some(button => button.textContent === '重新读取持续跟踪状态' && !button.disabled))
    expect(host.textContent).toContain('这版建议的条件已失效')
    expect(host.textContent).toContain('服务端已拒绝这版旧建议的新增模拟开仓或加仓')
    expect(host.textContent).toContain('已有模拟仓位的保护继续有效')
    expect(host.textContent).toContain('原研究首选')
    expect(host.textContent).not.toContain('当前首选：')
    expect(host.textContent).toContain('原概率与路径保持冻结')
    expect(host.querySelectorAll('tbody tr')).toHaveLength(3)
    expect(host.textContent).toContain('43.5%')
    expect([...host.querySelectorAll('button')].some(button => button.textContent === '重新读取持续跟踪状态' && !button.disabled)).toBe(true)
    expect([...host.querySelectorAll('button')].some(button => button.textContent === '准备手动模拟')).toBe(false)
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})

it('accepts the backend real SQLite settings/admission/ActionRepository read example without changing any originals', async () => {
  // Backend provenance: real persistence, pure-derivation subjective/reviewer fixture;
  // this does not claim live Graph/provider review or calibrated financial advice.
  const selected = { owner_id: persistedExample.owner_id, partition: persistedExample.partition, settings_revision: persistedExample.settings_revision,
    settings_state_version: persistedExample.settings_state_version, horizon: '4h' } as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: selected.owner_id, csrf_token: 'signed-read', expires_at: '2099-01-01T00:00:00Z' })
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(persistedExample))))
  const value = await perpetualAdviceApi.current(selected)
  expect(value.status).toBe('available')
  expect(value.snapshot!.snapshot_hash).toBe(persistedExample.snapshot!.snapshot_hash)
  expect(value.comparison!.candidate_results).toEqual(persistedExample.comparison!.candidate_results)
})

it('mounts the real settings saved block and reads advice for the saved revision without submitting research', async () => {
  const view = decisionSettingsCurrentViewV1Schema.parse({ schema_version: 'decision-settings-current-view.v1', state_version: 1,
    reevaluation_intent_state: 'pending', scan_generation: 1, scan_after_plan_id: null,
    revision: { schema_version: 'decision-settings-revision.v1', revision_id: settingsFixture.receipt.revision.ref, settings_key: settingsFixture.receipt.settings_key,
      owner_id: selection.owner_id, partition: selection.partition, revision_no: 1, parent: null, command_hash: settingsFixture.receipt.command_hash,
      operation: 'upsert', values: settingsFixture.intent.command.values, created_at: settingsFixture.receipt.accepted_at, payload_hash: settingsFixture.receipt.revision.hash } })
  saveSettingsSelection(selection.owner_id, selection.partition)
  vi.spyOn(settingsApi, 'options').mockResolvedValue({ schema_version: 'decision-settings-options.v1', owner_id: selection.owner_id,
    declared_account_id: selection.partition.account_id, availability: 'unavailable', reason: 'decision_settings_instrument_original_unavailable',
    paper_available: false, paper_reason: 'unavailable', paper_accounts: [], instruments: [] })
  vi.spyOn(settingsApi, 'current').mockResolvedValue(view)
  const fetcher = vi.fn(async () => new Response(JSON.stringify(empty())))
  const adviceCalls = () => fetcher.mock.calls.map(call => call as unknown as [string, RequestInit])
    .filter(([url]) => String(url).startsWith('/v1/decision-settings/advice/current?'))
  vi.stubGlobal('fetch', fetcher)
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><DecisionSettingsPanel /></QueryClientProvider>))
    for (let i = 0; i < 50 && !host.textContent?.includes('当前保存设置尚未发起研究'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(host.querySelector('section[aria-label="永续4小时研究"]')).not.toBeNull()
    expect(host.querySelector('section[aria-label="已发布永续4小时建议"]')).not.toBeNull()
    expect(host.textContent).toContain('当前保存设置尚未发起研究')
    expect(adviceCalls()).toHaveLength(1)
    expect(adviceCalls()[0][1].method).toBe('GET')
    fetcher.mockImplementation(async () => new Response(JSON.stringify({ ...empty(), schema_version: 'perpetual-advice-current.v2', horizon: '24h' })))
    const selector = host.querySelector('select[aria-label="永续预测期限"]') as HTMLSelectElement
    await act(async () => { selector.value = '24h'; selector.dispatchEvent(new Event('change', { bubbles: true })) })
    for (let i = 0; i < 30 && adviceCalls().length < 2; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(host.querySelector('section[aria-label="永续24小时研究"]')).not.toBeNull()
    expect(host.querySelector('section[aria-label="已发布永续24小时建议"]')).not.toBeNull()
    expect(adviceCalls()).toHaveLength(2)
    expect(new URL(adviceCalls()[1][0], 'http://local').searchParams.get('horizon')).toBe('24h')
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})

// U06: keep result reads separate from ordinary advice/PAPER reads.
const resultPath = '/v1/decision-settings/research/results/run-pure'
function forecastResultFixture(status: 'not_due' | 'pending' | 'unsupported' | 'observed' = 'observed', advice = presentationFixture.value) {
  const target = advice.target
  return {
    schema_version: 'perpetual-forecast-result-view.v1', status,
    reason_code: status === 'observed' ? null : status === 'not_due' ? 'forecast_target_not_due' : status === 'pending' ? 'forecast_result_not_recorded' : 'forecast_horizon_unsupported',
    owner_id: advice.owner_id, run_id: advice.run_id, instrument_id: advice.partition.instrument_id, horizon: advice.horizon,
    read_at: status === 'not_due' ? '2026-01-01T04:25:00Z' : '2026-01-01T05:00:00Z',
    admission_ref: { ref: 'fixture:admission', hash: 'a'.repeat(64) }, target_ref: advice.snapshot.bindings.final_target, target,
    result: status !== 'observed' ? null : {
      experience_ref: { ref: 'perp_experience_fixture', hash: 'e'.repeat(64) },
      snapshot_ref: { ref: advice.snapshot.id, hash: advice.snapshot.snapshot_hash },
      final_target_ref: advice.snapshot.bindings.final_target, frozen_forecast_ref: advice.snapshot.bindings.frozen_forecast,
      market_distribution_ref: advice.frozen_forecast.market_distribution_ref, method_ref: target.method_ref,
      target_family_ref: target.target_family_ref, label_rule_ref: target.label_rule_ref,
      direction_estimate: advice.market_distribution.direction_distribution,
      actual_category: 'up', actual_return_fraction: '0.006', multiclass_brier: '0.47915',
      known_at: '2026-01-01T04:27:00Z', recorded_at: '2026-01-01T04:28:00Z', gaps: [],
      calculation_method_hash: 'c'.repeat(64), qualification: 'research_trial', financial_qualified: false, skill_consumed: false, trade_outcome: false,
    },
  }
}
const expiredAdvice = () => ({ ...structuredClone(presentationFixture.value), status: 'expired', updated_at: '2026-01-01T05:00:00Z' })
async function waitForResultUi(predicate: () => boolean) {
  for (let i = 0; i < 60 && !predicate(); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(predicate()).toBe(true)
}

it('reads U06 only on demand and distinguishes not-due, pending, unsupported and observed without changing ordinary advice', async () => {
  const selected = presentationFixture.selection as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: selected.owner_id, csrf_token: 'read-session', expires_at: '2099-01-01T00:00:00Z' })
  for (const status of ['not_due', 'pending', 'unsupported', 'observed'] as const) {
    const advice = expiredAdvice()
    if (status === 'unsupported') {
      advice.horizon = '24h'; advice.target.horizon = '24h'; advice.target.duration_seconds = 86400
      advice.target.window_end = '2026-01-02T00:26:00Z'
      advice.snapshot.bindings.final_target = { ref: 'fixture:target-24h', hash: await decisionBodyHash(advice.target) }
    }
    const result = forecastResultFixture(status, advice)
    const activeSelection = status === 'unsupported' ? { ...selected, horizon: '24h' as const } : selected
    // The non-4h presentation is already authenticated by the advice reader; this case tests result UI only.
    const adviceSpy = vi.spyOn(perpetualAdviceApi, 'current').mockResolvedValue({ ...advice, horizon: activeSelection.horizon } as Awaited<ReturnType<typeof perpetualAdviceApi.current>>)
    const fetcher = vi.fn(async () => new Response(JSON.stringify(result)))
    vi.stubGlobal('fetch', fetcher)
    const host = document.createElement('div'); document.body.append(host)
    const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    try {
      await act(async () => root.render(<QueryClientProvider client={client}><PerpetualAdvicePanel selection={activeSelection} /></QueryClientProvider>))
      await waitForResultUi(() => Boolean(host.querySelector('table')))
      expect(fetcher.mock.calls.filter(call => String((call as unknown as [string])[0]).includes('/research/results/'))).toHaveLength(0)
      const button = [...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')
      expect(button).toBeDefined()
      await act(async () => button!.click())
      const labels = { not_due: '预测窗口尚未结束', pending: '到期结果尚未形成', unsupported: '当前期限暂不支持到期评分', observed: '实际分类：上涨' }
      await waitForResultUi(() => Boolean(host.textContent?.includes(labels[status])))
      expect(fetcher.mock.calls.filter(call => String((call as unknown as [string])[0]).includes('/research/results/'))).toHaveLength(1)
      expect(adviceSpy).toHaveBeenCalledTimes(1)
      expect(host.querySelectorAll('tbody tr')).toHaveLength(3)
      expect(host.textContent).toContain('不是模拟或真实交易盈亏')
      if (status === 'observed') {
        expect(host.textContent).toContain('事前市场方向概率')
        expect(host.textContent).toContain('43.5%')
        expect(host.textContent).toContain('0.6%')
        expect(host.textContent).toContain('0.47915')
        expect(host.textContent).toContain('越低越好')
        expect(host.textContent).toContain('单个样本不是胜率')
        expect(host.querySelector('section[aria-label="预测到期结果"] details')?.hasAttribute('open')).toBe(false)
      }
    } finally { await act(async () => root.unmount()); client.clear(); host.remove(); adviceSpy.mockRestore() }
  }
})

it('aborts and clears U06 on selection, session and unmount and ignores a late old response', async () => {
  const selected = presentationFixture.selection as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: selected.owner_id, csrf_token: 'read-session', expires_at: '2099-01-01T00:00:00Z' })
  vi.spyOn(perpetualAdviceApi, 'current').mockResolvedValue(expiredAdvice() as Awaited<ReturnType<typeof perpetualAdviceApi.current>>)
  const pending: { signal: AbortSignal; resolve: (value: Response) => void }[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => String(url).startsWith('/v1/decision-settings/research/results/')
    ? new Promise<Response>(resolve => pending.push({ signal: init!.signal!, resolve }))
    : Promise.resolve(new Response(JSON.stringify({ detail: 'not_found' }), { status: 404 }))))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const draw = (input: PerpetualAdviceSelection) => root.render(<QueryClientProvider client={client}><PerpetualAdvicePanel selection={input} /></QueryClientProvider>)
  try {
    await act(async () => draw(selected)); await waitForResultUi(() => Boolean([...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')))
    await act(async () => [...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')!.click())
    expect(pending).toHaveLength(1)
    await act(async () => draw({ ...selected, settings_state_version: selected.settings_state_version + 1 }))
    expect(pending[0].signal.aborted).toBe(true)
    await act(async () => pending[0].resolve(new Response(JSON.stringify(forecastResultFixture()))))
    expect(host.textContent).not.toContain('实际分类：上涨')
    await waitForResultUi(() => Boolean([...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')))
    await act(async () => [...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')!.click())
    await act(async () => window.dispatchEvent(new Event('decision-hub-settings-session-changed')))
    expect(pending[1].signal.aborted).toBe(true)
    await act(async () => pending[1].resolve(new Response(JSON.stringify(forecastResultFixture()))))
    expect(host.textContent).not.toContain('实际分类：上涨')
    await act(async () => [...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')!.click())
    await act(async () => root.unmount())
    expect(pending[2].signal.aborted).toBe(true)
    await act(async () => pending[2].resolve(new Response(JSON.stringify(forecastResultFixture()))))
  } finally { if (host.firstChild) await act(async () => root.unmount()); client.clear(); host.remove() }
})

it('keeps U06 authorization and original failures explicit without replacing advice or falling back to old results', async () => {
  const selected = presentationFixture.selection as PerpetualAdviceSelection
  vi.mocked(settingsApi.session).mockReturnValue({ owner_id: selected.owner_id, csrf_token: 'read-session', expires_at: '2099-01-01T00:00:00Z' })
  vi.spyOn(perpetualAdviceApi, 'current').mockResolvedValue(expiredAdvice() as Awaited<ReturnType<typeof perpetualAdviceApi.current>>)
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: 'rejected' }), { status: 401 }))
  vi.stubGlobal('fetch', fetcher)
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><PerpetualAdvicePanel selection={selected} /></QueryClientProvider>))
    await waitForResultUi(() => Boolean([...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')))
    for (const [status, label] of [[401, '本机身份已失效'], [403, '当前身份无权读取这份结果'], [409, '到期结果原件无法核对']] as const) {
      fetcher.mockImplementation(async () => new Response(JSON.stringify({ detail: 'rejected' }), { status }))
      await act(async () => [...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')!.click())
      await waitForResultUi(() => Boolean(host.textContent?.includes(label)))
      expect(host.textContent).not.toContain('实际分类：上涨')
      expect(host.querySelectorAll('tbody tr')).toHaveLength(3)
    }
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})
