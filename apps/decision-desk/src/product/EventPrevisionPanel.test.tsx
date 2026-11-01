// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { eventPrevisionDetailSchema, eventPrevisionsApi } from '../api/eventPrevisions'
import { EventPrevisionPanel } from './EventPrevisionPanel'

vi.mock('../api/eventPrevisions', async original => ({
  ...await original<typeof import('../api/eventPrevisions')>(),
  eventPrevisionsApi: { list: vi.fn(), detail: vi.fn() },
}))
vi.mock('./EventLifecyclePanel', () => ({ EventLifecyclePanel: () => <section aria-label="公布与后续跟踪">跟踪状态</section> }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('keeps one forecast and independent lifecycle through refreshes and shows the active rule', async () => {
  const at = '2026-10-15T12:00:00Z', t0 = '2026-11-10T13:30:00Z'
  const event = { event_key: 'fred-cpi:2026-11-10', title: '美国 CPI', reference_period: '2026-10',
    scheduled_at: t0, state: 'published' as const, forecast_id: 'forecast',
    last_error_code: null, next_attempt_at: null, updated_at: at }
  const estimate = { target: 'fixture', probability_status: 'unavailable', basis: 'fixture', scenario_conditioned: false,
    value: null, lower_95: null, upper_95: null, events: 0, entered: 0, successes: 0, assumptions: [] }
  const scenarios = (['cooler', 'near', 'hotter'] as const).flatMap(h => (['cooler', 'near', 'hotter'] as const).map(c => ({
    scenario_id: `cpi:headline_${h}:core_${c}`, headline_band: h, core_band: c, probability: '0.1111111111111111', historical_matches: 1,
  })))
  const data = eventPrevisionDetailSchema.parse({
    schema_version: 'event-prevision-detail.v1', as_of: at, event, snapshot_ref: 'fixture', snapshot_hash: 'a'.repeat(64),
    estimate_as_of: at, published_at: at, probability_status: 'estimated_uncalibrated', baseline_kind: 'prior_month',
    headline_baseline: '0.3', core_baseline: '0.2', tolerance_pp: '0.05', target_definition: 'fixture', sample_count: 9,
    scenarios, assumptions: [], assets: [{ instrument: 'BTC-USDT', state: 'available', as_of: at, published_at: at,
      refresh_required_at: t0, asset_estimate: null, net_profit_estimate: null, net_profit_given_entry_estimate: null,
      entry_valid_from: t0, entry_valid_until: t0, exit_valid_until: t0, gaps: [], snapshot_ref: 'fixture',
      snapshot_hash: 'b'.repeat(64), monitoring_armed: false, plans: [{ scenario_id: scenarios[0].scenario_id,
        event_probability: scenarios[0].probability, main_chain: [], counter_mechanism: 'fixture', activation_required: [],
        flat_entry: ['旧版6小时收益规则'], flat_exit: ['旧版退出'], flat_invalidation: ['旧版失效'],
        held_long_entry: ['旧版减险'], held_long_invalidation: ['旧版正收益失效'],
        asset_estimate: estimate, net_profit_estimate: estimate, armed: false, current_confirmation: {
          confirmation_protocol: 'event-confirmation.v1', strategy_target: 'cpi_event_anchored_policy_net_profit_event_24h.v1',
          policy_hash: 'c'.repeat(64), flat_entry: '公告锚定收益与公告前20分钟量基准确认', flat_exit: '公告锚定保护退出',
          flat_invalidation: '窗口结束失效', long_entry: '先涨不关闭保护，随后下跌连续确认减险', long_invalidation: '减险窗口结束',
          probability_status: 'unknown', probability_gap: '新规则胜率尚未校准，不能使用旧规则概率。',
        } }] }],
  })
  vi.mocked(eventPrevisionsApi.list).mockResolvedValue({ schema_version: 'event-prevision-list.v1', as_of: at, events: [event], uncovered_families: [] })
  vi.mocked(eventPrevisionsApi.detail).mockResolvedValue(data)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const host = document.createElement('div'), root = createRoot(host)
  const settle = async () => { await act(async () => { await new Promise(r => setTimeout(r, 5)) }) }
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><EventPrevisionPanel /></QueryClientProvider>))
    await settle(); await settle()
    for (let i = 0; i < 8; i++) {
      await act(async () => { client.setQueryData(['event-prevision-detail', event.event_key], { ...data, as_of: `2026-10-15T12:00:0${i}Z` }) })
      await settle()
    }
    expect(host.querySelectorAll('table[aria-label="CPI 事前联合情景概率"]')).toHaveLength(1)
    expect(host.querySelectorAll('[aria-label="公布与后续跟踪"]')).toHaveLength(1)
    expect(host.textContent).toContain('公告锚定收益与公告前20分钟量基准确认')
    expect(host.textContent).toContain('新规则胜率尚未校准')
    expect(host.querySelector('details')?.textContent).toBeTruthy()
    const legacy = [...host.querySelectorAll('details')].find(d => d.querySelector('summary')?.textContent?.includes('原版规则'))
    expect(legacy).toBeDefined()
    expect(legacy?.open).toBe(false)
    expect(legacy?.textContent).toContain('旧版6小时收益规则')
  } finally { await act(async () => root.unmount()); client.clear(); vi.clearAllMocks() }
})
