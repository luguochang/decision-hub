// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { riskApi } from '../api/risk'
import { riskScopeFixture, riskSummaryFixture, riskTimelineFixture, RISK_AT } from '../api/riskFixtures'
import type { ResearchRunView } from '../api/types'
import { ProductPages, recentRunDescription } from './ProductPages'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it.each(['loading', 'failed'] as const)('does not claim no active research or upcoming events while %s', async (state) => {
  const response = () => state === 'failed' ? Promise.reject(new Error('unavailable')) : new Promise<never>(() => undefined)
  vi.spyOn(api, 'operations').mockImplementation(response)
  vi.spyOn(api, 'researchRuns').mockImplementation(response)
  vi.spyOn(api, 'researchHome').mockImplementation(response)
  vi.spyOn(api, 'macroEvents').mockImplementation(response)
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="home" onNavigate={vi.fn()} /></QueryClientProvider>))
    if (state === 'failed') for (let i = 0; i < 50 && !container.textContent?.includes('进行中的研究暂时无法确认'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.textContent).not.toContain('目前没有进行中的研究')
    expect(container.textContent).not.toContain('还没有研究任务')
    expect(container.textContent).not.toContain('当前日历没有可确认的下一项官方发布')
    expect(container.textContent).toContain(state === 'loading' ? '正在读取进行中的研究' : '进行中的研究暂时无法确认')
    expect(container.textContent).toContain(state === 'loading' ? '正在读取后续事件' : '后续事件暂时无法确认')
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks() }
})


it.each([false, true])('shows the latest automatic report and keeps explicit Run selection (%s)', async (explicit) => {
  const now = '2026-09-30T12:07:58Z'
  const saved = { run_id: 'auto-report', event_title: '自动复查后的报告', artifact_id: 'art-1', status: 'research_only', admission_origin: 'scheduled_recheck', updated_at: now } as ResearchRunView
  const active = { ...saved, run_id: 'running-new', event_title: '正在取证的新任务', artifact_id: null, status: 'researching' } as ResearchRunView
  const selected = { ...saved, run_id: 'manual-old', event_title: '明确指定的旧研究', admission_origin: 'manual' } as ResearchRunView
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  const list = vi.spyOn(api, 'researchRuns')
  vi.spyOn(api, 'researchHome').mockResolvedValue({ latest_report: saved, latest_report_at: now, latest_report_headline: '原报告结论预览与限制', latest_run: active, active_runs: [active], active_count: 130, generated_at: now })
  vi.spyOn(api, 'researchRun').mockResolvedValue({ run: selected } as Awaited<ReturnType<typeof api.researchRun>>)
  const navigate = vi.fn()
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="home" params={new URLSearchParams(explicit ? 'run=manual-old' : '')} onNavigate={navigate} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !container.textContent?.includes('阅读结论与依据'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.textContent).toContain(explicit ? '明确指定的旧研究' : '自动复查后的报告')
    expect(container.textContent).toContain('正在取证的新任务')
    expect(container.textContent).toContain('共 130 项，显示最近 3 项')
    if (!explicit) {
      expect(container.querySelector('.product-report-preview')?.textContent).toBe('旧版本报告摘要：综合判断来源未确认，原文仅作参考。')
      expect(container.querySelector('.product-report-original p')?.textContent).toBe('原报告结论预览与限制')
      expect(container.querySelector('.product-home-report-title')?.textContent).toBe('最近已保存的研究结论')
      expect(container.querySelector('.product-report-question p')?.textContent).toBe(saved.event_title)
    }
    expect(list).not.toHaveBeenCalled()
    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('阅读结论与依据'))!
    await act(async () => open.click())
    expect(navigate).toHaveBeenCalledWith('report', { run: explicit ? 'manual-old' : 'auto-report', back: explicit ? '#/home?run=manual-old' : '#/home' })
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks() }
})


it('does not claim a report exists for a terminal task with no artifact', () => {
  for (const status of ['completed', 'degraded', 'research_only'] as const) {
    expect(recentRunDescription({ status, artifact_id: null } as ResearchRunView)).toContain('没有已保存的报告')
  }
  expect(recentRunDescription({ status: 'cancelled', artifact_id: null } as ResearchRunView)).toContain('已停止')
})


it('keeps the project BTC report task visible when the risk observation selects ETH', async () => {
  const eth = { ...riskScopeFixture, scope_id: 'okx:spot:ETH-USDT:trade', label: '以太坊现货', instrument: 'ETH-USDT' }
  const saved = { run_id: 'btc-saved-report', event_title: 'BTC 现货与宏观风险研究（未来 24 小时）', artifact_id: 'btc-artifact', status: 'research_only', admission_origin: 'automatic', updated_at: RISK_AT } as ResearchRunView
  sessionStorage.setItem('market-risk-selected-scope.v1', eth.scope_id)
  vi.stubGlobal('fetch', vi.fn(() => new Promise<never>(() => undefined)))
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  vi.spyOn(api, 'researchHome').mockResolvedValue({ latest_report: saved, latest_report_at: RISK_AT, latest_report_headline: 'BTC 原始摘要', latest_run: saved, active_runs: [], active_count: 0, generated_at: RISK_AT })
  vi.spyOn(riskApi, 'scopes').mockResolvedValue({ schema_version: 'market-risk-scopes.v1', as_of: RISK_AT, items: [riskScopeFixture, eth] })
  const summary = vi.spyOn(riskApi, 'summary').mockResolvedValue({ ...riskSummaryFixture, scope_id: eth.scope_id, quote: { ...riskSummaryFixture.quote!, price: '3000.5' }, monitors: [] })
  vi.spyOn(riskApi, 'timeline').mockImplementation(async (scopeId, start, end) => ({ ...riskTimelineFixture, scope_id: scopeId, start, end, points: [{ at: start, price: '3000.5' }] }))
  const navigate = vi.fn()
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="home" onNavigate={navigate} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !container.textContent?.includes('3,000.5'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.querySelector<HTMLSelectElement>('select[aria-label="观察资产"]')?.value).toBe(eth.scope_id)
    expect(summary).toHaveBeenCalledWith(eth.scope_id, expect.any(AbortSignal))
    expect(container.querySelector('.product-home-report-title')?.textContent).toBe('最近已保存的研究结论')
    const task = container.querySelector('.product-report-task')!
    expect(task.textContent).toBe(`原研究任务：${saved.event_title}`)
    expect(task.closest('details')).toBeNull()
    expect(container.querySelector('.product-report-scope')?.textContent).toBe('这是项目最近已保存的研究结论，资产范围以原研究任务为准。')
    expect(saved.event_title).toBe('BTC 现货与宏观风险研究（未来 24 小时）')
    await act(async () => Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('阅读结论与依据'))!.click())
    expect(navigate).toHaveBeenCalledWith('report', { run: 'btc-saved-report', back: '#/home' })
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals() }
})
