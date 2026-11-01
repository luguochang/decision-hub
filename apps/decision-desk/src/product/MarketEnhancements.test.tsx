// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { enhancementRoutes, marketEnhancementsApi, type EnhancementView } from '../api/marketEnhancements'
import { MarketEnhancements } from './MarketEnhancements'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const BTC = 'crypto:spot:okx:BTC-USDT:trade:1m'
const ETH = 'crypto:spot:okx:ETH-USDT:trade:1m'
const at = '2026-10-08T04:00:00Z'
function fixture(scopeId = BTC): EnhancementView {
  return { schema_version: 'market-enhancements-view.v1', scope_id: scopeId,
    instrument: scopeId === ETH ? 'ETH-USDT' : 'BTC-USDT', as_of: at, integrity: 'stored_payload_hash',
    routes: enhancementRoutes.map(route => ({ route, registration_id: 'synthetic-registration', registered_at: at,
      data_status: 'unavailable', admission_state: 'not_admitted', receipt_id: 'synthetic-receipt', saved_at: at,
      cutoff_at: at, source_known_at: null, observation_date: null, features: [],
      missing_reasons: [route === 'MACRO_STATE_V1' ? 'macro_observations_unavailable' : route === 'CROWDING_V1' ? 'crowding_venue_contract_coverage_unavailable' : 'event_actual_unavailable'],
      original_count: 0, excluded_source_count: 0, scan_truncated: false, data_pit_verified: false,
      probability: null, probability_qualified: false, statistical_status: 'insufficient_evidence', promotion: false })),
  }
}
let container: HTMLDivElement
let root: Root
let client: QueryClient
async function mount() {
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await render(BTC)
}
async function render(scopeId: string) {
  await act(async () => root.render(<QueryClientProvider client={client}><MarketEnhancements scopeId={scopeId} /></QueryClientProvider>))
}
async function open() {
  await act(async () => { const details = container.querySelector('details')!; details.open = true; details.dispatchEvent(new Event('toggle')) })
}
async function until(check: () => boolean) {
  for (let i = 0; i < 60 && !check(); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  expect(check()).toBe(true)
}
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  client?.clear(); container?.remove(); vi.restoreAllMocks()
})
it('is collapsed by default and reads three independent unknown routes only after opening', async () => {
  const read = vi.spyOn(marketEnhancementsApi, 'summary').mockResolvedValue(fixture())
  await mount()
  expect(container.querySelector('details')?.open).toBe(false)
  expect(read).not.toHaveBeenCalled()
  await open()
  await until(() => Boolean(container.textContent?.includes('持仓拥挤度')))
  expect(container.textContent).toContain('事件预期差')
  expect(container.textContent).toContain('尚无已接受、归档且带首次可得时间')
  expect(container.textContent).toContain('没有通过预测优势验证')
  expect(container.textContent).not.toContain('not_admitted')
  expect(container.textContent).not.toContain('insufficient_evidence')
  expect(container.textContent).not.toContain('crowding_venue_contract')
})
it('shows refresh failure and clears the other asset data while changing scope', async () => {
  const read = vi.spyOn(marketEnhancementsApi, 'summary').mockResolvedValue(fixture())
  await mount(); await open()
  await until(() => Boolean(container.textContent?.includes('尚无已接受')))
  read.mockRejectedValueOnce(new Error('synthetic offline'))
  await act(async () => container.querySelector('button')!.click())
  await until(() => Boolean(container.querySelector('[role=alert]')))
  expect(container.textContent).toContain('已有记录仅供参照')
  read.mockImplementation(async scopeId => fixture(scopeId))
  await render(ETH)
  await until(() => read.mock.calls.some(call => call[0] === ETH))
  expect(read).toHaveBeenLastCalledWith(ETH, expect.any(AbortSignal))
  expect(container.textContent).not.toContain('synthetic offline')
})
it('keeps observation date distinct from first known and renders descriptive yield as a percentage', async () => {
  const value = fixture()
  value.routes[0] = { ...value.routes[0], observation_date: '2026-10-06', source_known_at: at,
    features: [{ name: 'DGS2:fred-public:level', value: 4.2, unit: 'yield_percent', status: 'available', reason_code: null }],
    missing_reasons: ['macro_vintage_or_exact_public_time_unknown_descriptive_only'] }
  vi.spyOn(marketEnhancementsApi, 'summary').mockResolvedValue(value)
  await mount(); await open()
  await until(() => Boolean(container.textContent?.includes('4.2%')))
  expect(container.textContent).toContain('最新观察日期：2026-10-06')
  expect(container.textContent).toContain('本机首次可得')
  expect(container.textContent).toContain('观察日期不等于发布时间')
  expect(container.textContent).not.toContain('yield_percent')
  expect(container.textContent).not.toContain('vintage')
})
