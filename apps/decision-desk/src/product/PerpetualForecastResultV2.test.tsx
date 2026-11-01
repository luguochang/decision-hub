// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { settingsApi } from '../api/decisionSettings'
import { perpetualAdviceApi, type PerpetualAdviceSelection } from '../api/perpetualAdvice'
import { PerpetualAdvicePanel } from './PerpetualAdvicePanel'
import fixture from '../api/fixtures/perpetualForecastResult.v2/ETH24h.json'
import infinite from '../api/fixtures/perpetualForecastResult.v2/infinite.json'
import unavailable from '../api/fixtures/perpetualForecastResult.v2/unavailable.json'

const selection: PerpetualAdviceSelection = {
  owner_id: fixture.advice.owner_id,
  partition: fixture.advice.partition as PerpetualAdviceSelection['partition'],
  settings_revision: fixture.advice.settings_revision,
  settings_state_version: fixture.advice.settings_state_version,
  horizon: '24h',
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id,
    csrf_token: 'synthetic-read-session', expires_at: '2099-01-01T00:00:00Z' })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); localStorage.clear() })
async function waitFor(predicate: () => boolean) {
  for (let i = 0; i < 60 && !predicate(); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(predicate()).toBe(true)
}
it.each([
  ['finite', fixture, '0.57092954783569610887108353721088331124837272291481'],
  ['infinite', infinite, '无穷（原预测给实际结果的概率为 0）'],
  ['unavailable', unavailable, '未评分（方向概率未知）'],
] as const)('shows original %s log loss only after a result read without manufacturing calibration', async (status, wire, label) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(wire.advice))))
  const advice = await perpetualAdviceApi.current(selection)
  vi.spyOn(perpetualAdviceApi, 'current').mockResolvedValue(advice)
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(wire.result))))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><PerpetualAdvicePanel selection={selection} /></QueryClientProvider>))
    await waitFor(() => Boolean([...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')))
    expect(host.textContent).not.toContain('Log loss 评分')
    await act(async () => [...host.querySelectorAll('button')].find(item => item.textContent === '读取到期结果')!.click())
    await waitFor(() => Boolean(host.textContent?.includes('Log loss 评分')))
    expect(host.textContent).toContain(label)
    expect(host.textContent).toContain('逐样本可靠性记录')
    expect(host.textContent).toContain('事件族：未知；市场状态：未知')
    expect(host.textContent).toContain('不能证明预测已校准')
    expect(host.textContent).toContain('未通过金融资格核验')
    const resultSection = host.querySelector('[aria-label="预测到期结果"]')!
    if (status === 'unavailable') {
      expect(resultSection.textContent).toContain('未形成可靠性记录（方向概率未知）')
      expect(resultSection.textContent).not.toContain('原预测概率 ')
    } else expect(resultSection.textContent).toContain('原预测概率 ')
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})
