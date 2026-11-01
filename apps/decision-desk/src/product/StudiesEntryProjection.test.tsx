// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { StudyView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { StudiesPage } from './StudiesPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const time = '2026-10-04T00:20:22Z'
const base: StudyView = {
  study_id: 'bank-study', title: 'Federal Reserve Board announces approval of application by Fleur Capital Corporation',
  market: 'crypto', instrument: 'BTC-USDT-SWAP', horizon_scope: '30m,24h,72h',
  identity_basis: 'occurrence', status: 'active', created_at: time, updated_at: time,
  latest_report_at: null, entry_projection: null,
}
let container: HTMLDivElement, root: Root, client: QueryClient
async function mount(items: StudyView[]) {
  vi.spyOn(assets, 'studies').mockResolvedValue({ items, total: items.length, offset: 0, limit: 12 })
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container); client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root.render(<QueryClientProvider client={client}><StudiesPage params={new URLSearchParams()} onNavigate={vi.fn()} /></QueryClientProvider>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) })
}
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); container?.remove(); vi.restoreAllMocks() })

describe('study list entry evidence', () => {
  it('shows an ignored event as collected without changing or hiding the original registration', async () => {
    const study: StudyView = { ...base, entry_projection: { state: 'collected_not_researched', research_runs: 0, latest_admission_decision: 'ignored', latest_admission_reason: 'no_high_impact_title_match', display_title: null, display_title_source_run_id: null, display_title_source_observation_id: null } }
    const original = JSON.stringify(study)
    await mount([study])
    const card = container.querySelector('.study-card')!
    expect(card.querySelector('.study-card-meta')?.textContent).toContain('已收集，尚未研究')
    expect(card.querySelector('.study-card-meta')?.textContent).not.toContain('BTC')
    expect(card.textContent).toContain('本次没有发起 AI 研究')
    expect(card.querySelector('details')?.textContent).toContain(base.title)
    expect(card.querySelector('details')?.textContent).toContain('BTC 永续')
    expect(card.querySelector('details')?.textContent).toContain('no_high_impact_title_match')
    expect(JSON.stringify(study)).toBe(original)
  })
  it('keeps past research visible when the latest admission was ignored, without claiming completion', async () => {
    await mount([{ ...base, entry_projection: { state: 'has_research', research_runs: 2, latest_admission_decision: 'ignored', latest_admission_reason: 'no_high_impact_title_match', display_title: null, display_title_source_run_id: null, display_title_source_observation_id: null } }])
    const card = container.querySelector('.study-card')!
    expect(card.querySelector('.study-card-meta')?.textContent).toContain('已登记研究任务')
    expect(card.textContent).toContain('已登记 2 次 AI 研究任务')
    expect(card.textContent).not.toContain('已收集，尚未研究')
    expect(card.textContent).not.toContain('研究已完成')
  })
  it('uses the explicit input projection for the heading and retains the whole original instruction', async () => {
    const title = '分别由宏观与衍生品研究角色核查最新 BTC 永续市场，再综合研究判断。' + '保留证据。'.repeat(30)
    await mount([{ ...base, title, entry_projection: { state: 'has_research', research_runs: 1, latest_admission_decision: null, latest_admission_reason: null, display_title: 'BTC 永续与宏观市场研究', display_title_source_run_id: 'manual-run', display_title_source_observation_id: 'original-input' } }])
    const card = container.querySelector('.study-card')!
    expect(card.querySelector('h3')?.textContent).toBe('BTC 永续与宏观市场研究')
    expect(card.querySelector('details')?.textContent).toContain(title)
    expect(card.querySelector('details')?.textContent).toContain('original-input')
  })
  it('keeps a legacy response without a projection unconfirmed instead of inventing zero research', async () => {
    const { entry_projection: _entry, ...legacy } = base
    await mount([legacy as StudyView])
    const card = container.querySelector('.study-card')!
    expect(card.querySelector('.study-card-meta')?.textContent).toContain('研究准入待确认')
    expect(card.textContent).not.toContain('已登记 0 次')
    expect(card.textContent).not.toContain('已收集，尚未研究')
  })
})

