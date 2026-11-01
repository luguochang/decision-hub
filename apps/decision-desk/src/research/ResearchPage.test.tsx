// @vitest-environment jsdom

import { act } from 'react'
import { webcrypto } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { AssetBackLink } from '../product/assetFormat'
import { DesignStatePage } from '../DesignStatePage'
import type { ComponentProps } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { researchRunComparisonViewSchema, researchRunDetailViewSchema, reviewScheduleViewSchema } from '../api/schemas'
import { researchDisplayText } from '../product/researchLanguage'
import { api, type OriginalInputReceipt } from '../api/client'
import { assets } from '../api/assets'
import { researchQualityApi } from '../api/researchQuality'
import * as savedReportApi from '../api/perpetualSavedReport'
import { settingsApi } from '../api/decisionSettings'
import { delivery, productOwner } from '../api/delivery'
import { revisionFeedbackEvaluationSchema } from '@decision-hub/contracts-ts'
import type { ResearchRunDetailView, ResearchValueEvaluation, ReviewScheduleView } from '../api/types'
import { evidenceTimeLabel, EvidenceDrawer, OriginalInputReadback, ResearchPage, ResearchRunDetail, ResearchRunList, ReportRevisionFeedback, ReportFollowup, researchRouteHref, researchNavigationRoute, graphBaseRoute, evidenceRouteHref } from './ResearchPage'

describe('visible research query polling', () => {
  async function mountedPage(hash: string, status: ResearchRunDetailView['run']['status'] = 'researching') {
    vi.useFakeTimers()
    window.history.replaceState(null, '', `/${hash}`)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const sample = { ...detail, run: { ...detail.run, status } }
    const reads = {
      runs: vi.spyOn(api, 'researchRuns').mockResolvedValue([sample.run]),
      inbox: vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now }),
      calendar: vi.spyOn(api, 'macroEvents').mockResolvedValue([]),
      detail: vi.spyOn(api, 'researchRun').mockResolvedValue(sample),
    }
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(api, 'researchRunScope').mockResolvedValue(null)
    vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
    for (const method of ['researchRunComparison', 'researchObservability', 'researchValueEvaluation', 'researchForecastAudit'] as const) vi.spyOn(api, method).mockRejectedValue(new Error('controlled optional unavailable'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const advance = async (milliseconds: number) => act(async () => { await vi.advanceTimersByTimeAsync(milliseconds) })
    await act(async () => root.render(<QueryClientProvider client={client}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>))
    await advance(20)
    const toggle = async (selector: string, open: boolean) => {
      const panel = container!.querySelector<HTMLDetailsElement>(selector)!
      await act(async () => { panel.open = open; panel.dispatchEvent(new Event('toggle')) })
      await advance(20)
    }
    const cleanup = async () => { await act(async () => root.unmount()); client.clear(); vi.useRealTimers() }
    return { reads, client, advance, toggle, cleanup, sample }
  }

  it('keeps collapsed report queries idle while the selected active Run refreshes', async () => {
    const page = await mountedPage('#/report?run=run-research-1')
    try {
      await page.advance(12_000)
      expect(page.reads.runs).not.toHaveBeenCalled()
      expect(page.reads.inbox).not.toHaveBeenCalled()
      expect(page.reads.calendar).not.toHaveBeenCalled()
      expect(page.reads.detail.mock.calls.length).toBeGreaterThanOrEqual(4)
      expect(page.reads.detail.mock.calls.every(([id]) => id === detail.run.run_id)).toBe(true)
      expect(window.location.hash).toBe('#/report?run=run-research-1')
    } finally { await page.cleanup() }
  })

  it('refreshes the same Run to its failed state without submitting another research and joins an in-flight read', async () => {
    const page = await mountedPage('#/report?run=run-research-1')
    try {
      const commands = vi.spyOn(api, 'researchCommand')
      let finishRead!: (value: ResearchRunDetailView) => void
      page.reads.detail.mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve }))
      const refresh = Array.from(container!.querySelectorAll<HTMLButtonElement>('button')).find(button => button.textContent?.includes('刷新任务状态'))!
      expect(refresh.disabled).toBe(false)
      expect(container!.textContent).toContain('页面最近读取：')
      await act(async () => { refresh.click(); refresh.click() })
      await page.advance(20)
      expect(page.reads.detail).toHaveBeenCalledTimes(2)
      expect(refresh.disabled).toBe(true)
      expect(container!.textContent).toContain('正在核对任务状态…')
      await act(async () => finishRead({ ...page.sample, run: { ...page.sample.run, status: 'failed', artifact_id: null } }))
      await page.advance(20)
      expect(container!.querySelector('.research-detail-status')?.textContent).toContain('失败')
      expect(refresh.disabled).toBe(false)
      expect(window.location.hash).toBe('#/report?run=run-research-1')
      expect(page.reads.detail.mock.calls.map(([id]) => id)).toEqual(['run-research-1', 'run-research-1'])
      expect(commands).not.toHaveBeenCalled()
    } finally { await page.cleanup() }
  })

  it('fetches and polls expanded panels, then stops hidden polling and hidden invalidation fetches', async () => {
    const page = await mountedPage('#/report?run=run-research-1')
    try {
      await page.toggle('details.research-report-context', true)
      expect(page.reads.runs).toHaveBeenCalledTimes(1)
      expect(page.reads.calendar).toHaveBeenCalledTimes(1)
      expect(page.reads.inbox).not.toHaveBeenCalled()
      await page.advance(6_100)
      expect(page.reads.runs.mock.calls.length).toBeGreaterThanOrEqual(3)
      await page.toggle('details.research-report-context', false)
      const hiddenRuns = page.reads.runs.mock.calls.length
      await act(async () => { await page.client.invalidateQueries({ queryKey: ['research-runs'] }) })
      await page.advance(6_000)
      expect(page.reads.runs).toHaveBeenCalledTimes(hiddenRuns)
      await page.toggle('details.research-history-panel:not(.research-report-context)', true)
      expect(page.reads.inbox).toHaveBeenCalledTimes(1)
      expect(page.reads.runs.mock.calls.length).toBeGreaterThan(hiddenRuns)
      await page.advance(5_100)
      expect(page.reads.inbox.mock.calls.length).toBeGreaterThanOrEqual(2)
      await page.toggle('details.research-history-panel:not(.research-report-context)', false)
      const hiddenInbox = page.reads.inbox.mock.calls.length
      const hiddenOverviewRuns = page.reads.runs.mock.calls.length
      await page.advance(6_000)
      expect(page.reads.inbox).toHaveBeenCalledTimes(hiddenInbox)
      expect(page.reads.runs).toHaveBeenCalledTimes(hiddenOverviewRuns)
      expect(page.reads.calendar).toHaveBeenCalledTimes(1)
    } finally { await page.cleanup() }
  })

  it.each(['home', 'event?event=event-not-yet-saved'])('loads the visible %s list and calendar, and disables them on a collapsed report route', async scene => {
    const page = await mountedPage(`#/${scene}`)
    try {
      expect(page.reads.runs).toHaveBeenCalledTimes(1)
      expect(page.reads.calendar).toHaveBeenCalledTimes(1)
      expect(page.reads.inbox).not.toHaveBeenCalled()
      expect(page.reads.detail).toHaveBeenCalledWith(detail.run.run_id)
      await act(async () => { window.history.pushState(null, '', '#/report?run=run-research-1'); window.dispatchEvent(new PopStateEvent('popstate')) })
      await page.advance(6_100)
      expect(page.reads.runs).toHaveBeenCalledTimes(1)
      expect(page.reads.calendar).toHaveBeenCalledTimes(1)
      expect(page.reads.inbox).not.toHaveBeenCalled()
    } finally { await page.cleanup() }
  })

  it('selects an original readable report when the report link has no Run identity', async () => {
    const page = await mountedPage('#/report', 'degraded')
    try {
      expect(page.reads.runs).toHaveBeenCalledTimes(1)
      expect(page.reads.detail).toHaveBeenCalledWith(detail.run.run_id)
      expect(new URLSearchParams(window.location.search).get('run_id')).toBe(detail.run.run_id)
      await page.advance(6_100)
      expect(page.reads.runs).toHaveBeenCalledTimes(1)
      expect(page.reads.inbox).not.toHaveBeenCalled()
      expect(page.reads.calendar).not.toHaveBeenCalled()
    } finally { await page.cleanup() }
  })

  it('keeps the selected detail on a library route without fetching hidden research lists', async () => {
    const page = await mountedPage('#/document-review?run=run-research-1')
    try {
      await page.advance(6_100)
      expect(page.reads.runs).not.toHaveBeenCalled()
      expect(page.reads.inbox).not.toHaveBeenCalled()
      expect(page.reads.calendar).not.toHaveBeenCalled()
      expect(page.reads.detail.mock.calls.length).toBeGreaterThanOrEqual(3)
      expect(page.reads.detail.mock.calls.every(([id]) => id === detail.run.run_id)).toBe(true)
    } finally { await page.cleanup() }
  })

  it('stops terminal detail polling and resumes after a command invalidates the active Run', async () => {
    const page = await mountedPage('#/report?run=run-research-1', 'cancelled')
    try {
      await page.advance(6_000)
      expect(page.reads.detail).toHaveBeenCalledTimes(1)
      page.reads.detail.mockResolvedValue({ ...page.sample, run: { ...page.sample.run, status: 'researching' } })
      await act(async () => { await page.client.invalidateQueries({ queryKey: ['research-run', detail.run.run_id] }) })
      await page.advance(20)
      expect(page.reads.detail).toHaveBeenCalledTimes(2)
      await page.advance(6_100)
      expect(page.reads.detail.mock.calls.length).toBeGreaterThanOrEqual(4)
      expect(page.reads.runs).not.toHaveBeenCalled()
      expect(page.reads.inbox).not.toHaveBeenCalled()
      expect(page.reads.calendar).not.toHaveBeenCalled()
    } finally { await page.cleanup() }
  })
})

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const now = '2026-08-29T12:00:00Z'
const budget = { max_evidence_rounds: 3, max_tool_calls: 12, max_subagents: 6, total_deadline_seconds: 180, per_tool_timeout_seconds: 20, per_model_step_timeout_seconds: 60, max_structured_repairs: 1, max_estimated_cost_usd: 1 }
const coverage = { status: 'insufficient' as const, covered_requirement_ids: ['event_identity'], gaps: [{ requirement_id: 'market_transmission', importance: 'hard' as const, reason_code: 'missing' as const, query_hint: 'Find DXY and Treasury response', attempted_capabilities: ['market.cross_asset'], blocks_directional_output: true }, { requirement_id: 'market_narrative', importance: 'soft' as const, reason_code: 'missing' as const, query_hint: 'Find and fetch one current official macro source', attempted_capabilities: [], blocks_directional_output: false }], conflicts: [], hard_coverage_ratio: 0.5, soft_coverage_ratio: 0, assessed_at: now }
const link = (id: string, statement: string) => ({ link_id: id, claim_type: 'inference' as const, statement, evidence_refs: ['ev-1'], confirmation: `${id} confirmation`, invalidation: `${id} invalidation`, affected_horizons: ['30m' as const, '24h' as const] })
const processKinds = ['intake', 'plan', 'acquire', 'assess', 'review', 'replan', 'synthesize', 'gate'] as const
const processSteps = processKinds.map((kind, index) => ({
  step_id: `step-${kind}`, order: index + 1, kind, label: `步骤 ${index + 1}`,
  status: kind === 'gate' ? 'limited' as const : 'completed' as const,
  objective: `执行 ${kind}`, outcome: kind === 'gate' ? '证据不足，结果仅供研究。' : `${kind} 已完成。`,
  evidence_refs: kind === 'acquire' ? ['ev-1'] : [], tool_call_refs: kind === 'acquire' ? ['call-1'] : [],
  warning_codes: kind === 'gate' ? ['trade_not_authorized'] : [], occurred_at: now,
}))

const detail = researchRunDetailViewSchema.parse({
  schema_version: 'research-run-detail-view.v1',
  run: { schema_version: 'research-run-view.v2', run_id: 'run-research-1', event_id: 'event-1', event_title: 'Federal Reserve speech changes the inflation risk balance', admission_origin: 'manual', priority: 'high', status: 'degraded', stage: 'done', runtime_id: 'dsh', profile_ref: 'crypto_macro.manager.v1', current_round: 2, budget, coverage, current_action: 'Stopped at the bounded evidence limit.', stop_reason: { code: 'critical_data_unavailable', detail: 'Market transmission remains unverified.', bounded: true, remaining_hard_gaps: ['market_transmission'] }, failure: { error_code: 'research_capability_timeout', origin: 'transport', cause_code: 'deadline', capability_id: 'market.cross_asset', tool_call_id: 'call-1', retryable: true, deadline_ms: 20000 }, latest_sequence_no: 4, artifact_id: 'art-1', available_at: null, parent_run_id: null, created_at: now, updated_at: now },
  trigger_snapshot: null,
  decision_snapshot: null,
  evidence: [],
  rounds: [{ round: 1, plan: { plan_id: 'plan-1', objective: 'Verify policy delta and market transmission', tasks: [{ task_id: 'task-1', requirement_id: 'market_transmission', capability_id: 'market.cross_asset', objective: 'Measure transmission', question: 'Did DXY and yields confirm the policy shock?', input_evidence_refs: ['ev-1'], output_schema_ref: 'evidence-candidate.v1', depends_on: [], success_condition: 'Two fresh market observations', priority: 1 }], required_capabilities: ['market.cross_asset'], created_at: now }, tool_invocations: [{ tool_call_id: 'call-1', capability_id: 'market.cross_asset', tool_name: 'market.cross_asset', query_summary: 'Measure rates and USD transmission', started_at: now, finished_at: '2026-08-29T12:00:01Z', status: 'failed', attempt: 1, latency_ms: 1000, cost_usd: null, error_code: 'research_capability_timeout', error: { error_code: 'research_capability_timeout', origin: 'transport', cause_code: 'deadline', capability_id: 'market.cross_asset', tool_call_id: 'call-1', retryable: true, deadline_ms: 20000 } }], tool_results: [], new_evidence_refs: [], coverage, started_at: now, finished_at: '2026-08-29T12:00:01Z' }],
  causal_case: { case_id: 'case-1', thesis: 'The speech is hawkish, but transmission is not yet confirmed.', main_chain: [link('main-1', 'Policy language raises the expected rate path.')], opposite_chain: [link('counter-1', 'The message may already be priced in.')], unresolved_questions: ['Will yields hold the move?'], evidence_refs: ['ev-1'] },
  horizons: [
    { horizon: '30m', action: 'no_trade', subjective_probability: 0.52, probability_status: 'uncalibrated', evidence_refs: ['ev-1'], trigger: '30m trigger', invalidation: '30m invalidation', expires_at: '2026-08-29T12:30:00Z', next_review_at: '2026-08-29T12:10:00Z', missing_facts: ['DXY'], confidence_cap_reason: 'Cross-asset confirmation missing' },
    { horizon: '24h', action: 'short', subjective_probability: 0.58, probability_status: 'uncalibrated', evidence_refs: ['ev-1'], trigger: '24h trigger', invalidation: '24h invalidation', expires_at: '2026-08-30T12:00:00Z', next_review_at: '2026-08-29T18:00:00Z', missing_facts: [], confidence_cap_reason: null },
    { horizon: '72h', action: 'neutral', subjective_probability: 0.5, probability_status: 'uncalibrated', evidence_refs: ['ev-1'], trigger: '72h trigger', invalidation: '72h invalidation', expires_at: '2026-09-01T12:00:00Z', next_review_at: '2026-08-30T12:00:00Z', missing_facts: ['PCE'], confidence_cap_reason: 'Macro release pending' },
  ],
  action_summary: {
    schema_version: 'research-action-summary.v1', recommendation: 'manual_review_required',
    headline: '7日价格规则倾向：偏多；交易权限未授权', rationale: '已形成未来7日的日频价格规则观点，但市场传导仍未确认，方向概率也未校准。',
    reliability: 'limited', trade_authorized: false, artifact_status: 'limited', data_status: 'insufficient',
    accepted_evidence_count: 0, stale_evidence_count: 0, rejected_evidence_count: 0,
    blocking_gap_ids: ['market_transmission'], warning_codes: ['probability_uncalibrated', 'directional_value_unvalidated'],
    supporting_evidence_refs: [], next_review_at: '2026-08-29T18:00:00Z',
    research_outlook: {
      schema_version: 'research-outlook.v1', horizon: '7d', outlook: 'bullish', basis: 'rule_based',
      action_candidate: '等待回撤确认后参与', rationale: '日线趋势同向。', invalidation: '日线收盘跌破 SMA60',
      trigger: '日线继续站上 SMA20', counter_case: '跌破 SMA60 后偏多失效',
      evidence_refs: ['ev-1'], missing_impact: ['实时 funding 不可用'], probability: null,
      probability_status: 'unavailable', strategy_status: 'baseline_only', execution_permission: false,
      strategy_receipt: {
        schema_version: 'strategy-receipt.v1', strategy_version: 'daily-trend-v0', data_hash: 'a'.repeat(64),
        bar_count: 80, as_of: '2026-08-29', parameters: { short_window: 20, long_window: 60 },
        features: { close: 100, sma_short: 99, sma_long: 95 }, outlook: 'bullish',
        action_candidate: '等待回撤确认后参与', invalidation: '日线收盘跌破 SMA60', rationale: '日线趋势同向。',
        trigger: '日线继续站上 SMA20', counter_case: '跌破 SMA60 后偏多失效',
        evidence_refs: ['ev-1'], probability: null, probability_status: 'unavailable',
      },
    },
    market_view: {
      schema_version: 'market-view.v1', horizon: '7d', direction: 'bullish', basis: 'rule_based',
      rationale: '日线趋势同向。', evidence_refs: ['ev-1'], probability: null, probability_status: 'unavailable',
      action_candidate: '等待回撤确认后参与', counter_case: '跌破 SMA60 后偏多失效', invalidation: '日线收盘跌破 SMA60', missing_impact: ['实时 funding 不可用'],
    },
    execution_view: {
      schema_version: 'execution-view.v1', status: 'blocked', action_candidate: '等待回撤确认后参与',
      reasons: ['交易权限未授权', '缺少执行级证据：衍生品与拥挤度确认'], blocking_gap_ids: ['derivatives_crowding'], trade_authorized: false,
    },
  },
  process_steps: processSteps,
  trace: [{ schema_version: 'research-trace-event.v1', run_id: 'run-research-1', research_session_id: 'product:run-research-1', sequence_no: 4, event_type: 'session_stopped', occurred_at: now, stage: 'done', summary: 'Market transmission remains unverified.', reference_type: 'stop_reason', reference_id: 'critical_data_unavailable', status: 'degraded', error_code: null }],
  total_tool_calls: 2, total_subagents: 1, total_tokens: 1200, estimated_cost_usd: 0.18, scheduled_recheck_at: '2026-08-29T18:00:00Z',
  reviews: [{ review_id: 'review-1', role: 'independent_reviewer', target_claim: 'The speech is hawkish.', evidence_refs: ['ev-1'], result_hash: '3'.repeat(64), challenge: 'Transmission is not yet confirmed.', resolution: 'bounded', limitations: ['DXY confirmation missing'], created_at: now }],
})

describe('saved report page composition', () => {
  afterEach(() => vi.unstubAllGlobals())
  function sample() {
    const raw = readFileSync('../../artifacts/d2-perpetual-saved-report/btc18-actual-v5-example.json', 'utf8')
    const wire = JSON.parse(raw) as { run_id: string; event_id: string; artifact_id: string; owner_id: string }
    const value: ResearchRunDetailView = { ...detail, causal_case: null, horizons: [], reviews: [],
      action_summary: { ...detail.action_summary, research_outlook: null },
      run: { ...detail.run, run_id: wire.run_id, event_id: wire.event_id, artifact_id: wire.artifact_id, research_profile: 'perpetual_decision_v2' },
      trace: detail.trace.map(row => ({ ...row, run_id: wire.run_id })),
      evidence: [{ evidence_id: 'kept-source', requirement_id: 'market_transmission', kind: 'market', authority: 'exchange',
        source_id: 'retained-source', source_url: 'https://example.com/retained-source', published_at: now, observed_at: now, received_at: now,
        content_hash: 'a'.repeat(64), excerpt: '保留的取证原文', content_ref: null, acquisition_receipt_id: null,
        source_content_hash: null, parser_ref: null, structured_payload_ref: null, tool_call_id: 'call-1',
        research_session_id: 'session', round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null }] }
    vi.stubGlobal('crypto', webcrypto)
    vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: wire.owner_id, csrf_token: 'controlled-session', expires_at: '2099-01-01T00:00:00Z' })
    return { raw, wire, value }
  }
  async function mount(value: ResearchRunDetailView) {
    const reader = vi.spyOn(savedReportApi, 'readSavedPerpetualReport') // Call through the actual strict reader.
    const host = document.createElement('div'); document.body.append(host)
    const root = createRoot(host)
    await act(async () => root.render(<ResearchRunDetail detail={value} commandReason="" setCommandReason={() => undefined}
      commandPending={false} commandError={null} onCommand={vi.fn()} />))
    return { host, reader, close: async () => { await act(async () => root.unmount()); host.remove() } }
  }
  function records(host: HTMLElement) {
    const panel = host.querySelector<HTMLDetailsElement>('[aria-label="取证与过程记录"]')
    expect(panel).not.toBeNull(); expect(panel!.open).toBe(false)
    expect(panel!.textContent).toContain('此区保留原过程统计；独立审核结论以已核对报告为准')
    expect(panel!.querySelector('[aria-label="采纳来源与证据"]')).not.toBeNull()
    return panel!
  }

  it('uses verified actual BTC18 report authority over legacy zero-review summaries while preserving records', async () => {
    const { raw, wire, value } = sample()
    let finish!: (response: Response) => void
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => new Promise<Response>((resolve, reject) => {
      finish = resolve
      init!.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
    }))
    const page = await mount(value)
    try {
      expect(page.host.textContent).toContain('正在核对已保存报告')
      expect(page.host.querySelector('.research-action-summary')).toBeNull()
      records(page.host)
      await act(async () => { finish(new Response(raw)); await page.reader.mock.results[0].value })
      expect(page.host.querySelectorAll('[aria-label="已保存的永续研究报告"]')).toHaveLength(1)
      expect(page.host.querySelector('[aria-label="已核对报告审核状态"]')?.textContent).toContain('独立审核已通过')
      expect(page.host.textContent).toContain('尚未取得金融资格')
      expect(page.host.querySelector('[aria-label="市场方向概率"]')?.textContent).toContain('上涨：38%')
      expect(page.host.querySelector('.research-action-summary')).toBeNull()
      expect(researchQualityApi.read).not.toHaveBeenCalled()
      const process = records(page.host)
      await act(async () => { process.open = true; process.dispatchEvent(new Event('toggle')) })
      expect(process.textContent).toContain('保留的取证原文')
      expect(process.querySelector('a[href="https://example.com/retained-source"]')).not.toBeNull()
      expect(process.textContent).toContain('0 份')
      expect(process.textContent).toContain('调用失败')
      expect(process.querySelector('details.research-report-evidence')).not.toBeNull()
      expect(fetcher).toHaveBeenCalledTimes(1)
      expect(fetcher.mock.calls[0][0]).toBe('/v1/decision-settings/research/runs/' + wire.run_id + '/report')
    } finally { await page.close() }
  })

  it('restores the unchanged legacy summary and records only for exact not-applicable404', async () => {
    const { value } = sample()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'perpetual_report_not_applicable' }), { status: 404 }))
    const page = await mount(value)
    try {
      await act(async () => { await page.reader.mock.results[0].value })
      expect(page.host.querySelector('.research-action-summary')).not.toBeNull()
      expect(page.host.querySelector('[aria-label="取证与过程记录"]')).toBeNull()
      expect(page.host.querySelector('[aria-label="可信度与来源"]')).not.toBeNull()
      expect(vi.mocked(researchQualityApi.read).mock.calls[0][0]).toBe(value.run.run_id)
      expect(page.host.querySelector('[aria-label="已保存的永续研究报告"]')).toBeNull()
    } finally { await page.close() }
  })

  it.each([401, 409])('keeps HTTP%s report errors fail-closed without hiding process evidence', async status => {
    const { value } = sample()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: status === 409 ? 'perpetual_report_original_unavailable' : 'session_required' }), { status }))
    const page = await mount(value)
    try {
      await act(async () => { await page.reader.mock.results[0].value.catch(() => undefined) })
      expect(page.host.querySelector('[role="alert"]')).not.toBeNull()
      expect(page.host.querySelector('.research-action-summary')).toBeNull()
      expect(page.host.querySelector('[aria-label="已保存的永续研究报告"]')).toBeNull()
      expect(page.host.querySelector('[aria-label="已核对报告审核状态"]')).toBeNull()
      expect(researchQualityApi.read).not.toHaveBeenCalled()
      expect(records(page.host).textContent).toContain('保留的取证原文')
    } finally { await page.close() }
  })
})

const evaluation: ResearchValueEvaluation = {
  schema_version: 'research-value-evaluation.v1', evaluation_id: 'value-1', run_id: detail.run.run_id,
  artifact_id: detail.run.artifact_id, evaluation_version: 'v1', mode: 'directional',
  hard_coverage_ratio: 0.8, soft_coverage_ratio: 0.5, accepted_evidence_count: 4,
  rejected_evidence_count: 1, typed_fact_count: 3, baseline_status: 'ready', latency_ms: 1200,
  cost_status: 'known', known_cost_usd: 0.12, citation_traceability: 1, usefulness: 'unlabeled',
  terminal_reason: 'sufficient', evaluated_at: now,
  outcomes: [
    { forecast_id: 'forecast-30m', horizon: '30m', direction: 'no_trade', quality_status: 'unavailable', return_pct: null, net_return_pct: null, brier_score: null, direction_correct: null, mfe_pct: null, mae_pct: null, unavailable_reason: 'direction_not_applicable', observed_at: null },
    { forecast_id: 'forecast-24h', horizon: '24h', direction: 'short', quality_status: 'observed', return_pct: 0.8, net_return_pct: 0.7, brier_score: 0.18, direction_correct: true, mfe_pct: 1.2, mae_pct: -0.3, unavailable_reason: null, observed_at: now },
  ],
}

let container: HTMLDivElement | null = null

const originalReceipt = (runId: string, evidenceId: string, owner = 'owner'): OriginalInputReceipt => ({
  schema_version: 'research-original-input-receipt.v1', owner_id: owner, run_id: runId,
  origin: 'authorized_original_input', public_evidence_accepted: false,
  input: { evidence_id: evidenceId, kind: 'document', authority: 'unverified', source_id: 'user-research-context', source_url: null, published_at: null, observed_at: now, received_at: now, content_hash: 'a'.repeat(64), excerpt: '未核验的用户研究输入。<img src=x onerror=alert(1)>' },
  excerpt_sha256: 'b'.repeat(64), request_hash: 'c'.repeat(64),
  request_receipt: { output_id: 'original-task', role: 'research_task_request', schema_version: 'research-task-request.v2', payload_hash: 'd'.repeat(64), recorded_at: now },
})

describe('authorized original input receipt', () => {
  it('opens the original report input after a fresh evidence-route mount and refresh without POST', async () => {
    const identity = 'original-context'
    const route = `#/evidence?run=${detail.run.run_id}&evidence=${identity}&back=${encodeURIComponent(`#/report?run=${detail.run.run_id}`)}`
    window.history.replaceState(null, '', `/${route}`)
    const fetches = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    vi.spyOn(api, 'researchRun').mockResolvedValue({ ...detail, run: { ...detail.run, status: 'research_only' } })
    vi.spyOn(api, 'researchRunScope').mockResolvedValue(null)
    vi.spyOn(api, 'researchOriginalInput').mockResolvedValue(originalReceipt(detail.run.run_id, identity))
    vi.spyOn(api, 'researchLinkedCommandReceipt').mockResolvedValue(null)
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
    for (const method of ['researchRunComparison', 'researchObservability', 'researchValueEvaluation', 'researchForecastAudit'] as const) vi.spyOn(api, method).mockRejectedValue(new Error('controlled optional unavailable'))
    for (let mount = 0; mount < 2; mount++) {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      container = document.createElement('div'); document.body.append(container)
      const root = createRoot(container)
      try {
        await act(async () => { root.render(<QueryClientProvider client={client}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>); await new Promise(resolve => setTimeout(resolve, 30)) })
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
        const drawer = container.querySelector('[aria-label="证据详情"]')!
        expect(drawer.textContent).toContain('研究输入档案')
        expect(drawer.textContent).toContain('授权原输入')
        expect(drawer.textContent).toContain('未核验上下文')
        expect(drawer.textContent).toContain('不属于已采纳的公开市场证据')
        expect(drawer.textContent).toContain('未核验的用户研究输入')
        expect(drawer.textContent).not.toContain('该证据已不在')
        expect(drawer.querySelector('script, img')).toBeNull()
        expect(window.location.hash).toBe(route)
      } finally { await act(async () => root.unmount()); client.clear(); container.remove() }
    }
    expect(api.researchOriginalInput).toHaveBeenCalledTimes(2)
    expect(vi.mocked(api.researchOriginalInput).mock.calls.every(([runId, evidenceId, owner, signal]) => runId === detail.run.run_id && evidenceId === identity && owner === productOwner && signal instanceof AbortSignal)).toBe(true)
    expect(fetches.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true)
    expect(sessionStorage.length).toBe(0)
  })

  it('ignores a late original-input response after Run and owner change', async () => {
    let resolveOld!: (value: OriginalInputReceipt) => void
    const pending = new Promise<OriginalInputReceipt>(resolve => { resolveOld = resolve })
    const reads = vi.spyOn(api, 'researchOriginalInput').mockReturnValueOnce(pending).mockResolvedValueOnce(originalReceipt('new-run', 'new-input', 'new-owner'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    try {
      await act(async () => root.render(<QueryClientProvider client={client}><OriginalInputReadback runId="old-run" evidenceId="old-input" owner="old-owner" /></QueryClientProvider>))
      await act(async () => { root.render(<QueryClientProvider client={client}><OriginalInputReadback runId="new-run" evidenceId="new-input" owner="new-owner" /></QueryClientProvider>); await new Promise(resolve => setTimeout(resolve, 20)) })
      expect(reads.mock.calls[0][3]?.aborted).toBe(true)
      await act(async () => { resolveOld({ ...originalReceipt('old-run', 'old-input', 'old-owner'), input: { ...originalReceipt('old-run', 'old-input', 'old-owner').input, excerpt: 'old private owner content' } }); await new Promise(resolve => setTimeout(resolve, 20)) })
      expect(container.textContent).toContain('new-input')
      expect(container.textContent).not.toContain('old private owner content')
      expect(container.textContent).not.toContain('old-input')
    } finally { await act(async () => root.unmount()); client.clear() }
  })

  it('uses GET with exact owner Run input identity and rejects cross-scope or promoted receipts', async () => {
    const value = originalReceipt('run/scoped', 'input:scoped', 'exact-owner')
    const fetches = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(value), { status: 200 }))
    await expect(api.researchOriginalInput('run/scoped', 'input:scoped', 'exact-owner')).resolves.toEqual(value)
    const [path, init] = fetches.mock.calls[0]
    expect(String(path)).toBe('/v1/research/runs/run%2Fscoped/input-receipts/input%3Ascoped')
    expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('exact-owner')
    expect(init?.method ?? 'GET').toBe('GET')
    fetches.mockResolvedValueOnce(new Response(JSON.stringify({ ...value, owner_id: 'other-owner' }), { status: 200 }))
    await expect(api.researchOriginalInput('run/scoped', 'input:scoped', 'exact-owner')).rejects.toThrow('身份无法核对')
    fetches.mockResolvedValueOnce(new Response(JSON.stringify({ ...value, public_evidence_accepted: true }), { status: 200 }))
    await expect(api.researchOriginalInput('run/scoped', 'input:scoped', 'exact-owner')).rejects.toThrow()
    expect(fetches.mock.calls.every(([, options]) => (options?.method ?? 'GET') === 'GET')).toBe(true)
  })

  it('keeps unreadable owner records distinct from accepted evidence and does not bypass library receipts', async () => {
    const reads = vi.spyOn(api, 'researchOriginalInput').mockRejectedValue(new Error('owner_binding_unavailable'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    try {
      await act(async () => { root.render(<QueryClientProvider client={client}><EvidenceDrawer evidence={null} runId="root-without-owner" originalInputId="context" onClose={vi.fn()} /></QueryClientProvider>); await new Promise(resolve => setTimeout(resolve, 20)) })
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
      expect(container.querySelector('[role="alert"]')?.textContent).toContain('未保存可核对的所有者记录')
      expect(container.textContent).not.toContain('授权原输入')
      const calls = reads.mock.calls.length
      await act(async () => root.render(<QueryClientProvider client={client}><EvidenceDrawer evidence={null} runId="same-run" originalInputId="library:private:part:1" onClose={vi.fn()} /></QueryClientProvider>))
      expect(reads).toHaveBeenCalledTimes(calls)
      expect(container.querySelector('[aria-label="原始研究输入"]')).toBeNull()
    } finally { await act(async () => root.unmount()); client.clear() }
  })
})

beforeEach(() => {
  vi.spyOn(researchQualityApi, 'read').mockImplementation(async runId => ({
    schema_version: 'research-quality-view.v1', run_id: runId, as_of: now,
    status: 'unavailable', reason_code: 'research_quality_legacy_unavailable',
    gate_status: null, published_at: null, publication_ref: null, graph_final_ref: null,
    recipe_selection_ref: null, quality_sidecar_id: null, decision: null, budget: null,
    unknown_receipt_codes: [],
  }))
})

afterEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '/')
  container?.remove()
  container = null
})

describe('saved report native execution failure', () => {
  beforeEach(() => {
    vi.spyOn(assets, 'researchUsage').mockRejectedValue(new Error('controlled unavailable usage'))
    vi.spyOn(assets, 'usageHistory').mockRejectedValue(new Error('controlled unavailable usage history'))
    vi.spyOn(assets, 'usageCollection').mockRejectedValue(new Error('controlled unavailable usage collection'))
  })

  it('shows the confirmed emission timeout directly without claiming a report or exposing raw codes', async () => {
    const forbiddenFetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const sample = researchRunDetailViewSchema.parse({
      ...detail, causal_case: null, horizons: [],
      run: { ...detail.run, status: 'failed', artifact_id: null, failure: null, stop_reason: null },
      native_execution_failure: {
        schema_version: 'research-native-execution-failure.v1', run_id: detail.run.run_id,
        dsh_session_id: 'dsh-emission-timeout', generation: 1,
        error_code: 'dsh_provider_emission_transport_timeout', terminal_at: now,
      },
    })
    const before = JSON.stringify(sample)
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })
      const failure = container.querySelector('[aria-label="AI 研究执行失败"]')!
      expect(failure.querySelector('p')?.textContent).toBe('模型输出回传确认超时，本次研究已停止。已保留证据和失败记录。')
      expect(failure.textContent).toContain('未生成本次报告。')
      expect(failure.querySelector('details')?.open).toBe(false)
      expect([...failure.querySelectorAll('p')].map(p => p.textContent).join('')).not.toContain('dsh_provider_')
      expect(failure.querySelector('time')?.getAttribute('datetime')).toBe(now)
      expect(forbiddenFetch).not.toHaveBeenCalled()
      expect(JSON.stringify(sample)).toBe(before)
    } finally { await act(async () => { root.unmount() }) }
  })

  it('explains the original native AI failure and time while keeping evidence, daily reference and audited recheck', async () => {
    const forbiddenFetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const onCommand = vi.fn()
    const nativeFailure = {
      schema_version: 'research-native-execution-failure.v1', run_id: detail.run.run_id,
      dsh_session_id: 'dsh-original-session', generation: 1,
      error_code: 'dsh_model_binding_unavailable', terminal_at: '2026-10-05T12:04:45.674Z',
    }
    const sample = researchRunDetailViewSchema.parse({
      ...detail, native_execution_failure: nativeFailure, causal_case: null, horizons: [],
      run: { ...detail.run, status: 'research_only', failure: null, updated_at: '2026-10-05T12:08:55Z',
        stop_reason: { code: 'synthesis_incomplete', detail: 'No causal case was produced.', bounded: true, remaining_hard_gaps: [] } },
      action_summary: { ...detail.action_summary, accepted_evidence_count: 6 },
    })
    const original = JSON.stringify(sample)
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={onCommand} />) })
      const summary = container.querySelector('[aria-label="研究判断摘要"]')!
      const failure = summary.querySelector('[aria-label="AI 研究执行失败"]')!
      expect(summary.querySelector('h2')?.textContent).toBe('本次 AI 研究失败')
      expect(failure.textContent).toContain('AI 研究所需的模型调用未能建立，研究执行已结束。')
      expect(failure.textContent).toContain('本次综合 AI 结果未完成。已采纳证据和独立日线参考仍保留。')
      expect(failure.querySelector('time')?.getAttribute('datetime')).toBe(nativeFailure.terminal_at)
      expect(failure.querySelector('details')?.open).toBe(false)
      expect([...failure.querySelectorAll('p')].map(p => p.textContent).join('')).not.toContain(nativeFailure.error_code)
      expect(failure.querySelector('details')?.textContent).toContain(nativeFailure.run_id)
      expect(failure.querySelector('details')?.textContent).toContain(nativeFailure.dsh_session_id)
      expect(failure.querySelector('details')?.textContent).toContain(nativeFailure.error_code)
      expect(summary.querySelector('[aria-label="本次综合研究"]')).toBeNull()
      expect(summary.textContent).toContain('已保留 6 条可用证据')
      expect(summary.querySelector('[aria-label="日线规则参考"]')).not.toBeNull()
      const recheck = container.querySelector<HTMLButtonElement>('[aria-label="更新研究"] .button.primary')!
      expect(recheck.textContent).toContain('更新公开证据')
      await act(async () => { recheck.click() })
      expect(onCommand).toHaveBeenCalledTimes(1)
      expect(onCommand).toHaveBeenCalledWith('recheck', '用户请求更新公开市场证据并复查当前研究；不继承历史资料授权。')
      expect(forbiddenFetch).not.toHaveBeenCalled()
      expect(JSON.stringify(sample)).toBe(original)
    } finally { await act(async () => { root.unmount() }) }
  })


  it.each([
    ['dsh_model_binding_unavailable', '模型调用未能建立'],
    ['dsh_model_binding_writer_busy', '取得数据库写锁时暂时繁忙'],
  ])('keeps an original native failure readable without claiming an unknown cause or a saved report: %s', async (code, message) => {
    const sample = researchRunDetailViewSchema.parse({
      ...detail, causal_case: null, horizons: [],
      run: { ...detail.run, status: 'failed', failure: null, artifact_id: null, stop_reason: null },
      native_execution_failure: { schema_version: 'research-native-execution-failure.v1', run_id: detail.run.run_id,
        dsh_session_id: 'dsh-no-report', generation: 1, error_code: code, terminal_at: now },
    })
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })
      const summary = container.querySelector('[aria-label="研究判断摘要"]')!
      const failure = summary.querySelector('[aria-label="AI 研究执行失败"]')!
      expect(failure.classList.contains('research-native-failure')).toBe(true)
      expect(failure.textContent).toContain('未生成本次报告。')
      expect(failure.textContent).toContain(message)
      expect(failure.textContent).toContain('原任务的执行记录保留供对照')
      expect(summary.textContent).not.toContain('具体原因尚无可读说明')
      expect(summary.textContent).not.toContain('请结合已保存的结论')
      expect(summary.querySelector('button')?.textContent).toBe('查看问题详情')
      expect(container.textContent).toContain('重新尝试研究')
    } finally { await act(async () => { root.unmount() }) }
  })

  it('keeps incomplete synthesis explicit for a legacy DTO without inventing a native cause or time', async () => {
    const { native_execution_failure: _omitted, ...legacy } = detail
    const onCommand = vi.fn()
    const sample = researchRunDetailViewSchema.parse({
      ...legacy, causal_case: null, horizons: [],
      run: { ...detail.run, status: 'research_only', failure: null,
        stop_reason: { code: 'synthesis_incomplete', detail: 'No causal case was produced.', bounded: true, remaining_hard_gaps: [] } },
    })
    expect(sample.native_execution_failure).toBeNull()
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={onCommand} />) })
      const summary = container.querySelector('[aria-label="研究判断摘要"]')!
      expect(summary.querySelector('h2')?.textContent).toBe('本次 AI 综合判断未完成')
      expect(summary.querySelector('[aria-label="AI 研究执行失败"]')).toBeNull()
      expect(summary.querySelector('[aria-label="AI 综合判断未完成"]')?.textContent).toContain('不能作为完整的 AI 研究结论')
      expect(summary.textContent).not.toContain('模型调用未能建立')
      const records = summary.querySelector<HTMLButtonElement>('[aria-label="AI 综合判断未完成"] button')!
      await act(async () => { records.click() })
      expect(container.textContent).toContain('材料充分性')
      expect(onCommand).not.toHaveBeenCalled()
    } finally { await act(async () => { root.unmount() }) }
  })

  it('removes a previous failure banner after a fresh completed DTO and never infers failure from tool activity', async () => {
    const props = { commandReason: '', setCommandReason: () => undefined, commandPending: false, commandError: null, onCommand: vi.fn() }
    const failed = researchRunDetailViewSchema.parse({ ...detail,
      native_execution_failure: { schema_version: 'research-native-execution-failure.v1', run_id: detail.run.run_id,
        dsh_session_id: 'dsh-before-repair', generation: 1, error_code: 'unknown_native_failure', terminal_at: now },
    })
    const completed = researchRunDetailViewSchema.parse({ ...detail,
      run: { ...detail.run, status: 'completed', failure: null }, native_execution_failure: null,
    })
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={failed} {...props} />) })
      expect(container.querySelector('[aria-label="AI 研究执行失败"]')?.textContent).toContain('AI 研究执行中断，后台已保留失败记录。')
      await act(async () => { root.render(<ResearchRunDetail detail={completed} {...props} />) })
      const summary = container.querySelector('[aria-label="研究判断摘要"]')!
      expect(summary.querySelector('h2')?.textContent).toBe('本次 AI 综合判断')
      expect(summary.querySelector('[aria-label="AI 研究执行失败"]')).toBeNull()
      expect(summary.querySelector('[aria-label="AI 综合判断未完成"]')).toBeNull()
      expect(summary.querySelector('[aria-label="本次综合研究"]')).not.toBeNull()
      expect(completed.rounds[0].tool_invocations[0].status).toBe('failed')
    } finally { await act(async () => { root.unmount() }) }
  })
})

describe('independent daily reference with limited AI synthesis', () => {
  beforeEach(() => {
    vi.spyOn(assets, 'researchUsage').mockRejectedValue(new Error('controlled unavailable usage'))
    vi.spyOn(assets, 'usageHistory').mockRejectedValue(new Error('controlled unavailable usage history'))
    vi.spyOn(assets, 'usageCollection').mockRejectedValue(new Error('controlled unavailable usage collection'))
  })

  it('retains the saved daily reference and exact results return when AI output is unavailable', async () => {
    const report = '#/report?run=run-research-1&back=%23%2Fhome'
    window.history.replaceState(null, '', `/${report}`)
    const forbiddenFetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const onCommand = vi.fn()
    const daily = detail.action_summary.research_outlook!
    const sample = researchRunDetailViewSchema.parse({
      ...detail,
      run: { ...detail.run, failure: { ...detail.run.failure!, error_code: 'structured_output_invalid' } },
      causal_case: null,
      horizons: [],
      evidence: Array.from({ length: 10 }, (_, index) => ({
        evidence_id: `ev-${index + 1}`, requirement_id: 'event_identity', kind: 'official', authority: 'official',
        source_id: 'federal-reserve', source_url: 'https://www.federalreserve.gov/example', published_at: now,
        observed_at: now, received_at: now, content_hash: String(index).repeat(64), excerpt: `Retained official material ${index + 1}.`,
        structured_payload_ref: null, tool_call_id: 'call-1', research_session_id: 'dsh-session-1',
        round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
      })),
      action_summary: {
        ...detail.action_summary, recommendation: 'unavailable', reliability: 'unavailable',
        accepted_evidence_count: 10, stale_evidence_count: 0, rejected_evidence_count: 0,
        supporting_evidence_refs: [], research_outlook: { ...daily, strategy_receipt: { ...daily.strategy_receipt!, bar_count: 90 } },
      },
    })
    const original = JSON.stringify(sample)
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={onCommand} />) })
      const summary = container.querySelector('[aria-label="研究判断摘要"]')!
      expect(summary.querySelector('h2')?.textContent).toBe('已有日线参考，综合判断暂不充分')
      expect(summary.querySelector('[aria-label="本次综合研究"]')).toBeNull()
      expect(summary.querySelectorAll('.research-window-summary article')).toHaveLength(0)
      expect(summary.textContent).toContain('未授权交易')
      expect(summary.textContent).toContain('已保留 10 条可用证据；综合判断摘要尚未提供引用。')
      expect(summary.textContent).not.toContain('暂未保留足够的可引用材料')
      expect(summary.textContent).toContain('10 可用 / 0 过期 / 0 未采纳')
      const reference = summary.querySelector<HTMLDetailsElement>('[aria-label="日线规则参考"]')!
      expect(reference.querySelector('summary')?.textContent).toContain('独立日线参考 · 7 天 · 偏多（非综合判断）')
      await act(async () => { reference.querySelector('summary')!.click() })
      expect(reference.open).toBe(true)
      expect(reference.textContent).toContain(daily.rationale)
      expect(reference.textContent).toContain('90 根日线；规则概率未校准')
      expect(reference.textContent).toContain(daily.invalidation)
      expect(reference.querySelector('a[title="ev-1"]')?.getAttribute('href')).toContain('run=run-research-1&evidence=ev-1')
      const destination = reference.querySelector<HTMLAnchorElement>('a[href^="#/results"]')!
      const params = new URLSearchParams(destination.hash.split('?')[1])
      expect(params.get('method')).toBe('daily_rule')
      expect(params.get('horizon')).toBe('7d')
      expect(params.get('run')).toBe(sample.run.run_id)
      expect(params.get('back')).toBe(report)
      expect(forbiddenFetch).not.toHaveBeenCalled()
      expect(onCommand).not.toHaveBeenCalled()
      expect(JSON.stringify(sample)).toBe(original)
      expect(sample.horizons).toEqual([])
      expect(sample.run.failure?.error_code).toBe('structured_output_invalid')
      expect(sample.action_summary.trade_authorized).toBe(false)
    } finally { await act(async () => { root.unmount() }) }
  })

  it('keeps zero accepted evidence and an absent daily record explicit without inventing a direction', async () => {
    const forbiddenFetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const onCommand = vi.fn()
    const sample = researchRunDetailViewSchema.parse({
      ...detail, evidence: [], causal_case: null, horizons: [],
      action_summary: {
        ...detail.action_summary, recommendation: 'unavailable', research_outlook: null, market_view: null,
        accepted_evidence_count: 0, stale_evidence_count: 3, rejected_evidence_count: 2, supporting_evidence_refs: [],
      },
    })
    const original = JSON.stringify(sample)
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={onCommand} />) })
      const summary = container.querySelector('[aria-label="研究判断摘要"]')!
      expect(summary.querySelector('h2')?.textContent).toBe('当前尚不能形成可靠判断')
      expect(summary.textContent).toContain('尚未保留可用证据，也未形成综合判断摘要引用。')
      expect(summary.textContent).toContain('0 可用 / 3 过期 / 2 未采纳')
      expect(summary.textContent).toContain('未授权交易')
      expect(summary.querySelector('[aria-label="日线规则参考"]')).toBeNull()
      expect(summary.querySelector('[aria-label="本次综合研究"]')).toBeNull()
      expect(summary.querySelector('a[title]')).toBeNull()
      const trust = container.querySelector('[aria-label="可信度与来源"]')!
      expect(trust.textContent).toContain('尚未采纳证据')
      expect(trust.textContent).not.toContain('证据来源可追溯')
      expect(trust.textContent).not.toContain('可查看采纳证据的来源记录')
      expect(forbiddenFetch).not.toHaveBeenCalled()
      expect(onCommand).not.toHaveBeenCalled()
      expect(JSON.stringify(sample)).toBe(original)
    } finally { await act(async () => { root.unmount() }) }
  })

  it('retains the rejected publication boundary and real summary citations without substituting the daily direction', async () => {
    const forbiddenFetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const onCommand = vi.fn()
    const sample = researchRunDetailViewSchema.parse({
      ...detail, causal_case: null, horizons: [],
      action_summary: {
        ...detail.action_summary, recommendation: 'unavailable', artifact_status: 'rejected',
        accepted_evidence_count: 10, supporting_evidence_refs: ['ev-1'],
      },
    })
    const original = JSON.stringify(sample)
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => { root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={onCommand} />) })
      const summary = container.querySelector('[aria-label="研究判断摘要"]')!
      expect(summary.querySelector('h2')?.textContent).toBe('当前尚不能形成可靠判断')
      expect(summary.querySelector('[aria-label="日线规则参考"]')).toBeNull()
      expect(summary.querySelector('[aria-label="本次综合研究"]')).toBeNull()
      expect(summary.querySelector('.research-action-gaps a[title="ev-1"]')?.getAttribute('href')).toBe('#/evidence?run=run-research-1&evidence=ev-1')
      expect(summary.querySelector('.research-action-gaps')?.textContent).not.toContain('尚未提供引用')
      expect(summary.querySelector('a[href^="#/results"]')).toBeNull()
      expect(summary.textContent).toContain('报告被拒绝')
      expect(summary.textContent).toContain('未授权交易')
      expect(forbiddenFetch).not.toHaveBeenCalled()
      expect(onCommand).not.toHaveBeenCalled()
      expect(JSON.stringify(sample)).toBe(original)
    } finally { await act(async () => { root.unmount() }) }
  })
})

describe('Research Command Center detail', () => {
  it('keeps orphan graph reviews conservative without claiming a different typed review or losing evidence routes', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const review = detail.reviews[0], rawNode = { node_id: 'review-1', node_type: 'review' as const, label: '独立复核 1', status: 'unresolved' as const, summary: `结论=bounded；挑战=A different original English review；限制=market_narrative`, evidence_refs: ['ev-1'] }
    const sample = { ...detail, decision_graph: { schema_version: 'decision-graph.v1' as const, graph_status: 'causal' as const, generation_mode: 'causal_case' as const, stop_reason: null, nodes: [rawNode], edges: [] } }
    window.history.replaceState(null, '', researchRouteHref({ name: 'graph-node', runId: detail.run.run_id, nodeId: rawNode.node_id, graphView: 'list', back: '#/report?run=run-research-1' }))
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ResearchRunDetail detail={sample} initialDetailView="technical" graphRouteOpen graphNodeId={rawNode.node_id} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />))
      const dialog = container.querySelector('[aria-label="全屏分析链路"]')!, inspector = dialog.querySelector('.graph-node-inspector')!
      expect(inspector.querySelector(':scope > p')?.textContent).toContain('尚未匹配')
      expect(inspector.querySelector(':scope > p')?.textContent).not.toContain('market_narrative')
      expect([...inspector.querySelectorAll('summary')].map(item => item.textContent)).not.toContain('完整复核原始记录')
      const raw = inspector.querySelector('details')!
      expect(raw.open).toBe(false)
      expect(raw.textContent).toContain(rawNode.summary)
      expect(raw.textContent).not.toContain(review.challenge)
      await act(async () => raw.querySelector<HTMLElement>('summary')!.click())
      expect(raw.open).toBe(true)
      expect(JSON.parse(raw.querySelector('pre')!.textContent!)).toEqual(rawNode)
      const ref = inspector.querySelector<HTMLAnchorElement>('.graph-node-evidence-list a')!
      expect(new URLSearchParams(ref.hash.split('?')[1]).get('run')).toBe(detail.run.run_id)
      expect(new URLSearchParams(ref.hash.split('?')[1]).get('back')).toBe(window.location.hash)
    } finally { await act(async () => root.unmount()) }
  })
  it('keeps typed graph reviews field-scoped through fullscreen list and inspector with complete closed originals', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(() => {
      const rect = new DOMRect(0, 0, 10, 10)
      return Object.assign([rect], { item: (index: number) => index === 0 ? rect : null })
    })
    const review = { ...detail.reviews[0], challenge: 'The original English review keeps the outcome bounded and does not authorize execution.', limitations: ['market_narrative', 'market_regime_context', 'custom_limit_91'] }
    const node = { node_id: 'review-1', node_type: 'review' as const, label: '独立复核 1', status: 'unresolved' as const, summary: `结论=${review.resolution}；挑战=${review.challenge}；限制=${review.limitations.join('；')}`, evidence_refs: [...review.evidence_refs] }
    const sample = { ...detail, reviews: [review], decision_graph: { schema_version: 'decision-graph.v1' as const, graph_status: 'causal' as const, generation_mode: 'causal_case' as const, stop_reason: null, nodes: [node], edges: [] } }
    const original = JSON.stringify(sample)
    window.history.replaceState(null, '', researchRouteHref({ name: 'graph-node', runId: detail.run.run_id, nodeId: node.node_id, graphQuery: '独立复核', graphView: 'list', back: '#/graph?run=run-research-1' }))
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container), evidence = vi.fn(), close = vi.fn()
    try {
      await act(async () => root.render(<ResearchRunDetail detail={sample} initialDetailView="technical" graphRouteOpen graphNodeId={node.node_id} onOpenEvidence={evidence} onCloseGraphRoute={close} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />))
      const dialog = container.querySelector('[aria-label="全屏分析链路"]')!
      const exit = dialog.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!
      expect(document.activeElement).toBe(exit)
      const summary = dialog.querySelector('.graph-node-inspector > p')!
      expect(summary.textContent).not.toContain(review.challenge)
      expect(summary.textContent).not.toContain('market_narrative')
      expect(summary.textContent).not.toContain('custom_limit_91')
      expect(summary.textContent).toContain('复核意见以英文保存')
      expect(summary.textContent).toContain('最新公开信息')
      expect(summary.textContent).toContain('日线趋势背景')
      expect(summary.textContent).toContain('另 1 项限制')
      expect(dialog.querySelector('.graph-node-list > button p')?.textContent).toBe(summary.textContent)
      const audit = [...dialog.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === '完整复核原始记录')!
      expect(audit.open).toBe(false)
      await act(async () => audit.querySelector<HTMLElement>('summary')!.click())
      expect(audit.open).toBe(true)
      expect(JSON.parse(audit.querySelector('pre')!.textContent!)).toEqual(review)
      const nodeAudit = [...dialog.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === '原始节点记录')!
      expect(nodeAudit.open).toBe(false)
      expect(nodeAudit.textContent).toContain(node.summary)
      await act(async () => nodeAudit.querySelector<HTMLElement>('summary')!.click())
      expect(nodeAudit.open).toBe(true)
      expect(JSON.parse(nodeAudit.querySelector('pre')!.textContent!)).toEqual(node)
      const ref = dialog.querySelector<HTMLAnchorElement>('.graph-node-evidence-list a')!
      expect(new URLSearchParams(ref.hash.split('?')[1]).get('run')).toBe(detail.run.run_id)
      expect(new URLSearchParams(ref.hash.split('?')[1]).get('back')).toBe(window.location.hash)
      ref.focus()
      const forwardTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
      ref.dispatchEvent(forwardTab)
      expect(forwardTab.defaultPrevented).toBe(true)
      expect(document.activeElement).toBe(exit)
      const reverseTab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
      exit.dispatchEvent(reverseTab)
      expect(reverseTab.defaultPrevented).toBe(true)
      expect(document.activeElement).toBe(ref)
      const nativeSummary = nodeAudit.querySelector<HTMLElement>('summary')!
      nativeSummary.focus()
      expect(document.activeElement).toBe(nativeSummary)
      await act(async () => ref.click()); expect(evidence).toHaveBeenCalledWith('ev-1')
      await act(async () => dialog.querySelector<HTMLButtonElement>('[aria-label="退出全屏"]')!.click())
      expect(close).toHaveBeenCalledWith({ graphQuery: '独立复核', graphFilter: 'all', graphView: 'list' })
      expect(container.querySelector('.decision-graph-panel .graph-node-inspector > p')?.textContent).toBe(summary.textContent)
      expect(JSON.stringify(sample)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
  it('retains a top-level tool error without provenance and distinguishes tiny saved costs from zero', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const call = { ...detail.rounds[0].tool_invocations[0], error: null, error_code: 'new-top-level-error', cost_usd: 0.0000000123 }
    const saved = { ...detail, rounds: [{ ...detail.rounds[0], tool_invocations: [call] }] }
    await act(async () => root.render(<ResearchRunDetail detail={saved} initialDetailView="technical" commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />))
    const activity = container.querySelector('.tool-activity')!
    expect(activity.querySelector('.tool-error')?.textContent).toContain('尚无中文说明，见原始记录')
    expect(activity.querySelector('.tool-error')?.textContent).toContain('重试许可未记录')
    expect(activity.textContent).toContain('$1.2300e-8')
    expect(activity.querySelector('p')?.textContent).not.toContain('$0.0000')
    const raw = activity.querySelector<HTMLDetailsElement>('.research-original-record')!
    expect(raw.open).toBe(false)
    expect(JSON.parse(raw.querySelector('pre')!.textContent!)).toEqual(call)
    await act(async () => root.unmount())
  })
  it('keeps unknown technical causes in closed original records and never calls a terminal run running', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const saved: ResearchRunDetailView = {
      ...detail,
      run: { ...detail.run, status: 'failed', stop_reason: null, failure: { ...detail.run.failure!, error_code: 'research_capability_mode_denied', cause_code: 'new-mode-cause', capability_id: 'new-capability' } },
      rounds: detail.rounds.map(round => ({ ...round, plan: { ...round.plan, tasks: round.plan.tasks.map(task => ({ ...task, requirement_id: 'new-requirement', capability_id: 'new-capability' })) }, tool_invocations: round.tool_invocations.map(tool => ({ ...tool, capability_id: 'new-capability', error: { ...tool.error!, error_code: 'new-tool-error' } })) })),
      trace: detail.trace.map(event => ({ ...event, error_code: 'new-trace-error', reference_type: 'new-reference-type' })),
    }
    await act(async () => root.render(<ResearchRunDetail detail={saved} initialDetailView="technical" commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />))
    const failure = container.querySelector('.research-failure')!
    expect(failure.querySelector('.failure-grid')?.textContent).toContain('取证工具不支持本次执行模式')
    expect(failure.querySelector('.failure-grid')?.textContent).toContain('尚无中文说明，见原始记录')
    expect(failure.querySelector('.failure-grid')?.textContent).not.toContain('new-mode-cause')
    expect(container.querySelector('.stop-reason strong')?.textContent).toBe('结束原因未记录')
    expect(container.querySelector('.task-list strong')?.textContent).toBe('其他材料要求')
    expect(container.querySelector('.tool-error')?.textContent).toContain('尚无中文说明，见原始记录')
    const trace = container.querySelector('.research-trace')!
    expect(trace.querySelector('small')?.textContent).toContain('尚无中文说明，见原始记录')
    expect(trace.querySelector('small')?.textContent).not.toContain('new-reference-type')
    for (const code of ['new-mode-cause', 'new-capability', 'new-requirement', 'new-tool-error', 'new-trace-error', 'new-reference-type']) {
      const record = [...container.querySelectorAll<HTMLDetailsElement>('details')].find(element => element.textContent?.includes(code) && element.querySelector('summary')?.textContent === '查看原始记录')
      expect(record, code).toBeDefined()
      expect(record?.open, code).toBe(false)
    }
    await act(async () => root.unmount())
  })
  it('preserves unknown coverage without rendering zero percent or no-gap claims', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const unknown: ResearchRunDetailView = { ...detail, run: { ...detail.run, coverage: null, coverage_basis: 'invalid' as const , budget: null, budget_basis: 'invalid' }, role_packets: [{ schema_version: 'role-packet.v1', created_at: now, transition_ids: [], packet_id: 'old-packet', role: 'manager', status: 'partial', round: 1, conclusion: '原始要求无法核验', claims: [], evidence_refs: ['ev-old'], gap_ids: [] }] }
    await act(async () => root.render(<ResearchRunDetail detail={unknown} initialDetailView="technical" commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />))
    const section = container.querySelector('.sufficiency-section')!
    expect(section.textContent).toContain('未知')
    expect(section.textContent).toContain('缺口数量未知')
    expect(section.textContent).not.toContain('0%')
    expect(section.textContent).not.toContain('没有未关闭')
    const packets = container.querySelector('[aria-label="专家角色证据包"]')!
    expect(packets.textContent).toContain('职责缺口未核验')
    expect(packets.textContent).not.toContain('无职责缺口')
    expect(packets.textContent).not.toContain('部分充分')
    expect(container.querySelector('[aria-label="本次研究预算"]')?.textContent).toContain('原预算未核验')
    expect(container.querySelector('[aria-label="轮次预算使用比例"]')).toBeNull()
    expect(container.querySelector('[aria-label="覆盖评估依据缺失"]')?.textContent).toContain('不能认定缺口为零')
    await act(async () => root.unmount())
  })

  it('updates public evidence from the user report without requiring a typed research question', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const onCommand = vi.fn()
    await act(async () => root.render(<ResearchRunDetail detail={detail} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={onCommand} />))
    const action = [...container.querySelectorAll('button')].find(button => button.textContent === '更新公开证据')!
    expect(action.disabled).toBe(false)
    expect(action.closest('details')).toBeNull()
    expect(container.textContent).toContain('研究过程保留 1 次调用失败')
    await act(async () => action.click())
    expect(onCommand).toHaveBeenCalledWith('recheck', '用户请求更新公开市场证据并复查当前研究；不继承历史资料授权。')
    await act(async () => root.unmount())
  })

  it('identifies a saved template report without pretending it is a live recommendation or changing user titles', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const onCommand = vi.fn()
    const saved = { ...detail, research_focus: '', run: { ...detail.run, event_title: 'BTC 最新操作建议' } }
    const props = { commandReason: '', setCommandReason: () => undefined, commandPending: false, commandError: null, onCommand }
    await act(async () => root.render(<ResearchRunDetail detail={saved} {...props} />))
    expect(container.querySelector('.research-detail-header h2')?.textContent).toBe('BTC 与宏观快照研究')
    const context = container.querySelector('.research-report-context')!
    expect(context.textContent).toContain('本次已保存的研究结论')
    expect(context.textContent).toContain('不会随当前市场自动刷新')
    expect(context.querySelector('time')?.dateTime).toBe(saved.run.updated_at)
    expect(context.textContent).not.toContain('报告生成时间')
    const original = container.querySelector<HTMLDetailsElement>('.research-heading-audit')!
    expect(original.open).toBe(false)
    expect(JSON.parse(original.querySelector('pre')!.textContent!).event_title).toBe('BTC 最新操作建议')
    await act(async () => [...container!.querySelectorAll('button')].find(button => button.textContent === '更新公开证据')!.click())
    expect(onCommand).toHaveBeenCalledWith('recheck', '用户请求更新公开市场证据并复查当前研究；不继承历史资料授权。')
    const custom = { ...saved, research_focus: null, run: { ...saved.run, event_title: 'BTC 最新操作建议 · 用户自定义标题' } }
    await act(async () => root.render(<ResearchRunDetail detail={custom} {...props} />))
    expect(container.querySelector('.research-detail-header h2')?.textContent).toBe(custom.run.event_title)
    expect(JSON.parse(container.querySelector('.research-heading-audit pre')!.textContent!).event_title).toBe(custom.run.event_title)
    await act(async () => root.unmount())
  })

  it('keeps a successful late cancellation call and its actual cost while explaining the retained material is not a new report', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const saved: ResearchRunDetailView = { ...detail, run: { ...detail.run, status: 'cancelled', artifact_id: null, failure: null }, total_tool_calls: 1, estimated_cost_usd: 0.031,
      rounds: detail.rounds.map(round => ({ ...round, tool_invocations: round.tool_invocations.map(tool => ({ ...tool, status: 'succeeded', cost_usd: 0.031, error: null, error_code: null })) })),
      trace: [{ ...detail.trace[0], event_type: 'evidence_accepted', status: 'succeeded', reference_type: 'cancellation_cutoff', reference_id: 'research-command:cancel-original', error_code: null, summary: '取消后收到原调用返回；cancelled_at=2026-08-29T12:00:00Z；cost_usd=0.031' }],
    }
    await act(async () => root.render(<ResearchRunDetail detail={saved} initialDetailView="technical" commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />))
    const trace = container.querySelector('.research-trace')!
    expect(trace.querySelector('strong')?.textContent).toBe('取消后收到的资料已留档，不发布新报告')
    expect(trace.querySelector('small')?.textContent).toContain('成功')
    expect(trace.querySelector('small')?.textContent).toContain('关联取消操作')
    expect(container.querySelector('[aria-label="耗时与成本"]')?.textContent).toContain('$0.0310')
    expect(container.querySelector('.tool-mark.succeeded')).not.toBeNull()
    expect(container.querySelector('.tool-error')).toBeNull()
    expect(container.textContent).not.toContain('次调用失败')
    expect(container.querySelector('.research-report-context')?.textContent).toContain('未生成报告')
    const raw = trace.querySelector<HTMLDetailsElement>('details')!
    expect(raw.open).toBe(false)
    const event = JSON.parse(raw.querySelector('pre')!.textContent!)
    expect(event.reference_id).toBe('research-command:cancel-original')
    expect(event.occurred_at).toBe(now)
    expect(event.status).toBe('succeeded')
    expect(event.summary).toContain('cost_usd=0.031')
    await act(async () => root.unmount())
  })

  it.each(['accepted', 'rejected'] as const)('handles %s rechecks without losing task identity or retrying with a new key', async (status) => {
    window.history.replaceState(null, '', '/#/technical?run=run-research-1')
    vi.spyOn(api, 'researchRuns').mockResolvedValue([detail.run])
    vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
    vi.spyOn(api, 'macroEvents').mockResolvedValue([])
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(api, 'researchRunScope').mockResolvedValue(null)
    vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
    for (const method of ['researchRunComparison', 'researchObservability', 'researchValueEvaluation', 'researchForecastAudit'] as const) vi.spyOn(api, method).mockRejectedValue(new Error('No optional diagnostic'))
    const child = { ...detail, run: { ...detail.run, run_id: 'run-child', parent_run_id: detail.run.run_id, status: 'queued' as const } }
    vi.spyOn(api, 'researchRun').mockImplementation(async id => id === 'run-child' ? child : detail)
    let receipt: Awaited<ReturnType<typeof api.researchCommandReceipt>> = null
    vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => receipt)
    const command = vi.spyOn(api, 'researchCommand').mockImplementation(async (id, payload, owner) => {
      const result = { schema_version: 'research-run-command-result.v1' as const, request_id: payload.request_id, command: payload.command, source_run_id: id, target_run_id: status === 'accepted' ? 'run-child' : null, status, created_at: now }
      receipt = { schema_version: 'research-run-command-receipt.v1', request_id: payload.request_id, owner_id: owner, source_run_id: id, command: payload, result }
      return result
    })
    if (status === 'accepted') command.mockRejectedValueOnce(new Error('Response interrupted'))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<QueryClientProvider client={queryClient}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    const input = container.querySelector<HTMLTextAreaElement>('.owner-command textarea')!
    expect(input).not.toBeNull()
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, '复查公开证据')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => [...container!.querySelectorAll('button')].find(button => button.textContent === '立即复查')!.click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    if (status === 'accepted') {
      expect(container.textContent).toContain('Response interrupted')
      await act(async () => [...container!.querySelectorAll('button')].find(button => button.textContent === '核对并按原操作重试')!.click())
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
      expect(command).toHaveBeenCalledTimes(2)
      expect(command.mock.calls[0][1].request_id).toBe(command.mock.calls[1][1].request_id)
    } else expect(command).toHaveBeenCalledTimes(1)
    const params = new URLSearchParams(window.location.hash.split('?')[1])
    if (status === 'accepted') {
      expect(params.get('run')).toBe('run-child')
      expect(params.get('back')).toBe('#/report?run=run-research-1')
      expect(new URLSearchParams(window.location.search).get('run_id')).toBe('run-child')
      expect(container.textContent).toContain('当前正在查看已确认的后续研究')
      expect([...container.querySelectorAll('button')].some(button => button.textContent === '打开已确认的新研究')).toBe(false)
    } else {
      expect(params.get('run')).toBe('run-research-1')
      expect(input.value).toBe('复查公开证据')
      expect(container.textContent).toContain('服务端拒绝了原操作')
    }
    await act(async () => root.unmount())
    queryClient.clear()
  })

  it('does not guess a Run for an Evidence deep link missing its Run identity', async () => {
    window.history.replaceState(null, '', '/?run_id=stale-run#/evidence?evidence=ev-1')
    vi.spyOn(api, 'researchRuns').mockResolvedValue([detail.run])
    vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
    vi.spyOn(api, 'macroEvents').mockResolvedValue([])
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    const readRun = vi.spyOn(api, 'researchRun')
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>) })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
    expect(container.textContent).toContain('证据链接缺少 Run ID')
    expect(container.querySelector('[aria-label="证据详情"]')).toBeNull()
    expect(readRun).not.toHaveBeenCalled()
    await act(async () => { root.unmount() })
    queryClient.clear()
  })

  it('discloses omitted parent material and permits recheck of a research-only report', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const parentScope = { schema_version: 'research-run-scope.v1' as const, run_id: 'run-parent', research_family: 'crypto_macro' as const, library_context_mode: 'selected' as const, selected_version_ids: ['docver_parent'], allow_remote_library_content: true, context_inputs: [] }
    const props = { commandReason: '核对最新公开证据', setCommandReason: () => undefined, commandPending: false, commandError: null, onCommand: vi.fn(), initialDetailView: 'technical' as const }
    const childDetail = { ...detail, run: { ...detail.run, parent_run_id: 'run-parent', status: 'research_only' as const } }
    await act(async () => root.render(<ResearchRunDetail detail={childDetail} libraryScope={null} parentLibraryScope={parentScope} {...props} />))
    expect(container.textContent).toContain('本次复查未重新使用历史研究选择的 1 份资料')
    expect(container.querySelector('[aria-label="复查资料范围"] a')?.getAttribute('href')).toBe('#/report?run=run-parent')
    const recheck = [...container.querySelectorAll('button')].find(button => button.textContent === '立即复查')!
    expect(recheck.disabled).toBe(false)
    await act(async () => recheck.click())
    expect(props.onCommand).toHaveBeenCalledWith('recheck')
    await act(async () => root.render(<ResearchRunDetail detail={childDetail} libraryScope={{ ...parentScope, run_id: childDetail.run.run_id }} parentLibraryScope={parentScope} {...props} />))
    expect(container.querySelector('[aria-label="复查资料范围"]')).toBeNull()
    const inherited = { ...childDetail, context_manifest: { manifest_id: 'context:grandchild', policy_ref: 'test', revision: 1, selected_inputs: [], effective_inputs: [], excluded_inputs: [{ evidence_ref: 'library:docver_parent', reason: 'parent_library_not_selected_for_this_run' }], rules_ref: 'test', tool_versions: {}, data_versions: {}, input_fingerprint: 'a'.repeat(64), created_at: now } }
    await act(async () => root.render(<ResearchRunDetail detail={inherited} libraryScope={null} parentLibraryScope={null} {...props} />))
    expect(container.textContent).toContain('本次复查未重新使用历史研究选择的 1 份资料')
    await act(async () => root.unmount())
  })

  it('preserves the report and its filtered inbox return when opening a citation', () => {
    const report = researchRouteHref({ name: 'report', runId: 'run-1', back: '#/delivery?unread=1&offset=12' })
    window.history.replaceState(null, '', report)
    expect(new URLSearchParams(evidenceRouteHref('run-1', 'ev-1').split('?')[1]).get('back')).toBe(report)
    expect(new URLSearchParams(evidenceRouteHref('another-run', 'ev-1').split('?')[1]).get('back')).toBeNull()
  })

  it.each(['#/Evolution', '#/research', '#/market', '#/sources?status=failed'])('preserves %s through report citations without nesting drawers', (back) => {
    const report = researchRouteHref({ name: 'report', runId: 'run-1', back })
    window.history.replaceState(null, '', report)
    const first = evidenceRouteHref('run-1', 'ev-1')
    expect(new URLSearchParams(first.split('?')[1]).get('back')).toBe(report)
    window.history.replaceState(null, '', first)
    expect(new URLSearchParams(evidenceRouteHref('run-1', 'ev-2').split('?')[1]).get('back')).toBe(report)
  })

  it('renders the real market explanation readably while retaining its original for audit', async () => {
    const original = '买方深度 185332 contracts，book_imbalance/crowding_signal 0.892949；proxy_kind 是 book_imbalance，不是 liquidation，不能代表全市场。'
    const sample = { ...detail, causal_case: { ...detail.causal_case!, main_chain: [link('live-market', original)] } }
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />))
    const visible = container.querySelector('[aria-label="本次综合研究"] > p:nth-child(2)')?.textContent
    expect(visible).toContain('185332 张合约')
    expect(visible).toContain('买卖盘失衡度/拥挤度指标 0.892949')
    expect(visible).toContain('代理指标类型 是 买卖盘失衡度，不是 强平，不能代表全市场')
    expect(container.querySelector('.research-summary-original')?.textContent).toContain(original)
    expect(container.querySelector<HTMLDetailsElement>('.research-summary-original')?.open).toBe(false)
    await act(async () => root.unmount())
  })

  it('preserves node identity and return context in a shareable graph link', () => {
    const href = researchRouteHref({ name: 'graph-node', runId: 'run-1', nodeId: 'evidence:one/two', back: '#/graph?run=run-1' })
    const params = new URLSearchParams(href.split('?')[1])
    expect(params.get('node')).toBe('evidence:one/two')
    expect(params.get('run')).toBe('run-1')
    expect(params.get('back')).toBe('#/graph?run=run-1')
  })

  it('keeps node return routes bounded while evidence returns to its exact node', () => {
    let current = '#/report?run=run-1'
    for (let index = 0; index < 20; index++) {
      current = researchRouteHref(researchNavigationRoute({ name: 'graph-node', runId: 'run-1', nodeId: `node-${index}` }, current))
    }
    const nodeParams = new URLSearchParams(current.split('?')[1])
    expect(nodeParams.get('node')).toBe('node-19')
    expect(nodeParams.get('back')).toBe('#/graph?run=run-1&back=%23%2Freport%3Frun%3Drun-1')
    expect(current.length).toBeLessThan(200)
    expect(researchNavigationRoute({ name: 'evidence', runId: 'run-1', evidenceId: 'ev-1' }, current).back).toBe(current)
    expect(researchNavigationRoute({ name: 'report', runId: 'run-1' }, current).back).toBeUndefined()
  })

  it('keeps the report parent and graph filters through repeated nodes and evidence', () => {
    const report = researchRouteHref({ name: 'report', runId: 'run-1', back: '#/home' })
    const graph = researchRouteHref({ name: 'graph', runId: 'run-1', back: report, graphQuery: '受限', graphFilter: 'gate', graphView: 'list' })
    let current = graph
    for (let i = 0; i < 20; i++) current = researchRouteHref(researchNavigationRoute({ name: 'graph-node', runId: 'run-1', nodeId: `node-${i}` }, current))
    const params = new URLSearchParams(current.split('?')[1])
    expect(params.get('back')).toBe(graph)
    expect(params.get('q')).toBe('受限')
    expect(params.get('view')).toBe('list')
    expect(graphBaseRoute(current, 'run-1').back).toBe(report)
    expect(current.length).toBeLessThan(600)
    expect(researchNavigationRoute({ name: 'evidence', runId: 'run-1', evidenceId: 'ev-1' }, current).back).toBe(current)
    expect(graphBaseRoute(current, 'another-run').back).toBe('#/report?run=another-run')
    expect(graphBaseRoute('#/graph-node?run=run-1&back=https%3A%2F%2Fexample.com', 'run-1').back).toBe('#/report?run=run-1')
  })

  it('counts only accepted evidence and its sources in inline and fullscreen graph summaries', async () => {
    const evidence: ResearchRunDetailView['evidence'] = ['accepted', 'candidate', 'rejected'].map((quality, index) => ({
      evidence_id: `ev-${index}`, requirement_id: 'market_transmission', kind: 'market', authority: 'exchange',
      source_id: `source-${index}`, source_url: null, published_at: now, observed_at: now, received_at: now,
      content_hash: 'a'.repeat(64), excerpt: 'Recorded observation', content_ref: null, acquisition_receipt_id: null,
      source_content_hash: null, parser_ref: null, structured_payload_ref: null, tool_call_id: `tool-${index}`,
      research_session_id: 'session', round: 1, quality: quality as 'accepted' | 'candidate' | 'rejected',
      freshness_status: 'fresh', conflict_group: null,
    }))
    const graph: NonNullable<typeof detail.decision_graph> = { schema_version: 'decision-graph.v1', graph_status: 'causal', generation_mode: 'causal_case', stop_reason: null, nodes: [{ node_id: 'gate', node_type: 'gate', label: '执行质量门', summary: 'Bounded', status: 'blocked', evidence_refs: ['ev-0'] }], edges: [] }
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<ResearchRunDetail detail={{ ...detail, evidence, decision_graph: graph }} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} initialDetailView="technical" />))
    const summary = () => [...container!.querySelectorAll('[aria-label="研究审计摘要"]')].map(item => item.textContent)
    expect(summary()).toEqual([expect.stringContaining('1 条采纳证据1 个来源')])
    await act(async () => container!.querySelector<HTMLButtonElement>('[aria-label="全屏查看分析链路"]')!.click())
    expect(summary()).toHaveLength(2)
    expect(summary().every(text => text?.includes('1 条采纳证据1 个来源'))).toBe(true)
    await act(async () => root.unmount())
  })

  it('restores graph filters on refresh, explains empty results, and clears the node on return', async () => {
    const graph: NonNullable<typeof detail.decision_graph> = { schema_version: 'decision-graph.v1', graph_status: 'causal', generation_mode: 'causal_case', stop_reason: null, nodes: [{ node_id: 'gate', node_type: 'gate', label: '执行质量门', summary: 'Not authorized', status: 'blocked', evidence_refs: ['ev-1'] }], edges: [] }
    const graphDetail = { ...detail, decision_graph: graph }
    window.history.replaceState(null, '', researchRouteHref({ name: 'graph-node', runId: detail.run.run_id, nodeId: 'gate', graphQuery: '受限', graphFilter: 'gate', graphView: 'list' }))
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const props = { detail: graphDetail, commandReason: '', setCommandReason: () => undefined, commandPending: false, commandError: null, onCommand: vi.fn(), graphRouteOpen: true }
    await act(async () => root.render(<ResearchRunDetail {...props} graphNodeId="gate" />))
    const dialog = container.querySelector('[aria-label="全屏分析链路"]')!
    expect(dialog.querySelector<HTMLInputElement>('input')?.value).toBe('受限')
    expect(dialog.querySelector('select')?.value).toBe('gate')
    expect(dialog.querySelectorAll('.graph-node-list > button')).toHaveLength(1)
    expect(dialog.querySelector('.graph-node-inspector')).not.toBeNull()
    await act(async () => root.render(<ResearchRunDetail {...props} />))
    expect(dialog.querySelector('.graph-node-inspector')).toBeNull()
    const input = dialog.querySelector('input')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '无匹配节点')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(dialog.textContent).toContain('没有符合条件的节点')
    expect(new URLSearchParams(window.location.hash.split('?')[1]).get('q')).toBe('无匹配节点')
    await act(async () => [...dialog.querySelectorAll('button')].find(b => b.textContent === '清除筛选')!.click())
    expect(dialog.querySelectorAll('.graph-node-list > button')).toHaveLength(1)
    expect(input.value).toBe('')
    expect(dialog.querySelector('select')?.value).toBe('all')
    await act(async () => root.unmount())
  })

  it('mounts the graph only after opening the research process and restores collapsed state for another report', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const props = { commandReason: '', setCommandReason: () => undefined, commandPending: false, commandError: null, onCommand: vi.fn() }
    await act(async () => { root.render(<ResearchRunDetail detail={detail} {...props} />) })
    expect(container.querySelector('.decision-graph-panel')).toBeNull()
    const process = container.querySelector<HTMLDetailsElement>('.research-report-evidence')!
    await act(async () => { process.open = true; process.dispatchEvent(new Event('toggle')) })
    expect(container.querySelector('.decision-graph-panel')).not.toBeNull()
    await act(async () => { root.render(<ResearchRunDetail detail={{ ...detail, run: { ...detail.run, run_id: 'another-report' } }} {...props} />) })
    expect(container.querySelector('.decision-graph-panel')).toBeNull()
    await act(async () => { root.unmount() })
  })

  it('offers separate record sections and preserves full technical access', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const props = { detail, commandReason: '', setCommandReason: () => undefined, commandPending: false, commandError: null, onCommand: vi.fn(), diagnostics: <p>已保存评估记录</p> }
    await act(async () => root.render(<ResearchRunDetail {...props} />))
    const sections = [...container.querySelectorAll<HTMLDetailsElement>('.research-record-section')]
    expect(sections).toHaveLength(5)
    expect(sections.every(section => !section.open)).toBe(true)
    expect(sections.map(section => section.querySelector('summary')?.textContent)).toContain('运行步骤与用量费用')
    const review = sections.find(section => section.getAttribute('aria-label')?.startsWith('完整审查记录'))!
    await act(async () => { review.open = true; review.dispatchEvent(new Event('toggle')) })
    expect(review.textContent).toContain(detail.reviews[0].challenge)
    await act(async () => root.render(<ResearchRunDetail {...props} initialDetailView="technical" />))
    expect([...container.querySelectorAll<HTMLDetailsElement>('.research-record-section')].every(section => section.open)).toBe(true)
    await act(async () => root.unmount())
  })

  it('opens the process immediately for graph deep links and technical routes', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const props = { detail, commandReason: '', setCommandReason: () => undefined, commandPending: false, commandError: null, onCommand: vi.fn() }
    await act(async () => { root.render(<ResearchRunDetail {...props} graphRouteOpen />) })
    expect(container.querySelector<HTMLDetailsElement>('.research-report-evidence')?.open).toBe(true)
    expect(container.querySelector('.decision-graph-panel')).not.toBeNull()
    await act(async () => { root.render(<ResearchRunDetail {...props} initialDetailView="technical" />) })
    expect(container.querySelector<HTMLDetailsElement>('.research-report-evidence')?.open).toBe(true)
    await act(async () => { root.unmount() })
  })

  it('links the persisted document version instead of an Evidence input', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={detail} libraryScope={{ schema_version: 'research-run-scope.v1', run_id: detail.run.run_id, research_family: 'crypto_macro', library_context_mode: 'selected', allow_remote_library_content: false, context_inputs: [{version_id: 'docver_real_version', evidence_id: 'library:docver_real_version:part:1:abc', excerpt: 'Archived source text', excerpt_hash: 'a'.repeat(64), original_file_hash: 'b'.repeat(64), locator_refs: ['document://docver_real_version/page/1/block/1']}], selected_version_ids: ['docver_real_version'] }} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })
    const link = container.querySelector<HTMLAnchorElement>('[aria-label="已连接研究资料"] a')
    expect(link?.getAttribute('href')).toContain('version=docver_real_version')
    expect(container.textContent).toContain('已纳入初始研究上下文：1 个正文片段。')
    expect(container.textContent).toContain('核对输入片段 1')
    const audit = container.querySelector<HTMLDetailsElement>('.library-input-audit')!
    expect(audit.open).toBe(false)
    await act(async () => { audit.open = true; audit.dispatchEvent(new Event('toggle')) })
    expect(audit.querySelector('a')?.getAttribute('href')).toContain('evidence=library%3Adocver_real_version')
    await act(async () => { root.unmount() })
  })
  it('opens a linked Run and keeps a newly submitted Run selected while the list is stale', async () => {
    window.history.replaceState(null, '', '/?run_id=run-linked')
    vi.spyOn(api, 'researchRuns').mockResolvedValue([detail.run])
    vi.spyOn(api, 'researchInbox').mockRejectedValue(new Error('inbox unavailable'))
    vi.spyOn(api, 'macroEvents').mockResolvedValue([])
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([{
      schema_version: 'review-schedule-view.v1', review_plan_id: 'review-plan-1',
      parent_run_id: 'run-linked', horizon: '24h', trigger_type: 'next_review',
      trigger_ref: '20260829T180000Z', trigger_at: '2026-08-29T18:00:00Z', status: 'planned',
      child_run_id: null, failure_code: null, updated_at: now,
    }])
    vi.spyOn(api, 'researchObservability').mockRejectedValue(new Error('diagnostics unavailable'))
    vi.spyOn(api, 'researchValueEvaluation').mockResolvedValue(evaluation)
    vi.spyOn(api, 'researchForecastAudit').mockResolvedValue([{
      schema_version: 'forecast-audit-view.v1', forecast_id: 'forecast-24h', horizon: '24h',
      forecast_direction: 'short', strategy_version: 'daily-trend-v0', forecasted_at: now,
      as_of: '2026-08-29', input_snapshot_id: 'snapshot-1', input_fingerprint: 'a'.repeat(64),
      evidence_cutoff_at: now, feature_snapshot: { return_1d_pct: -0.5 }, trailing_direction: 'bearish',
      same_as_trailing_trend: true, forecasted_before_outcome: true, immutable_snapshot: true,
    }])
    const readRun = vi.spyOn(api, 'researchRun').mockImplementation(async (id) => ({
      ...detail, run: { ...detail.run, run_id: id, event_title: `Report for ${id}` },
    }))
    vi.spyOn(api, 'submitResearch').mockResolvedValue({
      schema_version: 'research-run-queued.v1', event_id: 'event-new', run_id: 'run-new', status: 'queued', status_url: '/v1/runs/run-new',
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const flushQueries = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>) })
    await flushQueries()
    expect(readRun).toHaveBeenCalledWith('run-linked')
    expect(container.textContent).toContain('Report for run-linked')

    const findButton = (text: string) => [...container!.querySelectorAll('button')].find((button) => button.textContent === text)!
    await act(async () => { findButton('获取最新操作建议').click() })
    await act(async () => { findButton('开始分析').click() })
    await flushQueries()
    await flushQueries()
    expect(container.textContent).toContain('预测复盘')
    expect(container.textContent).toContain('24h')
    expect(container.textContent).toContain('方向：正确')
    expect(container.textContent).toContain('Brier：0.180')
    expect(container.textContent).toContain('样本外校准准入')
    expect(container.textContent).toContain('概率未获交易准入')
    expect(container.textContent).toContain('自动复查计划')
    expect(container.textContent).toContain('已计划')
    expect(api.submitResearch).toHaveBeenCalledTimes(1)
    expect(api.submitResearch).toHaveBeenCalledWith('', expect.anything(), 'owner')
    expect(readRun).toHaveBeenCalledWith('run-new')
    expect(container.textContent).toContain('Report for run-new')
    expect(new URLSearchParams(window.location.search).get('run_id')).toBe('run-new')
    expect(queryClient.getQueryData(['research-runs'])).toEqual([detail.run])
    await act(async () => { root.unmount() })
    queryClient.clear()
  })

  it('keeps a stale shared Run URL visible as an error without silently replacing it', async () => {
    window.history.replaceState(null, '', '/?run_id=run-missing')
    const readableRun = { ...detail.run, run_id: 'run-readable', status: 'research_only' as const, artifact_id: 'artifact-readable' }
    vi.spyOn(api, 'researchRuns').mockResolvedValue([readableRun])
    vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
    vi.spyOn(api, 'macroEvents').mockResolvedValue([])
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
    vi.spyOn(api, 'researchObservability').mockRejectedValue(new Error('diagnostics unavailable'))
    vi.spyOn(api, 'researchValueEvaluation').mockResolvedValue(null)
    vi.spyOn(api, 'researchForecastAudit').mockResolvedValue([])
    vi.spyOn(api, 'researchRunComparison').mockRejectedValue(new Error('comparison unavailable'))
    const readRun = vi.spyOn(api, 'researchRun').mockImplementation(async (id) => {
      if (id === 'run-missing') throw new Error('research_run_not_found')
      return { ...detail, run: { ...readableRun, event_title: 'Latest readable report' } }
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const flushQueries = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)) })
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>) })
    await flushQueries()
    await flushQueries()

    expect(readRun).toHaveBeenCalledWith('run-missing')
    expect(readRun).not.toHaveBeenCalledWith('run-readable')
    expect(container.textContent).toContain('重新读取这次研究')
    expect(container.textContent).not.toContain('Latest readable report')
    expect(new URLSearchParams(window.location.search).get('run_id')).toBe('run-missing')
    await act(async () => { root.unmount() })
    queryClient.clear()
  })

  it('distinguishes terminal limited steps and linked material quality while retaining originals', async () => {
    const sample: ResearchRunDetailView = { ...detail, evidence: [
      { evidence_id: 'ev-accepted', requirement_id: 'crypto_spot_confirmation', kind: 'market', authority: 'exchange', source_id: 'venue-a', source_url: null, published_at: null, observed_at: now, received_at: now, content_hash: 'a'.repeat(64), excerpt: '{"values":{"spot_price":"83464"}}', acquisition_receipt_id: null, source_content_hash: null, parser_ref: null, structured_payload_ref: null, content_ref: null, tool_call_id: null, research_session_id: 'session-1', round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null },
      { evidence_id: 'ev-candidate', requirement_id: 'crypto_spot_confirmation', kind: 'market', authority: 'exchange', source_id: 'venue-b', source_url: null, published_at: null, observed_at: now, received_at: now, content_hash: 'b'.repeat(64), excerpt: '{"values":{"spot_price":"83482"}}', acquisition_receipt_id: null, source_content_hash: null, parser_ref: null, structured_payload_ref: null, content_ref: null, tool_call_id: null, research_session_id: 'session-1', round: 1, quality: 'candidate', freshness_status: 'fresh', conflict_group: null },
    ], role_packets: [{ schema_version: 'role-packet.v1', packet_id: 'packet-1', role: 'market_structure', round: 1, status: 'partial', claims: ['original truncated JSON {"spot_price":"834'], conclusion: 'original role conclusion', evidence_refs: ['ev-accepted', 'ev-candidate', 'ev-missing'], gap_ids: ['crypto_spot_confirmation'], transition_ids: [], created_at: now }] }
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} initialDetailView="technical" />))
    expect(container.querySelector('.research-process header')?.textContent).toContain('8/8 步已结束 · 1 步有限制')
    const card = container.querySelector('.role-packet-card')!
    expect(card.querySelector(':scope > p')?.textContent).toContain('1 条已采纳材料，1 条待核对或未采纳；另有 1 条引用未定位')
    expect(card.querySelector('li p')?.textContent).toContain('现货价格 83,464')
    expect(card.querySelector('li a')?.getAttribute('href')).toContain('evidence=ev-accepted')
    expect(card.querySelector<HTMLDetailsElement>('.research-original-record')?.open).toBe(false)
    const originalConclusion = [...card.querySelectorAll<HTMLDetailsElement>('.research-original-record')].find(record => record.textContent?.includes('original truncated JSON'))
    expect(originalConclusion).toBeDefined()
    expect(originalConclusion?.open).toBe(false)
    expect(card.querySelector('li .research-original-record')?.textContent).toContain('venue-a')
    expect(container.querySelector<HTMLDetailsElement>('.research-evidence-list .research-original-record')?.open).toBe(false)
    expect(container.querySelector('.research-evidence-list .research-original-record')?.textContent).toContain(sample.evidence[0].excerpt)
    await act(async () => root.unmount())
  })

  it('renders sufficiency, causal counter-chain, distinct horizons and normalized trace', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={detail} commandReason="Owner review" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })

    expect(container.querySelector('.research-action-heading h2')?.textContent).toBe('本次 AI 综合判断')
    const actionEvidenceLinks = [...container.querySelectorAll<HTMLAnchorElement>('.research-action-summary a[href^="#/evidence"]')]
    expect(actionEvidenceLinks.length).toBeGreaterThan(0)
    expect(actionEvidenceLinks.every((link) => link.getAttribute('href')?.includes(`run=${detail.run.run_id}&evidence=`))).toBe(true)
    // The referenced evidence is absent from this fixture's saved evidence list.
    expect(actionEvidenceLinks[0]?.textContent).toBe('相关证据')
    expect(container.textContent).toContain('7日价格规则倾向：偏多')
    expect(container.textContent).not.toContain('观望，当前不交易')
    expect(container.textContent).toContain('确认与失效条件')
    expect(container.textContent).toContain('数据截至：')
    expect(container.textContent).toContain('7日价格规则倾向：偏多；交易权限未授权')
    expect(container.querySelector<HTMLDetailsElement>('[aria-label="日线规则参考"]')?.open).toBe(false)
    expect(container.querySelector('[aria-label="日线规则参考"] summary')?.textContent).toContain('独立日线参考')
    expect(container.querySelector('.research-summary-audit')?.hasAttribute('open')).toBe(false)
    const synthesis = container.querySelector('[aria-label="本次综合研究"]')
    expect(synthesis?.textContent).toContain(detail.causal_case!.thesis)
    expect(synthesis?.textContent).toContain(detail.causal_case!.opposite_chain[0].statement)
    expect(synthesis?.textContent).toContain(detail.reviews[0].challenge)
    expect(synthesis?.textContent).toContain('审查结论：有保留')
    expect(container.querySelectorAll('.research-window-summary article')).toHaveLength(3)
    expect(container.querySelector('.research-summary-scope a')?.getAttribute('href')).toBe(`#/results?run=run-research-1&back=${encodeURIComponent(window.location.hash || '#/report?run=run-research-1')}`)
    expect(container.textContent).toContain('偏多')
    expect(container.textContent).toContain('日线继续站上 SMA20')
    expect(container.textContent).toContain('未授权交易')
    expect(container.textContent).toContain('执行权限')
    expect(container.textContent).toContain('执行阻断')
    expect(container.textContent).toContain('缺少执行级证据')
    expect(container.textContent).toContain('查看来源与证据清单')
    expect(container.textContent).toContain('方向效果尚待验证')
    expect(container.textContent).toContain('使用这份研究时仍需核对')
    expect(container.textContent).toContain('最新公开信息未核验')
    expect(container.textContent).toContain('可靠性')
    expect(container.textContent).toContain('部分调用未完成，已取得的证据继续保留')
    expect(container.textContent).not.toContain('失败步骤已关闭输出')
    expect(container.textContent).toContain('分析过程')
    expect(container.textContent).toContain('实时宏观传导数据')
    expect(container.textContent).toContain('用户提交')
    expect(container.textContent).not.toContain('高优先级')
    expect(container.textContent).toContain('主论据')
    expect(container.textContent).toContain('反向解释')
    expect(container.textContent).toContain('独立复核状态未核验')
    expect(container.textContent).toContain('Transmission is not yet confirmed.')
    expect(container.textContent).toContain('DXY confirmation missing')
    expect(container.querySelector('.research-window-summary details')?.textContent).toContain('30 分钟 trigger')
    expect(container.querySelector<HTMLDetailsElement>('.research-window-summary details')?.open).toBe(false)
    expect(container.textContent).not.toContain('24h 条件')
    expect(container.textContent).not.toContain('72h 条件')
    expect(container.textContent).not.toContain('本次研究结束')
    const technicalTab = [...container.querySelectorAll('button')].find((button) => button.textContent === '技术详情')
    await act(async () => { technicalTab?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(container.textContent).toContain('本次研究结束')
    expect(container.textContent).toContain('research_capability_timeout')
    expect(container.textContent).toContain('market.cross_asset')
    expect(container.textContent).toContain('允许有界重试')
    expect(container.textContent).not.toContain('{"')
    expect(container.querySelectorAll('.horizon-grid article')).toHaveLength(0)
    expect(container.querySelectorAll('.research-outlook')).toHaveLength(1)
    await act(async () => { root.unmount() })
  })

  it('shows the DSH receipt and a source matrix in the user result view', async () => {
    const trustedDetail = researchRunDetailViewSchema.parse({
      ...detail,
      evidence: [{
        evidence_id: 'ev-source-1', requirement_id: 'market_narrative', kind: 'web', authority: 'official',
        source_id: 'federalreserve.gov', source_url: 'https://www.federalreserve.gov/example', published_at: now,
        observed_at: now, received_at: now, content_hash: '4'.repeat(64), excerpt: 'Official policy statement.',
        content_ref: null, source_content_hash: null, parser_ref: null, structured_payload_ref: null,
        tool_call_id: 'call-source-1', research_session_id: 'dsh-session-1', round: 1, quality: 'accepted',
        freshness_status: 'fresh', conflict_group: null,
      }],
      total_tool_calls: 9,
      total_subagents: 5,
    })
    const sourceEquivalentDetail = researchRunDetailViewSchema.parse({
      ...trustedDetail,
      evidence: [trustedDetail.evidence[0],
        { ...trustedDetail.evidence[0], evidence_id: 'ev-source-2', source_id: '美联储官网', content_hash: '5'.repeat(64) },
        { ...trustedDetail.evidence[0], evidence_id: 'ev-source-3', source_id: 'unknown-provider-source.v9', quality: 'rejected', content_hash: '6'.repeat(64) }],
    })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={sourceEquivalentDetail} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })

    const trust = container.querySelector('[aria-label="可信度与来源"]')
    expect(trust?.textContent).toContain('可查看采纳证据的来源记录')
    expect(trust?.querySelector('a')?.getAttribute('href')).toBe('#/evidence?run=run-research-1&evidence=ev-source-1')
    expect(trust?.querySelector('details')?.open).toBe(false)
    expect(trust?.querySelector('.research-trust-grid')?.textContent).toContain('9 次取证执行（网关记录）')
    expect(trust?.querySelector('.research-trust-grid')?.textContent).not.toContain('dsh-session-1')
    expect(trust?.textContent).toContain('运行时：dsh')
    expect([...trust!.querySelectorAll('.research-source-matrix a strong')].map(node => node.textContent)).toEqual(['美联储官网', '美联储官网'])
    expect([...trust!.querySelectorAll('.research-source-matrix a')].map(node => node.getAttribute('href'))).toEqual(['#/evidence?run=run-research-1&evidence=ev-source-1', '#/evidence?run=run-research-1&evidence=ev-source-2'])
    expect(trust?.querySelector('.research-trust-grid > div:nth-child(2) strong')?.textContent).toBe('2 个')
    expect(trust?.querySelector('.research-source-identity')?.textContent).toBe('来源身份美联储官网 · 官方来源美联储官网 · 官方来源')
    expect(trust?.querySelector('a[href="#/evidence?run=run-research-1&evidence=ev-source-3"]')?.textContent).toBe('来源名称待核对')
    const sourceAudit = trust?.querySelector<HTMLDetailsElement>('details[aria-label="来源标识核对"]')
    expect(sourceAudit?.open).toBe(false)
    expect(sourceAudit?.textContent).toContain('federalreserve.gov')
    expect(sourceAudit?.textContent).toContain('unknown-provider-source.v9')
    await act(async () => { sourceAudit!.querySelector('summary')!.click() })
    expect(sourceAudit?.open).toBe(true)
    expect(sourceEquivalentDetail.evidence.map(item => item.source_id)).toEqual(['federalreserve.gov', '美联储官网', 'unknown-provider-source.v9'])
    expect(trust?.textContent).toContain('官方来源')
    expect(trust?.textContent).not.toContain('official')
    expect(trust?.textContent).toContain('最新公开信息未核验')
    expect(trust?.textContent).toContain('证据覆盖通过不代表预测已经校准')
    const rejectedOnly = researchRunDetailViewSchema.parse({
      ...sourceEquivalentDetail,
      evidence: sourceEquivalentDetail.evidence.map(item => ({ ...item, quality: 'rejected' })),
    })
    await act(async () => { root.render(<ResearchRunDetail detail={rejectedOnly} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })
    expect(trust?.textContent).toContain('尚未采纳证据')
    expect(trust?.textContent).not.toContain('可查看采纳证据的来源记录')
    await act(async () => { root.render(<ResearchRunDetail detail={sourceEquivalentDetail} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })
    expect(trust?.textContent).toContain('可查看采纳证据的来源记录')
    expect(trust?.textContent).not.toContain('尚未采纳证据')
    await act(async () => { root.unmount() })
  })

  it('shows all data limitations in the run list even when hard coverage is complete', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const runWithSoftGaps = {
      ...detail.run,
      coverage: {
        ...detail.run.coverage!, status: 'sufficient' as const, hard_coverage_ratio: 1,
        gaps: [{ ...detail.run.coverage!.gaps[0], importance: 'soft' as const, blocks_directional_output: false }],
      },
      stop_reason: { ...detail.run.stop_reason!, remaining_hard_gaps: [] },
    }
    await act(async () => { root.render(<ResearchRunList runs={[runWithSoftGaps]} selectedId={runWithSoftGaps.run_id} loading={false} failed={false} onSelect={vi.fn()} />) })

    expect(container.textContent).toContain('1 项数据限制')
    expect(container.textContent).toContain('100% 必需证据')
    expect(container.textContent).not.toContain('0 个硬缺口')
    await act(async () => { root.unmount() })
  })

  it('does not present an active run as a failed conclusion or reuse a pending synthesis', async () => {
    const runningDetail = researchRunDetailViewSchema.parse({
      ...detail,
      run: { ...detail.run, status: 'researching' },
      action_summary: { ...detail.action_summary, recommendation: 'unavailable', research_outlook: null, artifact_status: 'pending' },
    })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={runningDetail} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })
    const summary = container.querySelector('[aria-label="研究判断摘要"]')!
    expect(summary.textContent).toContain('正在取证与复核，结论生成中')
    expect(summary.textContent).not.toContain('无法形成可靠判断')
    expect(summary.textContent).not.toContain('重新评估条件')
    expect(summary.querySelector('[aria-label="本次综合研究"]')).toBeNull()
    expect(container.querySelector('.research-report-context')?.textContent).not.toContain('本次已保存的研究结论')
    await act(async () => { root.unmount() })
  })

  it('keeps action boundaries and evidence visible when synthesis has no valid horizons', async () => {
    const limitedDetail = researchRunDetailViewSchema.parse({
      ...detail,
      horizons: [],
      action_summary: {
        ...detail.action_summary,
        recommendation: 'do_not_trade',
        research_outlook: null,
        supporting_evidence_refs: ['ev-1'],
        blocking_gap_ids: ['macro_transmission'],
      },
    })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={limitedDetail} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })

    expect(container.textContent).toContain('重新评估条件')
    expect(container.textContent).toContain('触发：补齐实时宏观传导数据')
    expect(container.textContent).toContain('失效：任一关键来源过期、冲突或再次不可用时，当前边界继续有效')
    expect(container.textContent).toContain('关键依据')
    expect(container.querySelector('.research-action-summary a[title="ev-1"]')?.getAttribute('href')).toBe('#/evidence?run=run-research-1&evidence=ev-1')
    await act(async () => { root.unmount() })
  })

  it('explains synthesis attestation failure and renders bounded evidence summaries', async () => {
    const failedDetail = researchRunDetailViewSchema.parse({
      ...detail,
      run: {
        ...detail.run,
        status: 'failed',
        failure: {
          error_code: 'dsh_evidence_unattested',
          origin: 'orchestration',
          cause_code: 'synthesis_attestation',
          capability_id: null,
          tool_call_id: null,
          retryable: false,
          deadline_ms: null,
        },
      },
      evidence: [
        {
          evidence_id: 'ev-market-1', requirement_id: 'crypto_spot_confirmation', kind: 'market', authority: 'exchange', source_id: 'coinex-public', source_url: 'https://api.coinex.com/v2/spot/ticker?market=BTCUSDT', published_at: null, observed_at: now, received_at: now, content_hash: '1'.repeat(64), excerpt: '{"market":"BTCUSDT","values":{"spot_price":"78719","spot_volume":"368.4"}}', structured_payload_ref: null, tool_call_id: 'call-market', research_session_id: 'dsh-session-1', round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
        },
        {
          evidence_id: 'ev-official-1', requirement_id: 'event_identity', kind: 'official', authority: 'official', source_id: 'federal-reserve', source_url: 'https://www.federalreserve.gov/example', published_at: null, observed_at: now, received_at: now, content_hash: '2'.repeat(64), excerpt: '<html><body><nav>Skip to main content Main Menu Careers FAQs</nav><main>Chairman testimony confirmed the policy event and its publication time.</main></body></html>', structured_payload_ref: null, tool_call_id: 'call-official', research_session_id: 'dsh-session-1', round: 1, quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
        },
      ],
      rounds: [],
      causal_case: null,
      horizons: [],
      action_summary: {
        ...detail.action_summary,
        recommendation: 'unavailable',
        headline: '无法形成可靠判断，不应据此交易',
        reliability: 'unavailable',
        artifact_status: 'rejected',
      },
    })
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<ResearchRunDetail detail={failedDetail} commandReason="" setCommandReason={() => undefined} commandPending={false} commandError={null} onCommand={vi.fn()} />) })

    expect(container.textContent).toContain('无法形成可靠判断，不应据此交易')
    expect(container.textContent).toContain('本次研究未完成，已有材料仍保留')
    const technicalTab = [...container.querySelectorAll('button')].find((button) => button.textContent === '技术详情')
    await act(async () => { technicalTab?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(container.textContent).toContain('综合结果的证据校验失败')
    expect(container.textContent).toContain('2 条证据已保留')
    expect(container.textContent).toContain('现货价格 78,719')
    expect(container.textContent).toContain('Chairman testimony confirmed')
    expect(container.querySelector('.research-evidence-list article > p')?.textContent).not.toContain('{"market"')
    expect(container.querySelector('.research-evidence-list article > p')?.textContent).not.toContain('<html>')
    await act(async () => { root.unmount() })
  })
})


it('reads a library citation from its input receipt without representing it as market evidence', async () => {
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const receipt = {version_id: 'docver_source', evidence_id: 'library:docver_source:part:1:abc', excerpt: 'Meeting calendar source text', excerpt_hash: 'a'.repeat(64), original_file_hash: 'b'.repeat(64), locator_refs: ['document://docver_source/page/1/block/1']}
  await act(async () => root.render(<EvidenceDrawer evidence={null} libraryInput={receipt} onClose={vi.fn()} />))
  expect(container.textContent).toContain('Meeting calendar source text')
  expect(container.textContent).toContain('辅助资料输入')
  expect(container.textContent).not.toContain('该证据已不在')
  expect(container.querySelector('a')?.getAttribute('href')).toContain('version=docver_source')
  await act(async () => root.unmount())
})

it('opens an archived web original and keeps accepted evidence category neutral', async () => {
  const evidence = researchRunDetailViewSchema.parse({ ...detail, evidence: [{
    evidence_id: 'ev-original', requirement_id: 'market_narrative', kind: 'web', authority: 'official',
    source_id: 'federalreserve.gov', source_url: 'https://www.federalreserve.gov/example',
    published_at: now, observed_at: now, received_at: now, content_hash: 'a'.repeat(64),
    excerpt: '完整原始段落。'.repeat(100) + '末尾限定条件仍应保留。', structured_payload_ref: null,
    content_ref: `document://sha256/${'b'.repeat(64)}`, source_content_hash: 'c'.repeat(64), parser_ref: 'test-parser',
    tool_call_id: 'call-original', research_session_id: 'session-original', round: 1,
    quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
  }] }).evidence[0]
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const read = vi.spyOn(api, 'researchEvidenceOriginal').mockRejectedValueOnce(new Error('原文未通过完整性校验')).mockResolvedValue('已核验的完整网页正文')
  await act(async () => root.render(<QueryClientProvider client={client}><EvidenceDrawer runId="run-original" evidence={evidence} onClose={vi.fn()} /></QueryClientProvider>))
  expect(container.querySelector('.drawer-header h2')?.textContent).toBe('美联储官网')
  const knownSourceAudit = container.querySelector<HTMLDetailsElement>('details[aria-label="来源标识核对"]')
  expect(knownSourceAudit?.open).toBe(false)
  expect(knownSourceAudit?.textContent).toContain('federalreserve.gov')
  await act(async () => root.render(<QueryClientProvider client={client}><EvidenceDrawer runId="run-original" evidence={{ ...evidence, source_id: 'unknown-provider-source.v9' }} onClose={vi.fn()} /></QueryClientProvider>))
  expect(container.querySelector('.drawer-header h2')?.textContent).toBe('来源名称待核对')
  const unknownSourceAudit = container.querySelector<HTMLDetailsElement>('details[aria-label="来源标识核对"]')
  expect(unknownSourceAudit?.open).toBe(false)
  expect(unknownSourceAudit?.textContent).toContain('unknown-provider-source.v9')
  await act(async () => { unknownSourceAudit!.querySelector('summary')!.click() })
  expect(unknownSourceAudit?.open).toBe(true)
  await act(async () => root.render(<QueryClientProvider client={client}><EvidenceDrawer runId="run-original" evidence={evidence} onClose={vi.fn()} /></QueryClientProvider>))
  expect(container.textContent).toContain('公开信息与市场叙事')
  expect(container.textContent).not.toContain('最新公开信息未核验')
  expect(container.textContent).toContain('末尾限定条件仍应保留。')
  expect(read).not.toHaveBeenCalled()
  const original = [...container.querySelectorAll('button')].find(a => a.textContent?.includes('阅读已保存的原文'))!
  await act(async () => { original.click(); await new Promise(resolve => setTimeout(resolve, 20)) })
  expect(container.textContent).toContain('原文未通过完整性校验')
  const retryOriginal = [...container.querySelectorAll('button')].find(a => a.textContent === '重试读取原文')!
  await act(async () => { retryOriginal.click(); await new Promise(resolve => setTimeout(resolve, 20)) })
  expect(container.textContent).toContain('已核验的完整网页正文')
  expect(read).toHaveBeenCalledWith('run-original', 'ev-original', expect.any(AbortSignal))
  await act(async () => root.render(<QueryClientProvider client={client}><EvidenceDrawer runId="run-original" evidence={{ ...evidence, content_ref: null }} onClose={vi.fn()} /></QueryClientProvider>))
  expect(container.textContent).toContain('查找已保存的原始响应')
  expect(container.textContent).not.toContain('没有关联的 HTTP 采集原文')
  expect(container.textContent).not.toContain('阅读已保存的原文')
  await act(async () => root.unmount())
})


it('keeps a saved daily yield record distinct from publication time and retains its exact original excerpt', async () => {
  const evidence = researchRunDetailViewSchema.parse({ ...detail, evidence: [{
    evidence_id: 'ev-yahoo-yield', requirement_id: 'macro_transmission', kind: 'market', authority: 'audited_aggregator',
    source_id: 'yahoo-finance-public', source_url: 'https://finance.yahoo.com/quote/%5ETNX',
    published_at: null, observed_at: now, received_at: now, content_hash: 'a'.repeat(64),
    excerpt: JSON.stringify({ provider: 'yahoo-finance-public', instrument: 'US10Y', completion_contract: 'yahoo-provider-declared-daily.v2', level: '5.236999988555908', change_5d: '7.4999809265136500', return_5d: '1.452921477337899534902794692', latest_date: '2026-10-01' }),
    structured_payload_ref: null, content_ref: null, source_content_hash: null, parser_ref: null,
    tool_call_id: 'call-yahoo-yield', research_session_id: 'session-yahoo-yield', round: 1,
    quality: 'accepted', freshness_status: 'fresh', conflict_group: null,
  }] }).evidence[0]
  const original = JSON.stringify(evidence)
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  await act(async () => root.render(<EvidenceDrawer evidence={evidence} onClose={vi.fn()} />))
  const cell = (label: string) => [...container!.querySelectorAll('.detail-cell')].find(node => node.querySelector('span')?.textContent === label)?.querySelector('strong')?.textContent
  expect(cell('依据类别')).toBe('宏观传导参考（日线）')
  expect(cell('内容发布时间')).toBe('来源未提供发布时间；按已保存的日线区间核对时效')
  expect(cell('观测时间')).toBe(new Date(now).toLocaleString('zh-CN', { hour12: false }))
  expect(cell('时效')).toBe('符合当时的时效要求')
  expect(container.querySelector('.evidence-drawer-excerpt')?.textContent).toContain('收益率水平 5.237%')
  expect(container.querySelector('.evidence-drawer-excerpt')?.textContent).toContain('五个交易日变动 7.499981 基点')
  const originalDetails = [...container.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === '查看完整摘录')!
  expect(originalDetails.open).toBe(false)
  expect(originalDetails.querySelector('p')?.textContent).toBe(evidence.excerpt)
  await act(async () => root.render(<EvidenceDrawer evidence={{ ...evidence, quality: 'stale', freshness_status: 'stale' }} onClose={vi.fn()} />))
  expect(cell('时效')).toBe('超出当时的时效要求')
  expect(cell('内容发布时间')).toBe('来源未提供发布时间；按已保存的日线区间核对时效')
  await act(async () => root.render(<EvidenceDrawer evidence={{ ...evidence, published_at: now }} onClose={vi.fn()} />))
  expect(cell('内容发布时间')).toBe(new Date(now).toLocaleString('zh-CN', { hour12: false }))
  await act(async () => root.render(<EvidenceDrawer evidence={{ ...evidence, source_id: 'unknown-provider' }} onClose={vi.fn()} />))
  expect(cell('依据类别')).toBe('宏观传导数据')
  expect(cell('内容发布时间')).toBe('未保存可核验的发布时间；时效以已保存核验记录为准')
  expect(container.querySelector('.evidence-drawer-excerpt')?.textContent).not.toMatch(/基点|收益率水平|%/)
  expect(JSON.stringify(evidence)).toBe(original)
  await act(async () => root.unmount())
})

it('never presents an undated historical webpage as newly published', () => {
  expect(evidenceTimeLabel({ kind: 'web', published_at: null, freshness_status: 'fresh' })).toBe('发布时间未核验')
  expect(evidenceTimeLabel({ kind: 'market', published_at: null, freshness_status: 'fresh' })).toBe('符合当时的时效要求')
  expect(evidenceTimeLabel({ kind: 'official', published_at: '2026-09-16T18:00:00Z', freshness_status: 'stale' })).toBe('超出当时的时效要求')
})

function preparationWaitingDetail() {
  const at = '2026-10-02T07:00:00Z', runId = 'run-pending'
  const summary = '执行准备尚未确认，原任务与材料保留；等待原租约到期后核对检查点恢复。'
  const error = { error_code: 'research_method_binding_failed', origin: 'orchestration', cause_code: 'cancellation_projection_wait_elapsed', capability_id: null, tool_call_id: null, retryable: true, deadline_ms: 1000, provider_attempts: [] }
  return researchRunDetailViewSchema.parse({
    schema_version: 'research-run-detail-view.v1',
    run: { schema_version: 'research-run-view.v2', run_id: runId, event_id: 'event-pending', event_title: '原研究关注点', admission_origin: 'manual', priority: 'normal', status: 'researching', stage: 'planning', runtime_id: 'dsh-web', profile_ref: null, current_round: 0, budget: null, coverage: null, current_action: summary, stop_reason: null, failure: error, latest_sequence_no: 4, artifact_id: null, available_at: null, parent_run_id: null, created_at: at, updated_at: at },
    research_focus: null, trigger_snapshot: null, decision_snapshot: null, evidence: [], rounds: [], causal_case: null, horizons: [],
    trace: [{ schema_version: 'research-trace-event.v1', run_id: runId, research_session_id: `product:${runId}`, sequence_no: 4, event_type: 'session_stopped', occurred_at: at, stage: 'planning', summary, reference_type: 'run', reference_id: runId, status: 'running', error_code: error.error_code, error }],
    action_summary: { schema_version: 'research-action-summary.v1', recommendation: 'unavailable', headline: '研究进行中，暂不能形成判断', rationale: '原运行记录等待核对。', reliability: 'unavailable', trade_authorized: false, artifact_status: 'pending', data_status: 'insufficient', accepted_evidence_count: 0, stale_evidence_count: 0, rejected_evidence_count: 0, blocking_gap_ids: [], warning_codes: [error.error_code], supporting_evidence_refs: [], next_review_at: null },
    process_steps: [{ step_id: 'step-plan', order: 1, kind: 'plan', label: '制定研究计划', status: 'running', objective: '核对原任务', outcome: '等待恢复核对', evidence_refs: [], tool_call_refs: [], warning_codes: [], occurred_at: at }],
    total_tool_calls: 0, total_subagents: 0, total_tokens: null, estimated_cost_usd: null, scheduled_recheck_at: null,
  })
}

it('presents preparation waiting from the current saved trace without declaring failure or completed research', async () => {
  const sample = preparationWaitingDetail(), original = JSON.stringify(sample), command = vi.fn()
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  try {
    await act(async () => root.render(<ResearchRunDetail detail={sample} initialDetailView="technical" commandReason="原操作理由" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
    expect(container.querySelector('[aria-label="研究判断摘要"] h2')?.textContent).toBe('等待恢复核对')
    expect(container.querySelector('.research-failure h3')?.textContent).toBe('执行准备尚未确认')
    expect(container.querySelector('.research-trace strong')?.textContent).toBe('执行准备尚未确认')
    expect(container.querySelector('.stop-reason > span')?.textContent).toBe('当前任务状态')
    expect(container.querySelector('.stop-reason > strong')?.textContent).toBe('等待恢复核对')
    const audit = container.querySelector<HTMLDetailsElement>('.research-trace details')!
    expect(audit.open).toBe(false)
    await act(async () => audit.querySelector<HTMLElement>('summary')!.click())
    expect(JSON.parse(audit.querySelector('pre')!.textContent!)).toEqual(sample.trace[0])
    await act(async () => root.render(<ResearchRunDetail detail={sample} initialDetailView="result" commandReason="原操作理由" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
    expect(container.querySelector('.research-refresh-actions')!.textContent).not.toContain('任务会继续查证并保存报告')
    expect(container.querySelector('.research-refresh-actions')!.textContent).toContain('等待恢复核对')
    expect(command).not.toHaveBeenCalled()
    expect(JSON.stringify(sample)).toBe(original)
  } finally { await act(async () => root.unmount()) }
})

it('keeps cached preparation waiting explicitly unconfirmed after the real detail query refresh fails', async () => {
  const sample = preparationWaitingDetail(), original = JSON.stringify(sample)
  window.history.replaceState(null, '', '#/report?run=run-pending&back=%23%2Fruns')
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
  vi.spyOn(api, 'researchRuns').mockResolvedValue([sample.run])
  vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
  vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
  vi.spyOn(api, 'researchObservability').mockRejectedValue(new Error('controlled diagnostics unavailable'))
  vi.spyOn(api, 'researchValueEvaluation').mockResolvedValue(null)
  vi.spyOn(api, 'researchForecastAudit').mockResolvedValue([])
  vi.spyOn(api, 'researchRunComparison').mockRejectedValue(new Error('controlled comparison unavailable'))
  vi.spyOn(api, 'researchRunScope').mockRejectedValue(new Error('controlled scope unavailable'))
  const read = vi.spyOn(api, 'researchRun').mockResolvedValueOnce(sample).mockRejectedValue(new Error('controlled offline'))
  const submit = vi.spyOn(api, 'submitResearch'), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
  const settleQueries = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 35)) }) }
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>))
    await settleQueries(); await settleQueries()
    expect(container.querySelector('[aria-label="研究判断摘要"] h2')?.textContent).toBe('等待恢复核对')
    await act(async () => { await client.refetchQueries({ queryKey: ['research-run', sample.run.run_id], exact: true }) })
    await settleQueries()
    expect(container.querySelector('[aria-label="研究判断摘要"] h2')?.textContent).toBe('最新任务状态暂时无法确认')
    expect(container.textContent).toContain('下方保留这次研究上次读取的内容，当前状态尚未确认')
    expect(container.querySelector('[aria-label="研究判断摘要"]')?.textContent).not.toContain('正在取证与复核')
    expect(read).toHaveBeenCalledTimes(2); expect(submit).not.toHaveBeenCalled()
    expect(window.location.hash).toBe('#/report?run=run-pending&back=%23%2Fruns')
    expect(JSON.stringify(sample)).toBe(original)
  } finally { await act(async () => root.unmount()); client.clear() }
})

it.each(['researching', 'failed'] as const)('keeps ordinary %s errors separate from the new preparation waiting explanation', async status => {
  const sample = preparationWaitingDetail()
  sample.run.status = status; sample.run.stage = status === 'failed' ? 'done' : 'planning'
  sample.run.failure!.cause_code = 'a-different-binding-failure'
  sample.trace[0].error!.cause_code = 'a-different-binding-failure'
  sample.trace[0].status = status === 'failed' ? 'failed' : 'running'
  sample.action_summary.artifact_status = status === 'failed' ? 'rejected' : 'pending'
  container = document.createElement('div'); document.body.append(container); const root = createRoot(container), command = vi.fn()
  try {
    await act(async () => root.render(<ResearchRunDetail detail={sample} initialDetailView="technical" commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
    expect(container.querySelector('.research-failure h3')?.textContent).toBe(status === 'failed' ? '失败原因与来源' : '执行问题与来源')
    expect(container.querySelector('[aria-label="研究判断摘要"] h2')?.textContent).not.toBe('等待恢复核对')
    expect(container.querySelector('.research-failure details')?.textContent).toContain('a-different-binding-failure')
    expect(command).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()) }
})


it('renders scoped market terms in the actual report while preserving numbers, negation, citation and closed originals', async () => {
  const original = '盘口代理orderbook imbalance为0.3628，变化-0.0173%；日线策略回执为bullish，对照回执为bearish；该证据被标记为stale，声明为stale；尚未形成accepted事实。截至2026-09-30T00:22:00Z，原材料仅供研究。'
  const sample = { ...detail, causal_case: { ...detail.causal_case!, main_chain: [link('scoped-market-original', original)] } }
  const originalDto = JSON.stringify(sample), command = vi.fn()
  const href = '#/report?run=run-research-1&back=%23%2FInbox%3Fbefore%3D2026-10-02T03%253A16%253A32.521Z'
  window.history.replaceState(null, '', href)
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  try {
    await act(async () => root.render(<ResearchRunDetail detail={sample} commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
    const statement = container.querySelector('[aria-label="本次综合研究"] > p:nth-child(2)')!
    expect(statement.textContent).toContain('盘口代理买卖盘失衡度为0.3628，变化-0.0173%')
    expect(statement.textContent).toContain('回执为偏多，对照回执为偏空')
    expect(statement.textContent).toContain('被标记为已过时，声明为已过时')
    expect(statement.textContent).toContain('尚未形成已采纳事实')
    expect(statement.textContent).toContain('2026-09-30T00:22:00Z')
    expect(statement.querySelector('a')?.getAttribute('href')).toBe(evidenceRouteHref(sample.run.run_id, 'ev-1'))
    const audit = container.querySelector<HTMLDetailsElement>('.research-summary-original')!
    expect(audit.open).toBe(false)
    await act(async () => audit.querySelector<HTMLElement>('summary')!.click())
    expect(audit.open).toBe(true); expect(audit.textContent).toContain(original)
    expect(JSON.stringify(sample)).toBe(originalDto)
    expect(window.location.hash).toBe(href); expect(command).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()) }
})

describe('scoped market terms boundaries', () => {
  it('translates delta and risk-off in prose while retaining explicit source identifiers and URLs', () => {
    const original = '未平仓量 delta -0.0173%，risk-off尚未确认；source_id="risk-off" reference_id="delta" https://example.test/risk-off?delta=1 custom_delta_v9'
    expect(researchDisplayText(original)).toBe('未平仓量 变化量 -0.0173%，避险情绪尚未确认；source_id="risk-off" reference_id="delta" https://example.test/risk-off?delta=1 custom_delta_v9')
  })
  it('does not apply new rules inside explicit identifiers and URLs', () => {
    const original = 'https://example.test/回执为bullish?status=stale&format=accepted事实 source_id="orderbook imbalance" reference_id="回执为bullish" run_orderbook_imbalance custom_stale_v9 https://example.test/orderbook%20imbalance'
    expect(researchDisplayText(original)).toBe(original)
  })
  it('keeps unscoped statuses, longer codes, numerical values and dates unchanged', () => {
    const original = '原记录：bullish / bearish / stale / accepted / unknown_status_v9 / orderbook imbalance_v9 / pre_orderbook imbalance；0.3628 -0.0173% 2026-09-30T00:22:00Z'
    expect(researchDisplayText(original)).toBe(original)
    expect(researchDisplayText('orderbook imbalance remains unverified')).toBe('orderbook imbalance remains unverified')
  })
  it('keeps opaque URLs unchanged and folds known evidence tokens in displayed prose', () => {
    expect(researchDisplayText('OI / ev_abcdef0123456789abcdef0123456789 / https://example.test/OI')).toBe('未平仓量 / 〔依据见原文〕 / https://example.test/OI')
    expect(researchDisplayText('买方深度 185332 contracts，book_imbalance/crowding_signal 0.892949')).toBe('买方深度 185332 张合约，买卖盘失衡度/拥挤度指标 0.892949')
  })
})

describe('default report followup and evidence destinations', () => {
  const plan: ReviewScheduleView = {
    schema_version: 'review-schedule-view.v1', review_plan_id: 'plan-failed-child',
    parent_run_id: detail.run.run_id, horizon: '30m', trigger_type: 'next_review',
    trigger_ref: now, trigger_at: now, status: 'failed', child_run_id: 'run-original-child',
    failure_code: 'research_observation_unavailable', updated_at: now,
  }
  it('shows the saved failed followup outside technical folds and opens only its original child', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const command = vi.fn(), refresh = vi.fn(), original = JSON.stringify(plan)
    const back = researchRouteHref({ name: 'report', runId: detail.run.run_id, back: '#/home' })
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ResearchRunDetail detail={detail} commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} followup={<ReportFollowup runId={detail.run.run_id} plans={[plan]} loading={false} failed={false} refreshing={false} back={back} onRefresh={refresh} />} />))
      const panel = container.querySelector<HTMLElement>('[aria-label="本报告的后续复查"]')!
      expect(panel.closest('details')).toBeNull()
      expect(panel.textContent).toContain('失败')
      expect(panel.textContent).toContain('执行原始记录尚未核验通过')
      expect(panel.textContent).toContain('已有任务与材料继续保留')
      const child = panel.querySelector('a')!, params = new URLSearchParams(child.getAttribute('href')!.split('?')[1])
      expect(params.get('run')).toBe(plan.child_run_id); expect(params.get('back')).toBe(back)
      expect(child.textContent).toContain('查看失败原因与恢复')
      await act(async () => panel.querySelector('button')!.click())
      expect(refresh).toHaveBeenCalledTimes(1); expect(command).not.toHaveBeenCalled()
      expect(JSON.stringify(plan)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
  it('keeps a failed plan read visibly unconfirmed and rejects a different parent Run', async () => {
    const refresh = vi.fn()
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ReportFollowup runId={detail.run.run_id} plans={[{ ...plan, parent_run_id: 'other-run' }]} loading={false} failed refreshing={false} back="#/home" onRefresh={refresh} />))
      expect(container.textContent).toContain('暂时读取失败')
      expect(container.textContent).toContain('与本报告不一致')
      expect(container.querySelector('a')).toBeNull()
      expect(container.textContent).not.toContain('尚未登记自动检查计划')
      await act(async () => container!.querySelector('button')!.click())
      expect(refresh).toHaveBeenCalledTimes(1)
    } finally { await act(async () => root.unmount()) }
  })
  it('does not call a report suggestion an automatic plan when the authoritative plan list is empty', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const command = vi.fn()
    const legacyHorizonDetail = { ...detail, action_summary: { ...detail.action_summary, research_outlook: null } }
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ResearchRunDetail detail={legacyHorizonDetail} commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} followup={<ReportFollowup runId={detail.run.run_id} plans={[]} suggestedReviewAt={detail.action_summary.next_review_at} loading={false} failed={false} refreshing={false} back="#/report" onRefresh={vi.fn()} />} />))
      const followup = container.querySelector('[aria-label="本报告的后续复查"]')!
      expect(followup.textContent).toContain('报告建议复查时间')
      expect(followup.textContent).toContain('尚未登记自动检查计划')
      expect(followup.textContent).not.toContain('已计划')
      expect(followup.querySelector('a')).toBeNull()
      expect(container.querySelector('.research-action-review')?.textContent).toContain('报告建议复查时间')
      expect([...container.querySelectorAll('.horizon-grid dt')].filter(label => label.textContent === '报告建议复查时间')).toHaveLength(detail.horizons.length)
      expect(container.querySelector('.horizon-grid')?.textContent).not.toContain('下一次复查')
      expect(container.textContent).not.toContain('这版报告的复查计划')
      expect(command).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()) }
  })
  it('distinguishes deferred scheduling, a child receipt, and stopped windows without presenting them as execution failures', async () => {
    const plans = [
      { ...plan, review_plan_id: 'plan-budget', status: 'planned' as const, child_run_id: null, failure_code: 'research_admission_budget_exhausted' },
      { ...plan, review_plan_id: 'plan-active-study', status: 'planned' as const, child_run_id: null, failure_code: 'study_research_active' },
      { ...plan, review_plan_id: 'plan-unknown-host', status: 'planned' as const, child_run_id: null, failure_code: 'study_host_terminal_unknown' },
      { ...plan, review_plan_id: 'plan-created', status: 'admitted' as const, failure_code: null },
      { ...plan, review_plan_id: 'plan-expired', status: 'skipped' as const, child_run_id: null, failure_code: 'research_review_window_expired' },
      { ...plan, review_plan_id: 'plan-consumed', status: 'skipped' as const, child_run_id: null, failure_code: 'review_trigger_already_consumed' },
      { ...plan, review_plan_id: 'plan-unknown', status: 'planned' as const, child_run_id: null, failure_code: 'unknown_schedule_reason' },
    ]
    const original = JSON.stringify(plans), refresh = vi.fn()
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ReportFollowup runId={detail.run.run_id} plans={plans} loading={false} failed={false} refreshing={false} back="#/report?run=run-research-1" onRefresh={refresh} />))
      const cards = [...container.querySelectorAll('.review-plan-grid article')]
      expect(cards.slice(0, 3).map(card => card.querySelector('.review-plan-status')?.textContent)).toEqual(['已延后', '已延后', '已延后'])
      expect(cards[0].textContent).toContain('预算不足')
      expect(cards[1].textContent).toContain('同一主题已有研究进行中')
      expect(cards[2].textContent).toContain('结束状态尚未确认')
      expect(cards[2].textContent).toContain('同一主题')
      expect(cards[2].textContent).not.toContain('原研究任务的结束状态')
      expect(cards.slice(0, 3).every(card => !card.querySelector('a') && !card.textContent?.includes('复查失败'))).toBe(true)
      expect(cards[3].querySelector('.review-plan-status')?.textContent).toBe('复查任务已创建')
      expect(cards[3].querySelector('a')?.textContent).toContain('查看复查进度')
      expect(cards[3].textContent).not.toContain('正在执行')
      expect(cards[4].textContent).toContain('适用时间已结束')
      expect(cards[5].textContent).toContain('停止这项重复计划')
      expect(cards[6].querySelector('.review-plan-status')?.textContent).toBe('等待调度确认')
      expect(cards.every(card => card.querySelector('time')?.dateTime === now)).toBe(true)
      expect(cards.every(card => !card.querySelector<HTMLDetailsElement>('details')?.open)).toBe(true)
      expect(JSON.stringify(plans)).toBe(original)
      expect(refresh).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()) }
  })
  it('links only a verified successor pair and retains the original report and child without any command', async () => {
    const successor = reviewScheduleViewSchema.parse({ ...plan, status: 'skipped', failure_code: 'superseded_by_newer_tracking_basis', replacement_run_id: 'run-successor', replacement_review_plan_id: 'plan-successor' })
    const unverified = { ...successor, review_plan_id: 'plan-unverified', child_run_id: null, replacement_review_plan_id: null }
    const self = { ...successor, review_plan_id: 'plan-self', child_run_id: null, replacement_run_id: detail.run.run_id }
    const plans = [successor, unverified, self], original = JSON.stringify(plans), refresh = vi.fn()
    const back = researchRouteHref({ name: 'report', runId: detail.run.run_id, back: '#/runs?q=BTC&offset=50' })
    window.history.replaceState(null, '', back)
    const originalRoute = window.location.hash
    const command = vi.spyOn(api, 'researchCommand'), submit = vi.spyOn(api, 'submitResearch')
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ReportFollowup runId={detail.run.run_id} plans={plans} loading={false} failed={false} refreshing={false} back={back} onRefresh={refresh} />))
      const cards = [...container.querySelectorAll('.review-plan-grid article')]
      expect(cards.every(card => card.querySelector('.review-plan-status')?.textContent === '已由新报告接续')).toBe(true)
      const links = [...cards[0].querySelectorAll<HTMLAnchorElement>('a')]
      expect(links).toHaveLength(2)
      const successorLink = links.find(link => link.textContent?.includes('查看接续报告与后续计划'))!
      const successorParams = new URLSearchParams(successorLink.hash.split('?')[1])
      expect(successorParams.get('run')).toBe(successor.replacement_run_id)
      expect(successorParams.get('back')).toBe(back)
      const child = links.find(link => link !== successorLink)!
      expect(new URLSearchParams(child.hash.split('?')[1]).get('run')).toBe(plan.child_run_id)
      expect(cards.slice(1).every(card => !card.querySelector('a') && card.textContent?.includes('尚无可跳转的接续计划回执'))).toBe(true)
      expect(JSON.parse(cards[0].querySelector('pre')!.textContent!)).toEqual(successor)
      expect(window.location.hash).toBe(originalRoute)
      expect(JSON.stringify(plans)).toBe(original)
      expect(command).not.toHaveBeenCalled(); expect(submit).not.toHaveBeenCalled(); expect(refresh).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()) }
  })
  it('shows saved retry lineage while legacy and incomplete records never invent a retry count or successor', async () => {
    const legacy = reviewScheduleViewSchema.parse(plan)
    const retry = reviewScheduleViewSchema.parse({ ...plan, review_plan_id: 'plan-retry', status: 'planned', child_run_id: null, failure_code: null, retry_of_plan_id: plan.review_plan_id, retry_attempt: 2 })
    const incomplete = { ...retry, review_plan_id: 'plan-incomplete-retry', retry_of_plan_id: null }
    const plans = [legacy, retry, incomplete], original = JSON.stringify(plans)
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ReportFollowup runId={detail.run.run_id} plans={plans} loading={false} failed={false} refreshing={false} back="#/report?run=run-research-1" onRefresh={vi.fn()} />))
      const cards = [...container.querySelectorAll('.review-plan-grid article')]
      expect(cards[0].textContent).not.toContain('次自动重试')
      expect(cards[1].textContent).toContain('第 2 次自动重试')
      expect(cards[1].textContent).toContain('原失败任务与记录继续保留')
      expect(cards[1].querySelector('.review-plan-status')?.textContent).toBe('已计划')
      expect(cards[1].querySelector('a')).toBeNull()
      expect(JSON.parse(cards[1].querySelector('pre')!.textContent!)).toEqual(retry)
      expect(cards[2].textContent).not.toContain('次自动重试')
      expect(container.textContent).not.toContain('查看接续报告与后续计划')
      expect(JSON.stringify(plans)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
  it.each([undefined, 'sources'] as const)('keeps the report identity and return when reading focus=%s', async focus => {
    const back = '#/runs?q=BTC&offset=50', href = researchRouteHref({ name: 'report', runId: detail.run.run_id, back, focus })
    window.history.replaceState(null, '', href)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    vi.spyOn(api, 'researchRuns').mockResolvedValue([])
    vi.spyOn(api, 'researchRun').mockResolvedValue(detail)
    vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
    vi.spyOn(api, 'macroEvents').mockResolvedValue([])
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(api, 'researchRunScope').mockResolvedValue(null)
    vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([plan])
    for (const method of ['researchRunComparison', 'researchObservability', 'researchValueEvaluation', 'researchForecastAudit'] as const) vi.spyOn(api, method).mockRejectedValue(new Error('controlled optional diagnostic'))
    const submit = vi.spyOn(api, 'submitResearch'), command = vi.spyOn(api, 'researchCommand')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<QueryClientProvider client={client}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>))
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
      const sources = container.querySelector<HTMLDetailsElement>('.research-trust-panel > [aria-label="采纳来源与证据"]')!
      expect(sources.open).toBe(focus === 'sources')
      if (focus) expect(document.activeElement).toBe(sources.querySelector('summary'))
      expect(window.location.hash).toBe(href)
      expect(graphBaseRoute(href, detail.run.run_id).back).toBe(href)
      const citation = new URLSearchParams(evidenceRouteHref(detail.run.run_id, 'ev-original').split('?')[1])
      expect(citation.get('run')).toBe(detail.run.run_id); expect(citation.get('back')).toBe(href)
      expect(submit).not.toHaveBeenCalled(); expect(command).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()); client.clear() }
  })
})

describe('saved graph state comparison labels', () => {
  it.each([
    { previous: 'bounded_fallback', current: 'causal', expected: '有保留的图谱结果 → 已形成论据' },
    { previous: 'causal', current: 'bounded_fallback', expected: '已形成论据 → 有保留的图谱结果' },
  ] as const)('renders the actual comparison component for $previous → $current while retaining DTOs and navigation', async ({ previous, current, expected }) => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    const comparison = researchRunComparisonViewSchema.parse({
      schema_version: 'research-run-comparison-view.v1', run_id: detail.run.run_id, previous_run_id: 'run-previous',
      same_event: true, current_status: 'research_only', previous_status: 'degraded',
      current_evidence_count: 12, previous_evidence_count: 6, current_graph_status: current, previous_graph_status: previous,
      current_direction: 'bullish', previous_direction: 'neutral', changed_fields: ['graph_status', 'direction'], compared_at: now,
    })
    const sample = { ...detail, run: { ...detail.run, status: 'research_only' as const }, causal_case: current === 'causal' ? detail.causal_case : null }
    const originalDetail = JSON.stringify(sample), originalComparison = JSON.stringify(comparison), command = vi.fn()
    const href = '#/technical?run=run-research-1&back=%23%2FInbox%3Fbefore%3D2026-10-02T03%253A16%253A32.521Z'
    window.history.replaceState(null, '', href)
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    try {
      await act(async () => root.render(<ResearchRunDetail detail={sample} comparison={comparison} initialDetailView="technical" commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
      const panel = container.querySelector('[aria-label="研究版本比较"]')!
      const graphState = [...panel.querySelectorAll('.run-comparison-grid > div')].find(cell => cell.querySelector('span')?.textContent === '图状态')!
      expect(graphState.querySelector('strong')?.textContent).toBe(expected)
      expect(panel.textContent).not.toContain('bounded_fallback')
      expect(comparison.current_graph_status).toBe(current)
      expect(comparison.previous_graph_status).toBe(previous)
      expect(JSON.stringify(comparison)).toBe(originalComparison)
      expect(JSON.stringify(sample)).toBe(originalDetail)
      expect(window.location.hash).toBe(href)
      expect(command).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()) }
  })
})

describe('technical trace dates and original time basis', () => {
  it('separates frozen business cutoffs from record clocks across dates without sorting or rewriting the saved Run', async () => {
    const early = '2026-10-01T00:21:46.490229Z', created = '2026-10-02T18:51:50.210750Z', cutoff = '2026-10-02T18:58:01.094000Z'
    const snapshot = { schema_version: 'research-snapshot-manifest.v1' as const, snapshot_id: 'snapshot-trigger', run_id: detail.run.run_id, event_id: detail.run.event_id, snapshot_type: 'trigger' as const, generation: 1, parent_snapshot_id: null, cutoff_at: early, snapshot_hash: 'a'.repeat(64), evidence_refs: ['ev-1'], pack_version: 'crypto_macro.v1', created_at: created }
    const trace = [
      { ...detail.trace[0], sequence_no: 1, event_type: 'session_started' as const, occurred_at: early, summary: '原业务时间记录' },
      { ...detail.trace[0], sequence_no: 2, occurred_at: '2026-10-03T18:52:19Z', summary: '后续记录保原顺序' },
      { ...detail.trace[0], sequence_no: 3, occurred_at: '2026-10-02T18:52:19+00:00', summary: '原序不按显示时间重排' },
    ]
    const sample = { ...detail, run: { ...detail.run, created_at: created }, trace, trigger_snapshot: snapshot, decision_snapshot: { ...snapshot, snapshot_id: 'snapshot-decision', snapshot_type: 'decision' as const, generation: 2, parent_snapshot_id: snapshot.snapshot_id, cutoff_at: cutoff } }
    expect(researchRunDetailViewSchema.safeParse(sample).success).toBe(true)
    const original = JSON.stringify(sample), command = vi.fn(), changeView = vi.fn()
    window.history.replaceState(null, '', '#/technical?run=run-research-1&back=%23%2Freport%3Frun%3Drun-research-1')
    const originalRoute = window.location.hash
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ResearchRunDetail detail={sample} initialDetailView="technical" onDetailViewChange={changeView} commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
      const section = container.querySelector('[aria-label="研究轨迹"]')!, context = section.querySelector('.research-trace-time-context')!
      expect(context.textContent).toContain('任务创建')
      expect(context.textContent).toContain('触发快照业务截止')
      expect(context.textContent).toContain('决策快照业务截止')
      expect([...context.querySelectorAll('time')].map(time => time.getAttribute('datetime'))).toEqual([created, early, cutoff])
      expect(section.textContent).toContain('记录时间不等于实际执行开始')
      const rows = [...section.querySelectorAll<HTMLElement>('.research-trace > div')]
      expect(rows.map(row => row.querySelector('time')!.dateTime)).toEqual(trace.map(event => event.occurred_at))
      expect(rows[0].querySelector('time')!.textContent).not.toBe(rows[2].querySelector('time')!.textContent)
      expect(rows.every(row => /本地 UTC[+-]\d{2}:\d{2}/.test(row.querySelector('time')!.textContent!))).toBe(true)
      expect(rows[0].querySelector('.trace-time-notice')?.textContent).toContain('请核对时间口径')
      expect(rows[1].querySelector('.trace-time-notice')).toBeNull()
      expect(rows[2].querySelector('.trace-time-notice')).toBeNull()
      expect(rows.map(row => JSON.parse(row.querySelector('pre')!.textContent!))).toEqual(trace)
      expect(rows.every(row => !row.querySelector<HTMLDetailsElement>('details')!.open)).toBe(true)
      expect(rows.every(row => row.style.gridTemplateColumns === '10px minmax(0, 1fr)')).toBe(true)
      const resultTab = [...container.querySelectorAll('button')].find(button => button.textContent === '用户结果')!
      expect(resultTab).toBeTruthy()
      await act(async () => resultTab.click())
      expect(changeView).toHaveBeenCalledWith('result')
      expect(window.location.hash).toBe(originalRoute)
      expect(command).not.toHaveBeenCalled()
      expect(JSON.stringify(sample)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })

  it('keeps invalid and missing trace times unknown with intact raw records and no inferred early-execution warning', async () => {
    const trace = ['2026-10-01T00:21:46', '2026-02-30T00:21:46Z', '2026-10-01T00:21:46+00:99'].map((occurred_at, index) => ({ ...detail.trace[0], sequence_no: index + 1, occurred_at }))
    const missing = { ...detail.trace[0], sequence_no: 4 }
    Object.defineProperty(missing, 'occurred_at', { value: undefined, enumerable: true })
    trace.push(missing)
    const sample = { ...detail, trace }, original = JSON.stringify(sample), command = vi.fn()
    container = document.createElement('div'); document.body.append(container); const root = createRoot(container)
    try {
      await act(async () => root.render(<ResearchRunDetail detail={sample} initialDetailView="technical" commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
      const section = container.querySelector('[aria-label="研究轨迹"]')!, rows = [...section.querySelectorAll('.research-trace > div')]
      expect(rows).toHaveLength(4)
      expect(rows.every(row => row.querySelector('time')!.textContent!.includes('时间未知'))).toBe(true)
      expect(rows.every(row => !row.querySelector('time')!.hasAttribute('datetime'))).toBe(true)
      expect(section.querySelector('.trace-time-notice')).toBeNull()
      expect(rows.map(row => JSON.parse(row.querySelector('pre')!.textContent!))).toEqual(JSON.parse(JSON.stringify(trace)))
      expect(container.querySelector('.research-detail-status')!.textContent).toContain('有界降级')
      expect(command).not.toHaveBeenCalled()
      expect(JSON.stringify(sample)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
})


describe('report revision feedback integration', () => {
  const report = {
    revision_id: 'revision-r1', study_id: 'study-r1', source_run_id: detail.run.run_id,
    title: '原报告', instrument: 'BTC-USDT-SWAP', strategy_version: 'research.v1',
    publication_status: 'research_only', created_at: now, local_written_at: null,
    external_dispatch_at: null, read_at: null, latest_feedback: null,
  }
  async function renderFeedback(hasReport = true, unconfirmed = false) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<QueryClientProvider client={client}><ResearchRunDetail detail={detail} commandReason="" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={vi.fn()} revisionFeedback={<ReportRevisionFeedback runId={detail.run.run_id} hasReport={hasReport} unconfirmed={unconfirmed} />} /></QueryClientProvider>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    return { root, client }
  }

  it('mounts feedback for a uniquely resolved original Run revision and leaves report actions intact', async () => {
    const original = JSON.stringify(detail)
    const lookup = vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [report], total: 1, offset: 0, limit: 12 })
    vi.spyOn(delivery, 'latestFeedbackEvaluation').mockResolvedValue(revisionFeedbackEvaluationSchema.parse({
      schema_version: 'research-revision-feedback-evaluation.v1', owner_id: productOwner, revision_id: report.revision_id,
      feedback: { feedback_id: 'feedback-r1', request_id: 'original-key', target_type: 'report_revision', target_id: report.revision_id,
        created_by: productOwner, verdict: 'needs_review', notes: '核对原报告', created_at: now },
      status: 'unavailable', reason: 'legacy_feedback_not_bound', confirmation_basis: 'legacy_feedback_receipt_only', binding: null, value: null,
    }))
    const post = vi.spyOn(delivery, 'feedback')
    const { root, client } = await renderFeedback()
    try {
      expect(lookup).toHaveBeenCalledWith({ run: detail.run.run_id, offset: 0, unread: false })
      expect(delivery.latestFeedbackEvaluation).toHaveBeenCalledWith(report.revision_id, detail.run.run_id)
      expect(container!.querySelector('[aria-label="反馈这版报告"]')!.textContent).toContain('未记录当时评估')
      expect(container!.textContent).toContain('更新公开证据')
      expect(container!.textContent).toContain('查看研究过程、证据、审查与预测复盘')
      expect(post).not.toHaveBeenCalled()
      expect(JSON.stringify(detail)).toBe(original)
    } finally { await act(async () => root.unmount()); client.clear() }
  })

  it.each(['missing', 'multiple', 'wrong-run', 'lookup-error'])('does not guess a version or allow feedback after %s', async problem => {
    const lookup = vi.spyOn(delivery, 'reports')
    if (problem === 'lookup-error') lookup.mockRejectedValue(new Error('controlled lookup failure'))
    else lookup.mockResolvedValue({
      items: problem === 'missing' ? [] : problem === 'multiple' ? [report, { ...report, revision_id: 'another-revision' }] : [{ ...report, source_run_id: 'another-run' }],
      total: problem === 'missing' ? 0 : problem === 'multiple' ? 2 : 1, offset: 0, limit: 12,
    })
    const latest = vi.spyOn(delivery, 'latestFeedbackEvaluation'), post = vi.spyOn(delivery, 'feedback')
    const { root, client } = await renderFeedback()
    try {
      expect(container!.textContent).toContain('反馈入口暂时无法核对')
      expect([...container!.querySelectorAll('button')].some(item => item.textContent === '反馈这一版')).toBe(false)
      expect(latest).not.toHaveBeenCalled()
      expect(post).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()); client.clear() }
  })

  it('does not turn stale detail or a task without a report into an actionable feedback version', async () => {
    const lookup = vi.spyOn(delivery, 'reports'), post = vi.spyOn(delivery, 'feedback')
    const { root, client } = await renderFeedback(true, true)
    try {
      expect(container!.textContent).toContain('当前报告记录尚未确认，反馈入口暂时关闭')
      expect(lookup).not.toHaveBeenCalled()
      await act(async () => root.render(<QueryClientProvider client={client}><ReportRevisionFeedback runId={detail.run.run_id} hasReport={false} /></QueryClientProvider>))
      expect(container!.querySelector('[aria-label="反馈这版报告"]')).toBeNull()
      expect(lookup).not.toHaveBeenCalled()
      expect(post).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()); client.clear() }
  })
})


// UI state controls only; these DTOs do not certify Worker execution or command delivery.
it.each([
  ['admitted', '研究请求已接收，等待开始'],
  ['queued', '研究已排队，等待开始'],
  ['retry_wait', '等待重试，原记录仍保留'],
] as const)('describes %s as waiting without claiming active evidence acquisition', async (status, heading) => {
  const sample: ResearchRunDetailView = {
    ...detail,
    run: { ...detail.run, status, artifact_id: null, current_round: 0, coverage: null, failure: null, stop_reason: null },
    action_summary: { ...detail.action_summary, research_outlook: null, artifact_status: 'pending' },
    evidence: [], rounds: [], reviews: [], process_steps: [], trace: [], total_tool_calls: 0,
  }
  const original = JSON.stringify(sample), command = vi.fn()
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  try {
    await act(async () => root.render(<ResearchRunDetail detail={sample} commandReason="原理由" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
    const summary = container.querySelector('[aria-label="研究判断摘要"]')!
    expect(summary.querySelector('h2')?.textContent).toBe(heading)
    expect(summary.textContent).not.toContain('系统正在获取公开资料和行情')
    expect(summary.textContent).not.toContain('正在取证与复核，结论生成中')
    expect(container.querySelector('[aria-label="更新研究"] h3')?.textContent).toBe(heading)
    expect(command).not.toHaveBeenCalled()
    expect(JSON.stringify(sample)).toBe(original)
  } finally { await act(async () => root.unmount()) }
})

it.each([false, true])('distinguishes a locked original command from active processing (busy=%s)', async busy => {
  const sample: ResearchRunDetailView = { ...detail, run: { ...detail.run, status: 'cancelled', artifact_id: null } }
  const original = JSON.stringify(sample), command = vi.fn()
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  try {
    await act(async () => root.render(<ResearchRunDetail detail={sample} commandReason="用户在研究报告页停止本次任务。" setCommandReason={vi.fn()} commandPending commandBusy={busy} commandError={null} onCommand={command} />))
    const button = container.querySelector<HTMLButtonElement>('[aria-label="更新研究"] .owner-command-actions button')!
    expect(button.disabled).toBe(true)
    expect(button.textContent).toBe(busy ? '正在核对操作…' : '请先核对原操作')
    expect(button.textContent).not.toBe('正在提交…')
    await act(async () => button.click())
    expect(command).not.toHaveBeenCalled()
    expect(JSON.stringify(sample)).toBe(original)
  } finally { await act(async () => root.unmount()) }
})


describe('no-report terminal task status and problem entry', () => {
  const sample = (status: ResearchRunDetailView['run']['status'], failure: ResearchRunDetailView['run']['failure'], stopReason: ResearchRunDetailView['run']['stop_reason'] = null): ResearchRunDetailView => ({
    ...detail, run: { ...detail.run, status, artifact_id: null, failure, stop_reason: stopReason }, causal_case: null, horizons: [],
    action_summary: { ...detail.action_summary, artifact_status: 'pending', research_outlook: null, market_view: null },
  })
  async function mount(value: ResearchRunDetailView, overrides: Partial<ComponentProps<typeof ResearchRunDetail>> = {}) {
    const host = document.createElement('div'); container = host; document.body.append(host)
    const root = createRoot(host), command = vi.fn()
    await act(async () => root.render(<ResearchRunDetail detail={value} commandReason="原理由" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} {...overrides} />))
    return { root, command, host, summary: host.querySelector('[aria-label="研究判断摘要"]')! }
  }
  it('explains owner cancellation without market conditions or claiming Provider settlement', async () => {
    const value = sample('cancelled', { ...detail.run.failure!, error_code: 'cancelled_by_owner' }, { code: 'cancelled', detail: '原停止记录', bounded: true, remaining_hard_gaps: [] })
    const original = JSON.stringify(value), { root, command, host, summary } = await mount(value)
    try {
      expect(summary.querySelector('h2')?.textContent).toBe('本次研究已取消')
      expect(summary.textContent).toContain('你已停止本次研究。未生成本次报告')
      expect(summary.textContent).not.toContain('查看失败原因')
      expect(summary.textContent).not.toContain('重新评估条件')
      expect(summary.textContent).not.toContain('报告建议复查时间')
      expect(summary.textContent).not.toMatch(/全树已停|费用已结清|调用免费/)
      expect(host.querySelector('[aria-label="更新研究"] h3')?.textContent).toBe('想继续这项研究？')
      expect(command).not.toHaveBeenCalled()
      expect(JSON.stringify(value)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
  it('does not guess the cancellation actor and opens the original stop record without a command', async () => {
    const value = sample('cancelled', null), original = JSON.stringify(value), changeView = vi.fn()
    const { root, command, host, summary } = await mount(value, { onDetailViewChange: changeView })
    try {
      expect(summary.textContent).not.toContain('你已停止')
      const button = [...summary.querySelectorAll('button')].find(item => item.textContent === '查看问题详情')!
      await act(async () => button.click())
      // Opening the problem is local; a keyed route remount discards focus.
      expect(changeView).not.toHaveBeenCalled()
      expect(document.activeElement).toBe(host.querySelector('.sufficiency-section'))
      expect(host.querySelector('.stop-reason strong')?.textContent).toBe('结束原因未记录')
      expect(command).not.toHaveBeenCalled()
      expect(JSON.stringify(value)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
  it('shows a known readable failure and locates the preserved technical error on demand', async () => {
    const value = sample('failed', detail.run.failure), original = JSON.stringify(value), { root, command, host, summary } = await mount(value)
    try {
      expect(summary.querySelector('h2')?.textContent).toBe('本次研究未完成')
      expect(summary.textContent).toContain('原因：取证调用超时。')
      expect(summary.textContent).not.toContain('research_capability_timeout')
      const button = [...summary.querySelectorAll('button')].find(item => item.textContent === '查看问题详情')!
      await act(async () => button.click())
      const failure = host.querySelector('.research-failure')!
      expect(document.activeElement).toBe(failure)
      expect(failure.textContent).toContain('research_capability_timeout')
      expect(failure.querySelector<HTMLDetailsElement>('details')?.open).toBe(false)
      expect(command).not.toHaveBeenCalled()
      expect(JSON.stringify(value)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
  it('keeps an unknown failure honest and the original command locked', async () => {
    const value = sample('failed', { ...detail.run.failure!, error_code: 'pc_command_probe_controlled_failure', cause_code: 'unknown_probe', retryable: false })
    const original = JSON.stringify(value), { root, command, host, summary } = await mount(value, { commandPending: true, commandBusy: false })
    try {
      expect(summary.textContent).toContain('具体原因尚无可读说明')
      expect(summary.textContent).not.toContain('pc_command_probe_controlled_failure')
      expect(summary.textContent).not.toContain('unknown_probe')
      expect(summary.textContent).not.toContain('取证调用超时')
      const retry = host.querySelector<HTMLButtonElement>('[aria-label="更新研究"] .owner-command-actions button')!
      expect(retry.disabled).toBe(true)
      expect(retry.textContent).toBe('请先核对原操作')
      await act(async () => retry.click())
      expect(command).not.toHaveBeenCalled()
      expect(JSON.stringify(value)).toBe(original)
    } finally { await act(async () => root.unmount()) }
  })
  it('preserves unknown, preparation-waiting and existing saved report display priority', async () => {
    const value = sample('failed', detail.run.failure), { root, command, host, summary } = await mount(value, { currentStateUnconfirmed: true })
    try {
      expect(summary.querySelector('h2')?.textContent).toBe('最新任务状态暂时无法确认')
      expect(summary.textContent).not.toContain('未生成本次报告。原因')
      await act(async () => root.render(<ResearchRunDetail detail={preparationWaitingDetail()} commandReason="原理由" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
      expect(host.querySelector('.research-action-heading h2')?.textContent).toBe('等待恢复核对')
      await act(async () => root.render(<ResearchRunDetail detail={detail} commandReason="原理由" setCommandReason={vi.fn()} commandPending={false} commandError={null} onCommand={command} />))
      expect(host.querySelector('.research-action-heading h2')?.textContent).toBe('本次 AI 综合判断')
      expect(host.querySelector('[aria-label="日线规则参考"]')).not.toBeNull()
      expect(command).not.toHaveBeenCalled()
    } finally { await act(async () => root.unmount()) }
  })
})


it('routes only typed perpetual terminal recovery to saved settings and retains spot commands', async () => {
  const onCommand = vi.fn()
  const props = {commandReason:'核对原件',setCommandReason:()=>undefined,commandPending:false,commandError:null,onCommand}
  const host=document.createElement('div');document.body.append(host);const root=createRoot(host)
  try {
    for (const status of ['failed','degraded','completed'] as const) {
      const sample={...detail,run:{...detail.run,status,research_profile:'perpetual_decision_v2' as const}}
      await act(async()=>{root.render(<ResearchRunDetail detail={sample} {...props}/> )})
      expect(host.querySelector('a[href="#/settings"]')?.textContent).toContain('按当前已保存设置发起新研究')
      expect([...host.querySelectorAll('button')].some(x=>['更新公开证据','重新尝试研究'].includes(x.textContent??''))).toBe(false)
    }
    await act(async()=>{root.render(<ResearchRunDetail detail={{...detail,run:{...detail.run,status:'completed',event_title:'BTC perpetual title is not authority'}}} {...props}/> )})
    expect([...host.querySelectorAll('button')].some(x=>x.textContent==='更新公开证据')).toBe(true)
    const sample={...detail,run:{...detail.run,status:'failed' as const,research_profile:'perpetual_decision_v2' as const}}
    vi.spyOn(api,'run').mockResolvedValue({run_id:detail.run.run_id,strategy_version:'research.v1',status:'failed',updated_at:now,error_code:'failed'} as Awaited<ReturnType<typeof api.run>>)
    vi.spyOn(api,'researchRun').mockResolvedValue(sample)
    const command=vi.spyOn(api,'researchCommand').mockRejectedValue(new Error('must not dispatch legacy command'))
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}})
    await act(async()=>{root.render(<QueryClientProvider client={client}><DesignStatePage scene="failed" params={new URLSearchParams({run:detail.run.run_id})} onNavigate={vi.fn()}/></QueryClientProvider>)})
    for(let i=0;i<20&&!host.querySelector('a[href="#/settings"]');i++) await act(async()=>{await new Promise(resolve=>setTimeout(resolve,10))})
    expect(host.querySelector('a[href="#/settings"]')?.textContent).toContain('按当前已保存设置发起新研究')
    expect([...host.querySelectorAll('button')].some(x=>x.textContent==='重新尝试研究')).toBe(false)
    expect(command).not.toHaveBeenCalled()
    await act(async()=>{root.render(<AssetBackLink back="#/settings"/> )})
    expect(host.querySelector('a')?.textContent).toContain('返回建议与模拟')
    expect(onCommand).not.toHaveBeenCalled()
  } finally {await act(async()=>root.unmount());host.remove()}
})

it('explains the actual model-preparation timeout for typed perpetual saved-settings research without exposing generated task text', async () => {
  const runId = 'run_7881016135b84b618ca92a8e04018b1e'
  const rawTitle = 'Automatic saved-settings perpetual research'
  const rawFocus = 'perpetual-research:original-settings-reference'
  const errorCode = 'dsh_model_binding_budget_elapsed'
  const sample = researchRunDetailViewSchema.parse({ ...detail, research_focus: rawFocus,
    run: { ...detail.run, run_id: runId, event_title: rawTitle, research_profile: 'perpetual_decision_v2',
      status: 'failed', artifact_id: null, failure: { error_code: errorCode, cause_code: errorCode,
        origin: 'orchestration', retryable: false, capability_id: null, tool_call_id: null, deadline_ms: null },
    },
  })
  const original = JSON.stringify(sample)
  vi.spyOn(api, 'run').mockResolvedValue({ run_id: runId, strategy_version: 'research.v1', status: 'failed',
    updated_at: now, error_code: errorCode } as Awaited<ReturnType<typeof api.run>>)
  vi.spyOn(api, 'researchRun').mockResolvedValue(sample)
  vi.spyOn(api, 'researchLinkedCommandReceipt').mockResolvedValue(null)
  const command = vi.spyOn(api, 'researchCommand'), submit = vi.spyOn(api, 'submitResearch')
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><DesignStatePage scene="failed" params={new URLSearchParams({ run: runId })} onNavigate={vi.fn()} /></QueryClientProvider>))
    for (let i = 0; i < 20 && !host.querySelector('a[href="#/settings"]'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(host.querySelector('textarea')?.value).toBe('按已保存设置发起的永续研究')
    const audit = [...host.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === '原始任务记录')!
    expect(audit.open).toBe(false)
    expect(audit.textContent).toContain(rawTitle)
    expect(audit.textContent).toContain(rawFocus)
    const primary = host.cloneNode(true) as HTMLElement
    primary.querySelectorAll('details').forEach(node => node.remove())
    expect(primary.textContent).not.toContain(rawTitle)
    expect(primary.textContent).not.toContain(rawFocus)
    expect(primary.textContent).not.toContain('未保存单独的研究问题')
    expect(primary.textContent).toContain('无需手动填写研究问题')
    expect(primary.textContent).toContain('研究在模型准备核验阶段超时，未完成本次分析')
    expect(primary.textContent).toContain('确认问题修复后')
    expect(host.querySelector('a[href="#/settings"]')?.textContent).toContain('按当前已保存设置发起新研究')
    expect([...host.querySelectorAll('button')].some(node => node.textContent === '重新尝试研究')).toBe(false)
    expect(command).not.toHaveBeenCalled(); expect(submit).not.toHaveBeenCalled()
    expect(JSON.stringify(sample)).toBe(original)
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})
