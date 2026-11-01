// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { DeskSummary, RunView, RunInspector } from '../api/types'
import { LegacyLedgerPage } from './LegacyLedgerPage'
import { ledgerRunStatus } from './LedgerRunStatus'
import { AssetBackLink } from './assetFormat'
import { RunDrawer } from '../App'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const run: RunView = { run_id: 'run-failed', event_id: 'event-1', status: 'failed', strategy_version: 'research.v1', runtime_version: 'dsh', snapshot_id: null, artifact_id: null, created_at: '2026-09-30T02:40:00Z', updated_at: '2026-09-30T02:40:00Z', finished_at: null, latency_ms: 0, cost_usd: null, error_code: 'provider_timeout', headline: null, gate_status: null }
const summary: DeskSummary = { inbox: { pending_count: 0, running_count: 0, latest: [run] }, forecast_count: 1677, evaluated_count: 12, published_count_30d: 2, health_status: 'ok', active_strategy: 'baseline.v1', active_pack: 'crypto_macro.v1' }
let root: Root, container: HTMLDivElement, client: QueryClient
beforeEach(() => { vi.spyOn(api, 'ledgerRunsPage').mockResolvedValue({ items: [run], total: 1 }); container = document.createElement('div'); document.body.append(container); root = createRoot(container); client = new QueryClient({ defaultOptions: { queries: { retry: false } } }) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); client.clear(); vi.restoreAllMocks() })
async function mount(element: React.ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}>{element}</QueryClientProvider>)); await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) }) }
function button(text: string) { return [...container.querySelectorAll('button')].find(item => item.textContent === text)! }

describe('legacy ledger accuracy and navigation', () => {
  it('shows failed work, full-history counts, true zero duration and a keyboard-accessible detail action', async () => {
    vi.spyOn(api, 'summary').mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    const inspect = vi.fn(), navigate = vi.fn(), submit = vi.fn()
    await mount(<LegacyLedgerPage onInspect={inspect} onSubmit={submit} onNavigate={navigate} />)
    expect(container.textContent).toContain('执行失败')
    expect(container.textContent).toContain('0.0 秒')
    expect(container.textContent).toContain('全历史')
    expect(container.textContent).not.toContain('待补标签')
    expect(container.textContent).not.toContain('近 30 天预测')
    expect(container.textContent).toContain('来源状态读取失败')
    expect(container.querySelector('time')?.dateTime).toBe(run.updated_at)
    expect(container.querySelector('time')?.textContent).toBe(new Date(run.updated_at).toLocaleString('zh-CN', { hour12: false }))
    await act(async () => button('查看详情').click())
    expect(inspect).toHaveBeenCalledWith(run)
    await act(async () => button('打开运行审计').click())
    expect(navigate).toHaveBeenCalledWith('Operations')
    await act(async () => button('发起研究').click())
    expect(navigate).toHaveBeenCalledWith('compose')
    expect(submit).not.toHaveBeenCalled()
    await act(async () => button('提交事件文本').click())
    expect(submit).toHaveBeenCalledOnce()
  })

  it('persists filters, page and creation boundary when navigating history and opening a record', async () => {
    vi.mocked(api.ledgerRunsPage).mockResolvedValue({ items: [run], total: 67 })
    vi.spyOn(api, 'summary').mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    const navigate = vi.fn(), inspect = vi.fn()
    const before = '2026-09-30T03:00:00Z'
    await mount(<LegacyLedgerPage params={new URLSearchParams({ kind: 'baseline', status: 'failed', offset: '20', before })} onInspect={inspect} onSubmit={vi.fn()} onNavigate={navigate} />)
    expect(api.ledgerRunsPage).toHaveBeenCalledWith({ limit: 20, offset: 20, created_before: before, kind: 'baseline', status: 'failed', q: undefined })
    expect(container.textContent).toContain('21–40')
    await act(async () => button('下一页').click())
    expect(navigate).toHaveBeenLastCalledWith('Inbox', expect.objectContaining({ kind: 'baseline', status: 'failed', offset: '40', before }))
    await act(async () => button('查看详情').click())
    expect(navigate).toHaveBeenLastCalledWith('Inbox', expect.objectContaining({ record: run.run_id, offset: '20', kind: 'baseline', before }))
    await act(async () => button('清除筛选').click())
    expect(navigate).toHaveBeenLastCalledWith('Inbox', expect.objectContaining({ q: undefined, kind: undefined, status: undefined, offset: undefined, before }))
  })

  it('does not describe a native DSH session as a fixed workflow with no model calls', async () => {
    const inspector: RunInspector = { run, timeline: [], steps: [], calls: [], artifact: null, evaluation_count: 0, evaluations: [], snapshot_cutoff_at: null, snapshot_hash: null, evidence_lineage: [], versions: { strategy_version: 'research.v1', runtime_version: 'dsh', pack_version: null, provider_ids: [], models: [], schema_versions: [], pricing_versions: [] }, orchestration: { mode: 'dsh_native', supervisor_role: null, planned_capabilities: [], required_capabilities: [], specialist_coverage: [], missing_capabilities: [], replan_count: 0, experiment_refs: [] } }
    const report = vi.fn()
    await mount(<RunDrawer selected={run} inspector={inspector} loading={false} failed={false} onClose={vi.fn()} onReport={report} />)
    expect(container.textContent).toContain('本次由 DSH 研究会话执行')
    expect(container.textContent).toContain('旧调用账本为空，不代表本次未调用模型')
    expect(container.textContent).not.toContain('固定流程')
    await act(async () => button('查看已留存材料与恢复入口').click())
    expect(report).toHaveBeenCalledWith('technical')
  })

  it('exposes failure and retry rather than an empty successful ledger', async () => {
    vi.spyOn(api, 'summary').mockRejectedValueOnce(new Error('offline')).mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    await mount(<LegacyLedgerPage onInspect={vi.fn()} onSubmit={vi.fn()} onNavigate={vi.fn()} />)
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('账本刷新失败')
    await act(async () => button('重试读取账本').click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(container.textContent).toContain('执行失败')
  })

  it('labels deterministic predictions as test outputs and does not call their probability a verified win rate', async () => {
    const baseline = { ...run, strategy_version: 'baseline.v1', runtime_version: 'fake.v1', status: 'completed' as const }
    const inspector: RunInspector = { run: baseline, timeline: [], steps: [], calls: [], evaluation_count: 1, evaluations: [{ evaluation_id: 'eval-test', forecast_id: 'forecast-test', direction_correct: false, brier_score: 0.1, net_return_pct: -1, label_status: 'estimated', evaluated_at: run.updated_at }], snapshot_cutoff_at: null, snapshot_hash: null, evidence_lineage: [], versions: { strategy_version: 'baseline.v1', runtime_version: 'fake.v1', pack_version: null, provider_ids: ['fake'], models: ['deterministic-fixture'], schema_versions: [], pricing_versions: [] }, orchestration: { mode: 'fixed_graph', supervisor_role: null, planned_capabilities: [], required_capabilities: [], specialist_coverage: [], missing_capabilities: [], replan_count: 0, experiment_refs: [] }, artifact: { artifact_id: 'artifact-test', run_id: run.run_id, event_id: run.event_id, gate_status: 'publish', headline: 'fixture', summary: 'fixture', facts: [], inferences: [], counter_thesis: '', uncertainty: [], transmission_chain: [], citations: [], gate_decisions: [], created_at: run.created_at, forecasts: [{ forecast_id: 'forecast-test', artifact_id: 'artifact-test', instrument: 'BTC', horizon: '30m', direction: 'short', probability: 0.58, trigger: 'fixture trigger', invalidation: 'fixture invalidation', expires_at: run.updated_at }] } }
    await mount(<RunDrawer selected={baseline} inspector={inspector} loading={false} failed={false} onClose={vi.fn()} onReport={vi.fn()} />)
    expect(container.textContent).toContain('确定性测试数据')
    expect(container.textContent).toContain('原记录概率 58%')
    expect(container.textContent).toContain('校准状态未记录')
    expect(container.textContent).toContain('行情估算')
    expect(container.textContent).toContain('偏空')
    expect(container.querySelector('.forecast-row > div strong')?.textContent).toBe('偏空')
    expect(container.querySelector('.forecast-row details')?.hasAttribute('open')).toBe(false)
    expect(container.querySelector('.forecast-row details pre')?.textContent).toContain('"direction": "short"')
    expect(container.querySelector('.evaluation-row details')?.hasAttribute('open')).toBe(false)
  })

  it('keeps report return links and terminal execution status distinct from publication', async () => {
    expect(ledgerRunStatus('failed', 'publish').label).toBe('执行失败')
    expect(ledgerRunStatus('cancelled').label).toBe('已取消')
    expect(ledgerRunStatus('completed').label).toBe('执行完成')
    expect(ledgerRunStatus('admitted').label).toBe('等待执行')
    await mount(<AssetBackLink back="#/Inbox" />)
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#/Inbox')
    expect(container.textContent).toContain('返回业务账本')
  })
})

describe('observed ledger prose labels', () => {
  it('renders the saved Evidence and revision prefixes without changing identifiers, direction fields or original records', async () => {
    const evidence = 'Evidence 已保留，但综合校验未通过，当前不能形成根因结论'
    const first = '中文结论 revision 1: BTC 七日主方向为价格规则偏多/long 候选，但不是执行授权。'
    const revision = first + '事实层面，已收盘日线截至 2026-09-30 显示 close 83616.5 > SMA20 81328.34 > SMA60 75121.66，20 日收益 +9.19%，且 2026-10-01T00:22Z Gate.io 与 Huobi 现货价约 83482/83464 USDT 相互确认。推断层面，这支持 72h 价格规则偏多，但置信度被宏观风险偏紧、负基差、混合 OI 和单场馆卖侧盘口压力压低；30m 与 24h 不给出主动追多执行。交易执行权限未由本研究授予。'
    const opaque = '原文标识 source_id="Evidence 已保留"；https://example.test/Evidence；revision_1；非执行授权。'
    const hashRecord = `原文 SHA-256 ${'a'.repeat(64)}；Evidence_id；非执行授权。`
    const cases = [
      { headline: evidence, expected: '证据已保留，但综合校验未通过，当前不能形成根因结论', excerpt: false },
      { headline: revision, expected: '中文结论（第1次修订）： BTC 七日主方向为价格规则偏多候选，但不是执行授权。', excerpt: true },
      { headline: '中文结论 revision 001: 变化为 -0.58%，不是执行授权。', expected: '中文结论（第001次修订）： 变化为 -0.58%，不是执行授权。', excerpt: false },
      { headline: 'Evidence remains retained, revision 1 is not execution authorization.', expected: '摘要未提供中文预览，请查看详情核对原文。', excerpt: false },
      { headline: opaque, expected: opaque, excerpt: false },
      { headline: hashRecord, expected: hashRecord, excerpt: false },
      { headline: '中文结论 revision 1a: 原版本编号不是整数，仍待核对。', expected: '中文结论 revision 1a: 原版本编号不是整数，仍待核对。', excerpt: false },
      { headline: 'Evidence 已保留_id 是原始标识，尚未核验。', expected: 'Evidence 已保留_id 是原始标识，尚未核验。', excerpt: false },
    ]
    const items: RunView[] = cases.map((item, index) => Object.freeze({ ...run, run_id: `prose-${index}`, headline: item.headline }))
    const original = JSON.stringify(items), inspect = vi.fn(), navigate = vi.fn()
    const values = { before: '2026-10-02T03:16:32.521Z', kind: 'research', q: '原文', back: '#/management' }
    vi.mocked(api.ledgerRunsPage).mockResolvedValue({ items, total: items.length })
    vi.spyOn(api, 'summary').mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    await mount(<LegacyLedgerPage params={new URLSearchParams(values)} onInspect={inspect} onSubmit={vi.fn()} onNavigate={navigate} />)
    const rows = [...container.querySelectorAll('tbody tr')]
    cases.forEach((item, index) => {
      const preview = rows[index].querySelector('.ledger-headline')?.textContent ?? ''
      expect(preview).toBe(item.expected)
      expect(Array.from(preview).length).toBeLessThanOrEqual(120)
      expect(rows[index].querySelector('td:first-child small')?.textContent ?? '').toBe(item.excerpt ? '仅显示首句，完整判断与限制请查看详情。' : '')
    })
    await act(async () => rows[1].querySelector<HTMLButtonElement>('button')!.click())
    expect(inspect.mock.calls[0][0]).toBe(items[1])
    expect(navigate).toHaveBeenLastCalledWith('Inbox', { ...values, record: items[1].run_id })
    expect(JSON.stringify(items)).toBe(original)
    await mount(<RunDrawer selected={items[1]} inspector={null} loading={false} failed={false} onClose={vi.fn()} onReport={vi.fn()} />)
    const raw = [...container.querySelectorAll('.legacy-audit pre')].map(node => JSON.parse(node.textContent!))
    expect(raw.find(value => 'task_headline' in value)?.task_headline).toBe(revision)
    expect(raw.find(value => 'run' in value)?.run).toEqual(items[1])
  })
})

describe('legacy ledger summary previews', () => {
  const notice = '仅显示首句，完整判断与限制请查看详情。'
  const longFallback = '摘要较长，请查看详情阅读完整内容。'
  const first = 'BTC未来7个自然日的价格规则基线偏多，但当前只支持未校准、低到中等置信度的多头候选/持有倾向，不代表交易执行授权。'

  it('renders complete bounded Chinese and honest missing, English and first-sentence previews', async () => {
    const short = '资金费率为 0.000000001%，当前变化为 -0.58%，尚不能据此确认研究有效。'
    const nearCap = '资料'.repeat(45) + '资金费率为 0.000000001%，不是授权。'
    const cases = [
      { headline: short, expected: short, excerpt: false },
      { headline: 'Federal Reserve economic projections remain uncertain.', expected: '摘要未提供中文预览，请查看详情核对原文。', excerpt: false },
      { headline: null, expected: '摘要未记录', excerpt: false },
      { headline: '', expected: '摘要未记录', excerpt: false },
      { headline: '  \n\t ', expected: '摘要未记录', excerpt: false },
      { headline: first + '事实材料继续保留日期与来源。'.repeat(12), expected: first, excerpt: true },
      { headline: nearCap + '后续事实材料继续保留。'.repeat(12), expected: nearCap, excerpt: true },
      { headline: '资料'.repeat(70) + '资金费率为 0.000000001%，并不构成授权。', expected: longFallback, excerpt: false },
      { headline: '原记录支持偏多。更正：上述结论不成立。' + '后续材料保留。'.repeat(20), expected: longFallback, excerpt: false },
      { headline: '原记录支持偏多。上述结论已失效。' + '后续材料保留。'.repeat(20), expected: longFallback, excerpt: false },
      { headline: '完整引文为“记录只用于核对。' + '资料'.repeat(70) + '”。', expected: longFallback, excerpt: false },
      { headline: '资料'.repeat(70) + '收益为 -0.58%，资料仍待核对', expected: longFallback, excerpt: false },
      { headline: '当前变化为 -0.58%，并不构成执行授权。' + '后续风险仍需阅读全文核对。'.repeat(12), expected: '当前变化为 -0.58%，并不构成执行授权。', excerpt: true },
    ]
    const items: RunView[] = cases.map((item, index) => ({ ...run, run_id: `preview-${index}`, headline: item.headline, artifact_id: index === 2 ? 'artifact-existing' : null }))
    vi.mocked(api.ledgerRunsPage).mockResolvedValue({ items, total: items.length })
    vi.spyOn(api, 'summary').mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    await mount(<LegacyLedgerPage onInspect={vi.fn()} onSubmit={vi.fn()} onNavigate={vi.fn()} />)
    const rows = [...container.querySelectorAll('tbody tr')]
    expect(rows).toHaveLength(cases.length)
    cases.forEach((item, index) => {
      const text = rows[index].querySelector('.ledger-headline')?.textContent ?? ''
      expect(text).toBe(item.expected)
      expect(Array.from(text).length).toBeLessThanOrEqual(120)
      expect(rows[index].querySelector('td:first-child small')?.textContent ?? '').toBe(item.excerpt ? notice : '')
      expect(rows[index].children[1].textContent).toContain('执行失败')
    })
    expect(container.querySelector('thead th')?.textContent).toBe('摘要预览')
    expect(container.textContent).not.toContain('尚未生成报告')
    expect(rows[0].textContent).toContain('0.000000001%')
    expect(rows[0].textContent).toContain('-0.58%')
    expect(rows[5].textContent).toContain('未校准')
    expect(rows[5].textContent).toContain('不代表交易执行授权。')
  })

  it('passes the original run object, preserves every route parameter and retains full raw drawer text', async () => {
    const original: RunView = Object.freeze({ ...run, run_id: 'preview-original', headline: 'English original headline with exact probability 0.000000001% and no execution authorization. '.repeat(8) })
    const values = { kind: 'research', status: 'failed', q: '宏观 0.58%', offset: '20', before: '2026-09-30T03:00:00Z', back: '#/management?tab=history', retained: 'original-value' }
    const params = new URLSearchParams(values)
    const originalParams = params.toString()
    vi.mocked(api.ledgerRunsPage).mockResolvedValue({ items: [original], total: 67 })
    vi.spyOn(api, 'summary').mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    const inspect = vi.fn(), navigate = vi.fn()
    await mount(<LegacyLedgerPage params={params} onInspect={inspect} onSubmit={vi.fn()} onNavigate={navigate} />)
    expect(api.ledgerRunsPage).toHaveBeenCalledWith({ limit: 20, offset: 20, created_before: values.before, kind: values.kind, status: values.status, q: values.q })
    const detail = container.querySelector<HTMLButtonElement>(`button[aria-label="查看记录 ${original.run_id}"]`)!
    await act(async () => detail.click())
    expect(inspect.mock.calls[0][0]).toBe(original)
    expect(navigate).toHaveBeenLastCalledWith('Inbox', { ...values, record: original.run_id })
    expect(params.toString()).toBe(originalParams)
    await mount(<RunDrawer selected={original} inspector={null} loading={false} failed={false} onClose={vi.fn()} onReport={vi.fn()} />)
    const raw = [...container.querySelectorAll('.legacy-audit pre')].map(node => JSON.parse(node.textContent!))
    expect(raw.find(value => 'task_headline' in value)?.task_headline).toBe(original.headline)
    expect(raw.find(value => 'run' in value)?.run).toEqual(original)
  })

  it('keeps all existing ledger actions available with their original destinations', async () => {
    vi.spyOn(api, 'summary').mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    const navigate = vi.fn(), submit = vi.fn()
    await mount(<LegacyLedgerPage onInspect={vi.fn()} onSubmit={submit} onNavigate={navigate} />)
    const destinations = [
      ['返回管理与审计', 'management'], ['发起研究', 'compose'],
      ['查看来源与运行状态', 'sources'], ['打开运行审计', 'Operations'],
      ['查看研究效果', 'results'], ['查看经验候选与审核', 'Evolution'],
    ]
    for (const [label, destination] of destinations) {
      await act(async () => button(label).click())
      expect(navigate).toHaveBeenLastCalledWith(destination)
    }
    await act(async () => button('提交事件文本').click())
    expect(submit).toHaveBeenCalledOnce()
  })
})


describe('ledger synonymous direction spelling', () => {
  it('shows one direction only for the observed synonymous prefix while retaining mismatch, unknown and original records', async () => {
    const cases = [
      { headline: '中文结论 revision 1: BTC 七日主方向为价格规则偏多/long 候选，但不是执行授权。', expected: '价格规则偏多候选', rawDirection: 'long' },
      { headline: '中文结论（第2次修订）： BTC 七日主方向为价格规则偏空/short 候选，但不是执行授权。', expected: '价格规则偏空候选', rawDirection: 'short' },
      { headline: '中文结论 revision 1: BTC 七日主方向为价格规则偏多/short 候选，原记录尚待核对。', expected: '价格规则偏多/偏空 候选', rawDirection: null },
      { headline: '中文结论 revision 1: BTC 七日主方向为价格规则偏多/sideways 候选，原记录尚待核对。', expected: '价格规则偏多/sideways 候选', rawDirection: null },
      { headline: 'source_id="中文结论 revision 1: BTC 七日主方向为价格规则偏多/long 候选"；原始标识不是方向证明。', expected: 'source_id=', rawDirection: null },
    ]
    const items: RunView[] = cases.map((item, index) => Object.freeze({ ...run, run_id: `direction-${index}`, headline: item.headline }))
    const before = JSON.stringify(items), inspect = vi.fn(), navigate = vi.fn()
    const values = { kind: 'research', status: 'failed', q: '方向', before: '2026-10-02T03:16:32.521Z', retained: 'original-value' }
    vi.mocked(api.ledgerRunsPage).mockResolvedValue({ items, total: items.length })
    vi.spyOn(api, 'summary').mockResolvedValue(summary)
    vi.spyOn(api, 'health').mockRejectedValue(new Error('offline'))
    await mount(<LegacyLedgerPage params={new URLSearchParams(values)} onInspect={inspect} onSubmit={vi.fn()} onNavigate={navigate} />)
    const rows = [...container.querySelectorAll('tbody tr')]
    cases.forEach((item, index) => {
      expect(rows[index].querySelector('.ledger-headline')?.textContent).toContain(item.expected)
      const audit = rows[index].querySelector('.legacy-direction-audit')
      expect(audit?.querySelector('code')?.textContent ?? null).toBe(item.rawDirection)
      expect(audit?.hasAttribute('open') ?? false).toBe(false)
    })
    expect(rows[0].querySelector('.ledger-headline')?.textContent).not.toContain('偏多/偏多')
    expect(rows[1].querySelector('.ledger-headline')?.textContent).not.toContain('偏空/偏空')
    await act(async () => rows[0].querySelector<HTMLButtonElement>('button')!.click())
    expect(inspect.mock.calls[0][0]).toBe(items[0])
    expect(navigate).toHaveBeenLastCalledWith('Inbox', { ...values, record: items[0].run_id })
    expect(JSON.stringify(items)).toBe(before)
  })
})
