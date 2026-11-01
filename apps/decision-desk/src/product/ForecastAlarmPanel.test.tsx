// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { forecastAlarmsApi, type ForecastAlarmReceipt, type ForecastAlarmView } from '../api/forecastAlarms'
import { ForecastAlarmPanel } from './ForecastAlarmPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const BTC = 'crypto:spot:okx:BTC-USDT:trade:1m'
const at = '2026-10-08T00:00:00Z'
const item: ForecastAlarmView = { alarm: { schema_version: 'forecast-alarm.v1', alarm_policy: 'shadow-forecast-ui-receipts.v1',
  alarm_id: 'synthetic-alarm', trial_id: 'synthetic-trial', prediction_id: 'synthetic-prediction', prediction_hash: 'a'.repeat(64),
  method: 'COND_VOL_V1', scope_id: BTC, cutoff_at: at, published_at: at, known_at: at,
  window_start_at: '2026-10-08T00:01:00Z', window_end_at: '2026-10-09T00:01:00Z', direction: 'long', threshold: '0.02', frequency: '0.3',
  experimental: true, probability_qualified: false, trading_authority: false, payload_hash: 'b'.repeat(64),
}, display_status: 'not_recorded', exposure_status: 'not_recorded', acknowledged: false }
function receipt(kind: ForecastAlarmReceipt['receipt_kind']): ForecastAlarmReceipt {
  return { schema_version: 'forecast-alarm-receipt.v1', receipt_id: kind, alarm_id: item.alarm.alarm_id,
    alarm_hash: item.alarm.payload_hash, receipt_kind: kind, owner_id: 'owner', request_id: 'synthetic', known_at: at,
    parent_receipt_id: kind === 'displayed' ? null : kind === 'visible' ? 'displayed' : 'visible', parent_receipt_hash: kind === 'displayed' ? null : 'c'.repeat(64),
    surface: 'pc', visible_duration_ms: kind === 'visible' ? 1000 : null, evidence_source: 'pc_browser_reported', payload_hash: 'c'.repeat(64) }
}
let container: HTMLDivElement, root: Root, client: QueryClient
let intersection: (visible: boolean) => void
async function mount() {
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback: IntersectionObserverCallback) {
      intersection = visible => callback([{ isIntersecting: visible, intersectionRatio: visible ? 1 : 0 } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
    }
    observe() {} disconnect() {}
  })
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root.render(<QueryClientProvider client={client}><ForecastAlarmPanel scopeId={BTC} /></QueryClientProvider>))
}
async function open() {
  await act(async () => { const details = container.querySelector('details')!; details.open = true; details.dispatchEvent(new Event('toggle')) })
}
async function until(check: () => boolean) {
  for (let i = 0; i < 150 && !check(); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(check()).toBe(true)
}
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  client?.clear(); container?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})
it('does not fetch or create display receipts while collapsed', async () => {
  const read = vi.spyOn(forecastAlarmsApi, 'list')
  const display = vi.spyOn(forecastAlarmsApi, 'display')
  await mount()
  expect(container.querySelector('details')?.open).toBe(false)
  expect(read).not.toHaveBeenCalled(); expect(display).not.toHaveBeenCalled()
})
it('requires continuous foreground visibility before exposure and acknowledges the exact receipt', async () => {
  vi.spyOn(forecastAlarmsApi, 'list').mockResolvedValue({ schema_version: 'forecast-alarm-list.v1', scope_id: BTC, as_of: at,
    items: [item], experimental: true, default_collapsed: true, probability_qualified: false, trading_authority: false })
  const display = vi.spyOn(forecastAlarmsApi, 'display').mockResolvedValue(receipt('displayed'))
  const exposure = vi.spyOn(forecastAlarmsApi, 'exposure').mockResolvedValue(receipt('visible'))
  const ack = vi.spyOn(forecastAlarmsApi, 'acknowledge').mockResolvedValue(receipt('acknowledged'))
  await mount(); await open(); await until(() => Boolean(container.querySelector('[aria-label="实验预警"]')))
  expect(display).not.toHaveBeenCalled(); expect(exposure).not.toHaveBeenCalled()
  await act(async () => intersection(true)); await until(() => display.mock.calls.length === 1)
  await act(async () => intersection(false))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 1300)) })
  expect(exposure).not.toHaveBeenCalled()
  await act(async () => intersection(true)); await until(() => exposure.mock.calls.length === 1)
  expect(exposure).toHaveBeenCalledWith(item.alarm, receipt('displayed'))
  await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === '我已知悉')!.click())
  await until(() => container.textContent?.includes('已知悉') ?? false)
  expect(ack).toHaveBeenCalledWith(item.alarm, receipt('visible'))
  expect(container.textContent).toContain('不代表持续阅读或避险收益')
  expect(container.textContent).not.toContain('experimental_uncalibrated')
})
