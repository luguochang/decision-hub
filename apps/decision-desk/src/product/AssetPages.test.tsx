// @vitest-environment jsdom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FactVersionView, StudyView, AnalyticalOutlookView, AnalyticalCohortView, AnalyticalOutlookDetailView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { api } from '../api/client'
import { FactsPage, factValue, factUnit } from './FactsPage'
import { DataJobsPage } from './DataJobsPage'
import { DataCoveragePanel } from './DataCoveragePanel'
import { ResultsPage } from './ResultsPage'
import { assetTime, instrumentName } from './assetFormat'
import { StudiesPage } from './StudiesPage'
import { SourceHistoryPanel, PollCaptureLinks, CaptureReadback } from './SourceHistoryPanel'
import { RuntimePanel } from './RuntimePanel'
import { ResearchCostPanel } from './ResearchCostPanel'
import { EventArchivePanel } from './EventArchivePanel'
import { ResearchMarketPanel } from './ResearchMarketPanel'
import type { ResearchRunDetailView } from '../api/types'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const time = '2026-09-29T12:00:00Z'
const fact = (id: string, key: string, value: number, known = time): FactVersionView => ({
  fact_version_id: id, semantic_key: key, known_at: known, recorded_at: known,
  market: 'crypto', scope_instrument: 'BTC-USDT', parsed_version_id: 'parsed1', parser_version: 'v1', content_ref: null, acquisition_receipt_id: null,
  fact: { schema_version: 'fact-envelope.v1', fact_id: id, evidence_id: 'ev1', requirement_id: 'spot', metric_family: 'spot', field: 'price', value, unit: 'USDT', instrument: 'BTC-USDT', venue: null, source_id: 'test-market', observed_at: known, received_at: known, published_at: null, window_start_at: null, window_end_at: null, event_offset: null, independence_group: 'market', quality: 'accepted', delay_class: 'realtime', payload_schema_ref: 'spot.v1', payload_hash: 'a'.repeat(64), attributes: {} },
  archive_status: 'captured', raw_captures: [], run_inputs: [], structured_payload_ref: null,
})
const outlook = { outlook_id: 'out-1', run_id: 'run-1', method_family: 'ai_synthesis', instrument: 'BTC-USDT', horizon: '24h', action: 'neutral', window_start_at: time, observation: { status: 'pending', market_return_pct: null, quality_status: null } } as AnalyticalOutlookView
let container: HTMLDivElement, root: Root, client: QueryClient
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) }) }
async function mount(node: ReactNode) {
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container); client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>))
  await settle()
}
beforeEach(() => { vi.spyOn(assets, 'factOptions').mockResolvedValue({ as_of: time, fields: ['price', 'funding_rate'], sources: ['test-market'], instruments: [{ instrument: 'BTC-USDT', metric_family: 'crypto.spot' }, { instrument: 'BTC-USDT', metric_family: 'crypto.derivatives' }] }) })
afterEach(async () => { if (root) await act(async () => root.unmount()); client?.clear(); container?.remove(); vi.restoreAllMocks() })

describe('persisted research asset pages', () => {
  it.each([
    ['long', 'prospective', 'eligible', 'completed', false, '账本记为未命中，需核对原件'],
    ['short', 'prospective', 'eligible', 'completed', true, '账本记为命中，需核对原件'],
    ['neutral', 'prospective', 'eligible', 'completed', null, '不计算方向命中'],
    ['no_trade', 'prospective', 'eligible', 'completed', null, '不计算方向命中'],
    ['long', 'prospective', 'eligible', 'retry', true, '尚未核定'],
    ['long', 'retrospective', 'eligible', 'completed', true, '不纳入前瞻命中'],
  ])('shows saved direction outcome for %s/%s/%s/%s without inventing a hit', async (action, cohort, eligibility, status, correct, expected) => {
    vi.spyOn(assets, 'outlooks').mockResolvedValue({ items: [], total: 0, offset: 0, limit: 12 })
    vi.spyOn(assets, 'cohorts').mockResolvedValue({ items: [], total: 0, offset: 0, limit: 12 })
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(assets, 'outlook').mockResolvedValue({ outlook: { ...outlook, action, cohort, eligibility, fact_version_refs: [], exclusion_reasons: [], trigger: '确认条件', invalidation: '失效条件', observation: { ...outlook.observation, status, direction_correct: correct } }, raw_payload: {}, attempts: [], attempts_total: 0 } as unknown as AnalyticalOutlookDetailView)
    await mount(<ResultsPage params={new URLSearchParams('outlook=out-1')} onNavigate={vi.fn()} />)
    const field = [...container.querySelectorAll('.asset-facts > div')].find(e => e.querySelector('dt')?.textContent === '方向核对')
    expect(field?.querySelector('dd')?.textContent).toBe(expected)
  })

  it('does not claim archive verification when a failed job saved no partitions, and explains errors in Chinese', async () => {
    vi.spyOn(assets, 'jobCoverage').mockResolvedValue({
      schema_version: 'coverage-manifest.v1', job_id: `datajob_${'a'.repeat(32)}`,
      requested_start_at: time, requested_end_at: time, generated_at: time,
      datasets: [{ dataset_id: 'crypto.btc_usdt.ohlcv_1d', instrument: 'BTC-USDT',
        source_id: 'gateio-public', requested_start_at: time, requested_end_at: time,
        unit: 'USDT', frequency: '1d', venue: 'gateio', contract_type: 'spot',
        publication_time_precision: 'second', revision_policy: 'unknown',
        status: 'missing', row_count: 0, partition_refs: [], actual_start_at: null,
        actual_end_at: null, gaps: ['connecterror', 'actual_coverage_does_not_span_requested_window'] }],
    })
    await mount(<DataCoveragePanel jobId="failed-job" names={{ 'crypto.btc_usdt.ohlcv_1d': 'BTC 每日行情' }} />)
    await act(async () => container.querySelector('button')!.click()); await settle()
    expect(container.textContent).toContain('这次作业未保存任何数据分区')
    expect(container.textContent).not.toContain('归档分区已校验')
    expect(container.querySelector('tbody')!.textContent).toContain('无法连接数据来源；实际覆盖未达到请求区间')
    expect(container.querySelector('tbody')!.textContent).not.toContain('connecterror')
    expect(container.querySelector('details')!.textContent).toContain('connecterror')
  })

  it('uses fact market semantics instead of guessing spot from a shared exchange symbol', async () => {
    const derivative = fact('derivative', 'htx:funding', -0.000000013)
    derivative.fact = { ...derivative.fact, metric_family: 'crypto.derivatives', field: 'funding_rate', unit: 'rate', venue: 'htx' }
    const spot = fact('spot', 'spot:price', 80000)
    spot.fact = { ...spot.fact, metric_family: 'crypto.spot', venue: 'okx' }
    vi.spyOn(assets, 'facts').mockResolvedValue({ items: [derivative, spot], total: 2, limit: 20, offset: 0, as_of: time })
    await mount(<FactsPage params={new URLSearchParams({ as_of: time })} onNavigate={vi.fn()} />)
    const rows = [...container.querySelectorAll('tbody tr')].map(row => row.textContent)
    expect(rows[0]).toContain('BTC 衍生品（USDT） · HTX')
    expect(rows[0]).not.toContain('现货')
    expect(rows[0]).toContain('-0.000000013')
    expect(rows[1]).toContain('BTC 现货（USDT） · OKX')
    expect(instrumentName('BTC_USDT', 'crypto.derivatives')).toBe('BTC 衍生品（USDT）')
    expect(instrumentName('BTCUSDT')).toBe('BTC / USDT')
    expect(instrumentName('BTC-USDT-SWAP')).toBe('BTC 永续')
    expect(factUnit('contracts')).toBe('张合约')
    expect(factUnit('yield_percent')).toBe('收益率（%）')
    expect(factValue(null)).toBe('未提供数值')
    expect(factValue(0)).toBe('0')
  })

  it('filters real fact identity and restores an out-of-range page without losing parent context', async () => {
    const read = vi.spyOn(assets, 'facts').mockResolvedValue({ items: [fact('f1', 'key', 0)], total: 1, limit: 20, offset: 0, as_of: time })
    const navigate = vi.fn()
    await mount(<FactsPage params={new URLSearchParams({ as_of: time, fact_instrument: 'BTC-USDT', metric_family: 'crypto.derivatives', source_id: 'test-market', field: 'funding_rate', offset: '999', back: '#/market' })} onNavigate={navigate} />)
    expect(read).toHaveBeenCalledWith(expect.objectContaining({ fact_instrument: 'BTC-USDT', metric_family: 'crypto.derivatives', source_id: 'test-market', field: 'funding_rate', as_of: time }))
    expect(navigate).toHaveBeenCalledWith('facts', expect.objectContaining({ offset: '0', back: '#/market', metric_family: 'crypto.derivatives' }))
    expect(container.textContent).not.toContain('1000–1')
    expect(container.querySelector('a.asset-back-link')?.getAttribute('href')).toBe('#/market')
    const select = container.querySelector<HTMLSelectElement>('[aria-label="品种与指标类别"]')!
    await act(async () => { select.value = JSON.stringify(['BTC-USDT', 'crypto.spot']); select.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(navigate).toHaveBeenLastCalledWith('facts', expect.objectContaining({ fact_instrument: 'BTC-USDT', metric_family: 'crypto.spot', version: undefined, offset: undefined, back: '#/market' }))
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === '清除筛选')!.click())
    expect(navigate).toHaveBeenLastCalledWith('facts', expect.objectContaining({ fact_instrument: undefined, metric_family: undefined, field: undefined, source_id: undefined, as_of: time }))
  })

  it('labels saved fact sources and commodity scope while preserving their exact filter identity', async () => {
    vi.mocked(assets.factOptions).mockResolvedValue({ as_of: time, fields: ['level'], sources: ['yahoo-finance-public', 'future-source'], instruments: [{ instrument: 'GOLD', metric_family: 'cross_asset.commodity' }] })
    vi.spyOn(assets, 'facts').mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, as_of: time })
    const navigate = vi.fn()
    await mount(<FactsPage params={new URLSearchParams({ as_of: time, back: '#/market' })} onNavigate={navigate} />)
    const source = [...container.querySelectorAll('select')].find(select => select.querySelector('option[value="yahoo-finance-public"]'))!
    expect(source.querySelector('option[value="yahoo-finance-public"]')?.textContent).toBe('雅虎财经公开数据')
    expect(source.querySelector('option[value="future-source"]')?.textContent).toBe('其他来源（future-source）')
    await act(async () => { source.value = 'yahoo-finance-public'; source.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(navigate).toHaveBeenLastCalledWith('facts', expect.objectContaining({ source_id: 'yahoo-finance-public', as_of: time, back: '#/market' }))
    const instrument = container.querySelector<HTMLSelectElement>('[aria-label="品种与指标类别"]')!
    expect(instrument.textContent).toContain('黄金相关指标 · 商品指标（口径待核验）')
    expect(instrument.textContent).not.toContain('黄金现货')
    await act(async () => { instrument.value = JSON.stringify(['GOLD', 'cross_asset.commodity']); instrument.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(navigate).toHaveBeenLastCalledWith('facts', expect.objectContaining({ fact_instrument: 'GOLD', metric_family: 'cross_asset.commodity', as_of: time, back: '#/market' }))
  })

  it('formats typed event facts without translating unrelated text or losing the original audit value', async () => {
    const revision = fact('revision', 'event:revision', 0)
    revision.fact = { ...revision.fact, metric_family: 'event.identity', field: 'revision_status', value: 'original', unit: 'text', instrument: null }
    const eventTime = fact('event-time', 'event:time', 0)
    eventTime.fact = { ...eventTime.fact, metric_family: 'event.identity', field: 'event_time', value: '2026-09-29T10:30:00Z', unit: 'datetime', instrument: null }
    const unrelated = fact('unrelated', 'text:note', 0)
    unrelated.fact = { ...unrelated.fact, field: 'event_actor', value: 'original', unit: 'text' }
    const unknownRevision = fact('unknown-revision', 'event:unknown-revision', 0)
    unknownRevision.fact = { ...unknownRevision.fact, field: 'revision_status', value: 'unknown_status', unit: 'text' }
    const invalidTime = fact('invalid-time', 'event:invalid-time', 0)
    invalidTime.fact = { ...invalidTime.fact, field: 'event_time', value: '2026-02-30T10:30:00Z', unit: 'datetime' }
    const dateOnly = fact('date-only', 'event:date-only', 0)
    dateOnly.fact = { ...dateOnly.fact, field: 'event_time', value: '2026-09-29', unit: 'datetime' }
    vi.spyOn(assets, 'facts').mockResolvedValue({ items: [revision, eventTime, unrelated, unknownRevision, invalidTime, dateOnly], total: 6, limit: 20, offset: 0, as_of: time })
    vi.spyOn(assets, 'fact').mockResolvedValue(revision)
    await mount(<FactsPage params={new URLSearchParams({ as_of: time, version: 'revision' })} onNavigate={vi.fn()} />)
    const values = [...container.querySelectorAll('tbody tr')].map(row => row.querySelector('td[data-label="数值 / 单位"]')?.textContent)
    expect(values[0]).toBe('原始发布文本')
    expect(values[1]).toBe(`${assetTime('2026-09-29T10:30:00Z')}日期时间`)
    expect(values[2]).toBe('original文本')
    expect(values[3]).toBe('unknown_status文本')
    expect(values[4]).toBe('2026-02-30T10:30:00Z日期时间')
    expect(values[5]).toBe('2026-09-29日期时间')
    expect(container.querySelector('.asset-detail-title')?.textContent).toContain('修订状态：原始发布')
    expect(container.querySelector('details .asset-json')?.textContent).toContain('"value": "original"')
    expect(revision.fact.value).toBe('original')
  })

  it('rejects invalid cutoff locally and recovers explicitly to the latest data', async () => {
    const read = vi.spyOn(assets, 'facts')
    const navigate = vi.fn()
    await mount(<FactsPage params={new URLSearchParams('as_of=invalid&version=f1&back=%23%2Fresearch')} onNavigate={navigate} />)
    expect(container.textContent).toContain('截止时间无效')
    expect(read).not.toHaveBeenCalled()
    expect(assets.factOptions).not.toHaveBeenCalled()
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === '回到最新数据')!.click())
    expect(navigate).toHaveBeenLastCalledWith('facts', expect.objectContaining({ as_of: expect.stringMatching(/Z$/), version: undefined, back: '#/research' }))
  })

  it('distinguishes a failed fact read from empty results and refreshes the exact cutoff', async () => {
    const read = vi.spyOn(assets, 'facts').mockRejectedValueOnce(new Error('unavailable')).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0, as_of: time })
    await mount(<FactsPage params={new URLSearchParams({ as_of: time, field: 'price' })} onNavigate={vi.fn()} />)
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    expect(container.textContent).not.toContain('没有已保存的指标版本')
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === '重新读取')!.click())
    await settle()
    expect(container.textContent).toContain('没有记录不代表指标为零')
    expect(container.querySelector('[role="alert"]')).toBeNull()
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent?.includes('刷新当前筛选'))!.click())
    await settle()
    expect(read).toHaveBeenLastCalledWith(expect.objectContaining({ as_of: time, field: 'price' }))
  })

  it('restores server-filtered data history and expanded record, preserving cutoff on paging', async () => {
    const job = { schema_version: 'data-job.v1', job_id: 'job-old', dataset_ids: ['macro.fred.dgs2'], status: 'failed', created_at: time, updated_at: time, window_start_at: time, window_end_at: time, finished_at: null, attempt: 2, partition_refs: [], coverage_manifest_ref: null, errors: ['source_unavailable'] } as Awaited<ReturnType<typeof api.dataJobs>>[number]
    const read = vi.spyOn(api, 'dataJobsPage').mockResolvedValue({ items: [job], total: 523 })
    const navigate = vi.fn()
    await mount(<DataJobsPage params={new URLSearchParams(`status=failed&dataset=macro.fred.dgs2&offset=500&job=job-old&before=${time}`)} onNavigate={navigate} />)
    expect(read).toHaveBeenCalledWith({ limit: 20, offset: 500, status: 'failed', dataset: 'macro.fred.dgs2', created_before: time })
    expect(container.querySelector('details.data-job')?.hasAttribute('open')).toBe(true)
    expect(container.textContent).toContain('523 次匹配记录')
    expect(container.textContent).toContain('501–520')
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '下一页')!.click() })
    expect(navigate).toHaveBeenLastCalledWith('data-jobs', expect.objectContaining({ offset: '520', status: 'failed', dataset: 'macro.fred.dgs2', before: time, job: undefined }))
    await act(async () => { container.querySelector('select')!.value = 'completed'; container.querySelector('select')!.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(navigate).toHaveBeenLastCalledWith('data-jobs', expect.objectContaining({ offset: undefined, status: 'completed', job: undefined, before: time }))
  })
  it('does not label a failed data history read as zero records or no failures', async () => {
    vi.spyOn(api, 'dataJobsPage').mockRejectedValue(new Error('503'))
    await mount(<DataJobsPage params={new URLSearchParams()} onNavigate={vi.fn()} />)
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    expect(container.textContent).toContain('记录暂不可读')
    expect(container.textContent).not.toContain('0 次匹配记录')
    expect(container.textContent).not.toContain('无失败记录')
  })

  it('shows backend market admission limits without presenting gold as executable', async () => {
    vi.spyOn(assets, 'researchMarkets').mockResolvedValue({ registry_version: 'scope-test.v1', items: [
      { scope_id: 'gold', label: '黄金宏观研究（待开通）', market: 'metal', instrument: 'XAU-USD', instrument_kind: 'spot', venue: 'provider_not_selected', quote_currency: 'USD', price_unit: 'USD/troy_ounce', source_timezone: 'UTC', calendar_ref: 'not_verified', domain_pack_ref: 'gold_macro.v1', research_enabled: false, outcome_enabled: false, supported_horizons: [], blocking_reasons: ['gold_provider_not_verified', 'gold_outcome_not_verified'] },
    ] })
    await mount(<ResearchMarketPanel />)
    expect(container.textContent).toContain('尚未开通')
    expect(container.textContent).toContain('黄金专属报价来源尚未接入并核验')
    expect(container.textContent).toContain('黄金后续观察与评估尚未接通')
    expect(container.textContent).not.toContain('已开通研究')
    expect(container.querySelector('button')).toBeNull()
  })
  it('separates BTC and ETH spot controlled trials from the static normal admission catalog', async () => {
    const conditions = ['spot_research_disabled', 'spot_effective_recipe_not_verified', 'spot_asset_routes_not_verified', 'spot_current_data_not_verified']
    const catalog = { registry_version: 'research-market-registry.v2', items: ['BTC', 'ETH'].map(asset => ({
      scope_id: `${asset.toLowerCase()}-spot-macro.v1`, label: `${asset} 现货研究（待验证）`,
      market: 'crypto', instrument: `${asset}-USDT`, instrument_kind: 'spot', venue: 'okx',
      quote_currency: 'USDT', price_unit: `USDT/${asset}`, source_timezone: 'UTC', calendar_ref: 'crypto-24x7.v1',
      domain_pack_ref: 'crypto_macro.v1', research_enabled: false, outcome_enabled: false,
      supported_horizons: ['24h', '30m', '72h'], blocking_reasons: [...conditions],
    })) } as Awaited<ReturnType<typeof assets.researchMarkets>>
    const before = JSON.stringify(catalog)
    const read = vi.spyOn(assets, 'researchMarkets').mockResolvedValue(catalog)
    await mount(<ResearchMarketPanel />)
    expect(read).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('本目录展示常规研究开放政策，不代表当前服务、行情时效或实际运行配方已通过验收。')
    for (const article of container.querySelectorAll('.market-scope-card')) {
      expect(article.querySelector('strong')?.textContent).toBe('常规研究尚未开通')
      expect(article.textContent).toContain('现货研究处于受控试运行阶段，完整报告仍待验收。')
      expect(article.textContent).toContain('正式开通需满足以下条件：')
      expect([...article.querySelectorAll('li')].map(item => item.textContent)).toEqual([
        '通过现货 AI 研究的正式准入', '核验现货研究实际运行的配方版本',
        '核验该资产的研究数据与工具路线', '核验完整分钟路径与时点资格',
      ])
      expect(article.textContent).not.toContain('实际运行版本尚未验证')
      expect(article.querySelector('details')?.textContent).toContain('领域规则：crypto_macro.v1')
      expect(article.querySelector('details')?.textContent).toContain('目录版本：research-market-registry.v2')
    }
    expect(container.textContent).not.toContain('常规研究已开通')
    expect(container.querySelector('button')).toBeNull()
    expect(JSON.stringify(catalog)).toBe(before)
  })

  it('previews exact saved capture text lazily, escapes markup and announces truncation', async () => {
    const raw = '<script>bad()</script>' + 'a'.repeat(13000)
    const read = vi.spyOn(assets, 'captureText').mockResolvedValue(raw)
    await mount(<CaptureReadback captureId="raw-1" index={1} />)
    expect(read).not.toHaveBeenCalled()
    await act(async () => container.querySelector('button')!.click())
    await settle()
    expect(read).toHaveBeenCalledWith('raw-1')
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('pre')?.textContent).toBe(raw.slice(0, 12000))
    expect(container.textContent).toContain('当前预览已截断')
    expect(container.querySelector('a')?.getAttribute('href')).toContain('/v1/raw-captures/raw-1/body')
  })
  it('keeps event calendar precision, missing samples, and archive paging context visible', async () => {
    vi.spyOn(assets, 'eventArchive').mockResolvedValue({ macro_event_id: 'event1', occurrence_id: 'o1', vintage_total: 13, sample_total: 1, run_total: 0, runs: [], vintages: [{ vintage_id: 'v1', status: 'released', time_precision: 'date', scheduled_at: null, scheduled_date: '2026-09-29', observed_at: time, recorded_at: time, prior_value: null, consensus_value: null, actual_value: 0, source_timezone: 'America/New_York', payload_hash: 'a'.repeat(64) }], samples: [{ sample_id: 's1', watch_id: 'w1', offset: 't0', watch_scheduled_at: time, watch_status: 'retrospective_only', target_at: time, status: 'missing', observed_at: null, received_at: null, error_code: 'sample_missing', provider_id: null, payload_ref: null }] })
    const navigate = vi.fn()
    await mount(<EventArchivePanel eventId="event1" params={new URLSearchParams('event=event1&back=%23%2Fmarket')} onNavigate={navigate} />)
    expect(container.textContent).toContain('2026-09-29（仅日期，具体时刻未确认）')
    expect(container.textContent).toContain('实际 0')
    expect(container.textContent).toContain('事件时刻 · 数据缺失')
    expect(container.textContent).not.toContain('事件时刻 · 已采集')
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent?.includes('下一页') && !b.disabled)!.click() })
    expect(navigate).toHaveBeenCalledWith('event', { event: 'event1', back: '#/market', vintages: '12' })
  })
  it('keeps failed source days distinct from no records and lazy-loads only the selected attempt files', async () => {
    vi.spyOn(assets, 'sourceHistory').mockResolvedValue({ start_at: '2026-09-16T00:00:00Z', end_at: time, timezone: 'UTC', items: [{ source_id: 'fed', day: '2026-09-29', status: 'failed', attempts: 3, accepted: 0, revisions: 0 }] })
    const read = vi.spyOn(assets, 'pollCaptures').mockResolvedValue([])
    await mount(<><SourceHistoryPanel names={{ fed: '美联储' }} onNavigate={vi.fn()} /><PollCaptureLinks attemptId="attempt-1" /></>)
    expect(container.querySelectorAll('.source-heat-cell.warning')).toHaveLength(1)
    expect(container.querySelectorAll('.source-heat-cell.empty')).toHaveLength(13)
    expect(container.querySelector('.source-heat-cell.warning')?.getAttribute('aria-label')).toContain('失败 3 次')
    expect(read).not.toHaveBeenCalled()
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '查看采集文件')!.click() })
    await settle()
    expect(read).toHaveBeenCalledWith('attempt-1')
    expect(container.textContent).toContain('未保存原始 HTTP 文件')
  })

  it('does not claim a stale process configuration belongs to the current heartbeat', async () => {
    vi.spyOn(api, 'operations').mockResolvedValue({ services: [{ service_id: 'hub-research-worker', role: 'research_worker', instance_id: 'new-instance', heartbeat_at: time, status: 'offline' }] } as Awaited<ReturnType<typeof api.operations>>)
    vi.spyOn(assets, 'runtimeConfigurations').mockResolvedValue([{ service_id: 'hub-research-worker', instance_id: 'old-instance', started_at: time, recorded_at: time, process_id: 123, fingerprint: 'old', settings: { runtime_mode: 'provider', sources_enabled: true } }])
    await mount(<RuntimePanel />)
    expect(container.textContent).toContain('离线')
    expect(container.textContent).toContain('未找到匹配的启动配置')
    expect(container.textContent).not.toContain('在线供应商')
  })

  it('preserves unknown costs and draws concurrent calls on the same time axis', async () => {
    const call = { started_at: time, finished_at: '2026-09-29T12:00:10Z', latency_ms: 10000, cost_usd: null, status: 'succeeded', query_summary: '价格确认', capability_id: 'market.spot' }
    await mount(<ResearchCostPanel detail={{ run: { updated_at: time }, estimated_cost_usd: null, total_tokens: 300, rounds: [{ tool_invocations: [{ ...call, tool_call_id: 'a' }, { ...call, tool_call_id: 'b', cost_usd: 0.02 }] }] } as unknown as ResearchRunDetailView} />)
    expect(container.textContent).toContain('估算费用：未记录')
    expect(container.textContent).toContain('工具费用缺失 1 / 2')
    expect(container.textContent).not.toContain('$0.0000')
    expect([...container.querySelectorAll<HTMLElement>('.research-waterfall-track span')].map(item => item.style.marginLeft)).toEqual(['0%', '0%'])
  })

  it('deduplicates cumulative snapshots and does not invent zero-time durations', async () => {
    const call = { tool_call_id: 'same', started_at: time, finished_at: time, latency_ms: 0, cost_usd: null, status: 'succeeded', query_summary: '历史快照', capability_id: 'dsh.internal.skill' }
    await mount(<ResearchCostPanel detail={{ run: { updated_at: time }, estimated_cost_usd: null, total_tokens: null, rounds: [{ tool_invocations: [call] }, { tool_invocations: [call] }] } as unknown as ResearchRunDetailView} />)
    expect(container.querySelectorAll('.research-waterfall > li')).toHaveLength(1)
    expect(container.querySelectorAll('.research-waterfall-track')).toHaveLength(0)
    expect(container.textContent).toContain('耗时未可靠记录')
    expect(container.textContent).not.toContain('0.0 秒')
    expect(container.textContent).toContain('工具费用缺失 1 / 1')
  })

  it('retains run and method scope through pagination and keeps pending neutral observations unlabelled', async () => {
    const list = vi.spyOn(assets, 'outlooks').mockResolvedValue({ items: [outlook], total: 25, limit: 12, offset: 12 })
    const groups = vi.spyOn(assets, 'cohorts').mockResolvedValue({ items: [{ method_family: 'ai_synthesis', method_version: 'v1', instrument: 'BTC-USDT', horizon: '24h', action: 'neutral', research_quality: 'insufficient', quality_status: null, completed: 0, total: 1, pending: 1, retry: 0, excluded: 0, directional_count: 0, directional_hit_rate: null, distinct_studies: 1 } as AnalyticalCohortView], total: 1, offset: 0, limit: 12 })
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    const navigate = vi.fn()
    await mount(<ResultsPage params={new URLSearchParams('run=run-1&method=ai_synthesis&offset=12')} onNavigate={navigate} />)
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-1', cohort: 'prospective', method_family: 'ai_synthesis', offset: 12 }))
    expect(groups).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'run-1' }))
    expect(container.textContent).toContain('等待到期'); expect(container.textContent).toContain('尚无标签')
    expect(container.textContent).toContain('不计算方向命中'); expect(container.textContent).not.toContain('0.00%')
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '下一页')!.click() })
    expect(navigate).toHaveBeenCalledWith('results', expect.objectContaining({ run: 'run-1', method: 'ai_synthesis', offset: '24' }))
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '查看判断')!.click() })
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ outlook: 'out-1', run: 'run-1' }))
  })

  it('opens frozen evidence at the report cutoff and preserves the exact result return route', async () => {
    const lastMaterialReceived = new Date(Date.parse(time) - 60_000).toISOString()
    vi.spyOn(assets, 'outlooks').mockResolvedValue({ items: [outlook], total: 1, offset: 12, limit: 12 })
    vi.spyOn(assets, 'cohorts').mockResolvedValue({ items: [], total: 0, offset: 0, limit: 12 })
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    vi.spyOn(assets, 'outlook').mockResolvedValue({ outlook: { ...outlook, data_as_of: lastMaterialReceived, fact_version_refs: ['frozen-1'], exclusion_reasons: [], trigger: '确认条件', invalidation: '失效条件', subjective_probability: null }, raw_payload: {}, attempts: [{ attempt: 1, status: 'failed', started_at: time, finished_at: time, error_code: 'market_window_missing', raw_capture_ids: ['raw-observation-1'] }], attempts_total: 1, attempts_limit: 10, attempts_offset: 0 } as unknown as AnalyticalOutlookDetailView)
    const navigate = vi.fn()
    const capture = vi.spyOn(assets, 'captureText').mockResolvedValue('Saved observation candle')
    const params = new URLSearchParams('run=run-1&method=ai_synthesis&offset=12&outlook=out-1')
    await mount(<ResultsPage params={params} onNavigate={navigate} />)
    expect(container.querySelector('.asset-facts dt')?.textContent).toBe('本观点引用材料最晚收到')
    const times = container.querySelectorAll('.asset-facts dd')
    expect(times[0].textContent).toBe(assetTime(lastMaterialReceived))
    expect(times[1].textContent).toBe(assetTime(time))
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === '依据 1')!.click() })
    expect(navigate).toHaveBeenCalledWith('facts', { version: 'frozen-1', as_of: time, back: `#/results?${params.toString()}` })
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent?.trim() === '回到对应报告')!.click() })
    expect(navigate).toHaveBeenLastCalledWith('report', { run: 'run-1', back: `#/results?${params.toString()}` })
    expect(container.textContent).toContain('当次采集未完成')
    expect(container.textContent).not.toContain('稍后重试')
    expect(capture).not.toHaveBeenCalled()
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '查看原始响应 1')!.click() })
    await settle()
    expect(capture).toHaveBeenCalledWith('raw-observation-1')
    expect(container.textContent).toContain('Saved observation candle')
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent?.includes('查看全部'))!.click() })
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ run: undefined, outlook: undefined, offset: undefined }))
  })

  it('surfaces a failed list read and retries the same request without submitting research', async () => {
    const read = vi.spyOn(assets, 'studies').mockRejectedValueOnce(new Error('503')).mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
    const submit = vi.spyOn(api, 'submitResearch')
    await mount(<StudiesPage params={new URLSearchParams('q=gold')} onNavigate={vi.fn()} />)
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '重新读取')!.click() })
    await settle()
    expect(read).toHaveBeenCalledTimes(2); expect(read).toHaveBeenLastCalledWith({ q: 'gold', limit: 12, offset: 0 })
    expect(container.querySelector('[role="alert"]')).toBeNull(); expect(submit).not.toHaveBeenCalled()
  })

  it('requests point-in-time history for one semantic metric and refuses to mix a foreign metric into its chart', async () => {
    const selected = fact('f1', 'BTC:price', 80)
    vi.spyOn(assets, 'fact').mockResolvedValue(selected)
    const read = vi.spyOn(assets, 'facts').mockImplementation(async filters => ({ items: filters.semantic_key ? [selected, fact('f2', 'BTC:price', 90, '2026-09-29T11:00:00Z'), fact('future', 'BTC:price', 9999, '2026-09-29T13:00:00Z'), fact('foreign', 'XAU:price', 99999)] : [selected], total: filters.semantic_key ? 4 : 1, limit: 100, offset: 0, as_of: time }))
    await mount(<FactsPage params={new URLSearchParams({ version: 'f1', as_of: time, back: '#/results?outlook=out-1&offset=12' })} onNavigate={vi.fn()} />)
    await settle()
    expect(read).toHaveBeenCalledWith({ semantic_key: 'BTC:price', as_of: time, limit: 100, offset: 0 })
    expect(container.querySelector('svg[role="img"]')?.getAttribute('aria-label')).toContain('最小 80，最大 90')
    expect(container.querySelector('figcaption')?.textContent).toContain('2 个数值版本')
    expect(container.querySelector('a.asset-back-link')?.getAttribute('href')).toBe('#/results?outlook=out-1&offset=12')
    expect(container.textContent).not.toContain('99999')
  })

  it('restores history and reference pages and previews the exact fact capture', async () => {
    const selected = { ...fact('f1', 'BTC:price', 80), raw_captures: [{ capture_id: 'raw-fact', completed_at: time, status_code: 200 }], run_inputs: Array.from({ length: 41 }, (_, i) => ({ run_id: `run-${i}`, fact_id: `fact-${i}`, use_mode: 'reused' })) } as FactVersionView
    vi.spyOn(assets, 'fact').mockResolvedValue(selected)
    const read = vi.spyOn(assets, 'facts').mockImplementation(async filters => ({ items: [selected], total: filters.semantic_key ? 250 : 1, limit: Number(filters.limit), offset: Number(filters.offset), as_of: time }))
    const raw = vi.spyOn(assets, 'captureText').mockResolvedValue('Saved source response')
    const navigate = vi.fn()
    await mount(<FactsPage params={new URLSearchParams(`version=f1&field=price&history_offset=100&refs_offset=20&as_of=${time}`)} onNavigate={navigate} />)
    await settle()
    expect(read).toHaveBeenCalledWith({ semantic_key: 'BTC:price', as_of: time, limit: 100, offset: 100 })
    expect(container.textContent).toContain('研究 21 ·')
    expect(container.textContent).not.toContain('研究 1 ·')
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent?.includes('研究 21'))!.click() })
    expect(navigate).toHaveBeenLastCalledWith('report', { run: 'run-20', back: `#/facts?${new URLSearchParams({ version: 'f1', field: 'price', history_offset: '100', refs_offset: '20', as_of: time })}` })
    await act(async () => { [...container.querySelectorAll<HTMLButtonElement>('[aria-label="指标版本分页"] button')].find(b => b.textContent === '下一页')!.click() })
    expect(navigate).toHaveBeenLastCalledWith('facts', expect.objectContaining({ history_offset: '200', refs_offset: '20', version: 'f1', as_of: time }))
    expect(raw).not.toHaveBeenCalled()
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '查看原始响应 1')!.click() })
    await settle()
    expect(raw).toHaveBeenCalledWith('raw-fact')
    expect(container.textContent).toContain('Saved source response')
  })

  it('retains cutoff and field on fact pagination and provides the exact report version link', async () => {
    const selected = { ...fact('f1', 'BTC:price', 80), run_inputs: [{ run_id: 'run-original', fact_id: 'fact1', use_mode: 'reused' }] } as FactVersionView
    vi.spyOn(assets, 'fact').mockResolvedValue(selected)
    vi.spyOn(assets, 'facts').mockResolvedValue({ items: [selected], total: 42, limit: 20, offset: 20, as_of: time })
    const navigate = vi.fn()
    await mount(<FactsPage params={new URLSearchParams(`version=f1&field=price&offset=20&as_of=${time}`)} onNavigate={navigate} />)
    await settle()
    expect(container.querySelector('svg[role="img"]')).toBeNull()
    expect(container.textContent).toContain('不足两个可比较')
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent?.includes('研究 1'))!.click() })
    expect(navigate).toHaveBeenLastCalledWith('report', { run: 'run-original', back: `#/facts?${new URLSearchParams({ version: 'f1', field: 'price', offset: '20', as_of: time })}` })
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent === '下一页')!.click() })
    expect(navigate).toHaveBeenLastCalledWith('facts', expect.objectContaining({ offset: '40', field: 'price', as_of: time, version: undefined }))
  })

  it('shows a study delta and navigates to the selected immutable revision rather than the latest run', async () => {
    const study = { study_id: 's1', title: 'Rates and BTC', instrument: 'BTC-USDT', status: 'paused', identity_basis: 'explicit', created_at: '2026-09-01T08:00:00Z', updated_at: time } as StudyView
    vi.spyOn(assets, 'studies').mockResolvedValue({ items: [study], total: 1, limit: 12, offset: 0 })
    vi.spyOn(assets, 'study').mockResolvedValue({ study, total: 1, limit: 8, offset: 0, revisions: [{ revision_id: 'rev1', source_run_id: 'run-historical', created_at: time, recorded_at: time, publication_status: 'publish', strategy_version: 'v1', change: { summary_changed: true, added_evidence_count: 2, removed_evidence_count: 1, current_summary: '本轮收益率走高', previous_summary: '前轮收益率持平', comparison_status: 'compared', evidence_unit: 'distinct_content_hash' } }] } as Awaited<ReturnType<typeof assets.study>>)
    const navigate = vi.fn()
    await mount(<StudiesPage params={new URLSearchParams('study=s1')} onNavigate={navigate} />)
    expect(container.textContent).toContain('目前不自动复查'); expect(container.textContent).toContain('新增材料 2')
    expect(container.textContent).toContain('前轮收益率持平')
    expect(container.querySelector('.study-card small')?.textContent).toBe(`主题登记：${assetTime('2026-09-01T08:00:00Z')}`)
    await act(async () => { [...container.querySelectorAll('button')].find(b => b.textContent?.includes('阅读这一版报告'))!.click() })
    expect(navigate).toHaveBeenCalledWith('report', { run: 'run-historical', back: '#/studies?study=s1' })
  })
  it('explains an ignored topic without claiming active work and preserves full title', async () => {
    const study = { study_id: 'ignored', title: 'A'.repeat(120), instrument: 'BTC-USDT', status: 'paused', identity_basis: 'explicit', updated_at: time } as StudyView
    vi.spyOn(assets, 'studies').mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
    vi.spyOn(assets, 'study').mockResolvedValue({ study, revisions: [], total: 0, limit: 8, offset: 0, activity: { research_runs: 0, active_runs: 0, recent_runs: [], pending_review_plans: 0, next_review_at: null, latest_admission: { run_id: null, study_id: 'ignored', strategy_version: 'research.v1', decision_id: 'd1', attempt_id: 'p1', event_id: 'e1', decision: 'ignored', reason_code: 'no_high_impact_title_match', decided_at: time, details: {}, source_id: 'fed-press', policy_version: 'v3' } } } as Awaited<ReturnType<typeof assets.study>>)
    const navigate = vi.fn()
    await mount(<StudiesPage params={new URLSearchParams('study=ignored&revisions=900&offset=999&back=%23%2Fresearch')} onNavigate={navigate} />)
    expect(container.textContent).toContain('本次未触发研究')
    expect(container.textContent).toContain('目前没有排队或执行中的研究任务')
    expect(container.textContent).toContain('这个主题尚无已保存报告')
    expect(container.textContent).toContain('A'.repeat(120))
    expect(navigate).toHaveBeenCalledWith('studies', expect.objectContaining({ revisions: '0', back: '#/research' }))
    expect(container.querySelector('a.asset-back-link')?.getAttribute('href')).toBe('#/research')
  })
  it.each(['failed', 'cancelled'])('routes a %s study task to recovery with the exact study parent', async (status) => {
    const study = { study_id: 'failed', title: 'Review', instrument: 'BTC-USDT', status: 'paused', identity_basis: 'explicit', updated_at: time } as StudyView
    vi.spyOn(assets, 'studies').mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
    vi.spyOn(assets, 'study').mockResolvedValue({ study, revisions: [], total: 0, limit: 8, offset: 0, activity: { research_runs: 1, active_runs: 0, pending_review_plans: 0, next_review_at: null, latest_admission: null, recent_runs: [{ run_id: 'failed-run', status, created_at: time, updated_at: time, admission_origin: 'automatic', error_code: 'provider_timeout', report_available: false }] } } as Awaited<ReturnType<typeof assets.study>>)
    const navigate = vi.fn()
    await mount(<StudiesPage params={new URLSearchParams('study=failed&back=%23%2Fresearch')} onNavigate={navigate} />)
    expect(container.textContent).toContain('尚无已保存报告')
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === (status === 'cancelled' ? '查看停止原因' : '查看失败原因'))!.click())
    expect(navigate).toHaveBeenLastCalledWith('failed', { run: 'failed-run', back: '#/studies?study=failed&back=%23%2Fresearch' })
  })

  it('drills into every cohort dimension and retains it through observation filtering', async () => {
    const group = { market: 'crypto', cohort: 'prospective', method_family: 'ai_synthesis', method_version: 'method-fixed', instrument: 'BTC-USDT-SWAP', horizon: '30m', action: 'neutral', research_quality: 'sufficient', quality_status: null, total: 3, completed: 0, pending: 3, retry: 0, excluded: 0, directional_count: 0, directional_hit_rate: null, distinct_studies: 1 } as AnalyticalCohortView
    vi.spyOn(assets, 'outlooks').mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
    vi.spyOn(assets, 'cohorts').mockResolvedValue({ items: [group], total: 1, limit: 12, offset: 0 })
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    const navigate = vi.fn()
    await mount(<ResultsPage params={new URLSearchParams('back=%23%2Fresearch&run=run-1')} onNavigate={navigate} />)
    await act(async () => [...container.querySelectorAll('button')].find(b => b.textContent === '查看这组记录')!.click())
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ back: '#/research', run: 'run-1', method: 'ai_synthesis', method_version: 'method-fixed', instrument: 'BTC-USDT-SWAP', horizon: '30m', action: 'neutral', market: 'crypto', research_quality: 'sufficient', quality_status: 'unlabelled', offset: undefined, groups: undefined, outlook: undefined, attempts: undefined }))
  })
  it('pages saved observation attempts without losing selected outlook or report context', async () => {
    vi.spyOn(assets, 'outlooks').mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
    vi.spyOn(assets, 'cohorts').mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 })
    vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
    const read = vi.spyOn(assets, 'outlook').mockResolvedValue({ outlook: { ...outlook, fact_version_refs: [], exclusion_reasons: [], trigger: '确认条件', invalidation: '失效条件', subjective_probability: null }, raw_payload: {}, window: { benchmark: 'saved-window' }, attempts: [], attempts_total: 23, attempts_limit: 10, attempts_offset: 10 } as unknown as AnalyticalOutlookDetailView)
    const navigate = vi.fn()
    await mount(<ResultsPage params={new URLSearchParams('outlook=out-1&attempts=10&method_version=fixed&quality_status=estimated&back=%23%2Fresearch')} onNavigate={navigate} />)
    expect(read).toHaveBeenCalledWith('out-1', { limit: 10, offset: 10 })
    await act(async () => [...container.querySelectorAll<HTMLButtonElement>('[aria-label="后验采集记录分页"] button')].find(b => b.textContent === '下一页')!.click())
    expect(navigate).toHaveBeenLastCalledWith('results', expect.objectContaining({ outlook: 'out-1', attempts: '20', method_version: 'fixed', quality_status: 'estimated', back: '#/research' }))
    expect(container.textContent).toContain('saved-window')
  })

})
