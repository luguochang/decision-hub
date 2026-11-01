// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { RunDrawer } from '../App'
import type { RunInspector } from '../api/types'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const stamp = '2026-10-01T01:00:00Z'
let root: Root, host: HTMLDivElement
afterEach(async () => { await act(async () => root.unmount()); host.remove() })
function fixture(): RunInspector {
  const run: RunInspector['run'] = { run_id: 'run-legacy', event_id: 'event-legacy', status: 'failed', strategy_version: 'research.v1', runtime_version: 'fake.v1', snapshot_id: 'snapshot-legacy', artifact_id: null, created_at: stamp, updated_at: stamp, finished_at: stamp, latency_ms: 0, cost_usd: null, error_code: 'provider_timeout', headline: '历史记录', gate_status: null }
  return {
    run, artifact: null, evaluation_count: 0, evaluations: [], snapshot_cutoff_at: stamp, snapshot_hash: 'a'.repeat(64),
    versions: { strategy_version: 'research.v1', runtime_version: 'fake.v1', pack_version: 'crypto_macro.v1', provider_ids: [], models: [], schema_versions: [], pricing_versions: [] },
    orchestration: { mode: 'fixed_graph', supervisor_role: 'research_supervisor', planned_capabilities: ['policy_delta', 'web.search'], required_capabilities: ['policy_delta', 'web.search'], specialist_coverage: ['policy_delta'], missing_capabilities: ['web.search'], replan_count: 0, experiment_refs: ['experiment-legacy'] },
    steps: [{ step_id: 'step-legacy', run_id: run.run_id, step_name: 'freeze_snapshot', status: 'succeeded', attempt: 1, started_at: stamp, finished_at: stamp, latency_ms: 0, error_code: null }],
    timeline: [{ sequence_no: 1, event_type: 'snapshot.frozen', occurred_at: stamp, reference_type: 'snapshot', reference_id: 'snapshot-legacy' }],
    calls: [{ call_id: 'call-legacy', run_id: run.run_id, role: 'policy_delta', status: 'failed', attempt: 2, started_at: stamp, finished_at: stamp, latency_ms: null, runtime_id: 'dsh', runtime_version: 'legacy-runtime', provider_id: null, model: null, api_mode: 'responses', schema_version: 'agent-payload.v1', prompt_tokens: null, completion_tokens: null, total_tokens: 1234, cost_usd: null, cost_status: 'unknown', pricing_version: null, error_code: 'provider_timeout', retryable: true }],
    evidence_lineage: [{ evidence_id: 'evidence-legacy', source_id: 'fed-speeches', source_type: 'official_feed', observed_at: stamp, published_at: null, received_at: stamp, cutoff_at: stamp, content_hash: 'b'.repeat(64) }],
  }
}
async function mount(inspector: RunInspector | null, flags: { loading?: boolean; failed?: boolean } = {}) {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  const close = vi.fn(), refresh = vi.fn(), report = vi.fn()
  await act(async () => root.render(<RunDrawer selected={inspector?.run ?? fixture().run} inspector={inspector} loading={flags.loading ?? false} failed={flags.failed ?? false} onClose={close} onRefresh={refresh} onReport={report} />))
  return { close, refresh, report }
}
function visibleText() {
  const copy = host.cloneNode(true) as HTMLDivElement
  copy.querySelectorAll<HTMLDetailsElement>('details:not([open])').forEach(details => details.replaceWith(document.createTextNode(details.querySelector('summary')?.textContent ?? '')))
  return copy.textContent ?? ''
}
function button(text: string) { return [...host.querySelectorAll('button')].find(item => item.textContent === text)! }

it('shows known ledger meanings while retaining exact identities, dates, versions and hashes in closed audit records', async () => {
  const inspector = fixture()
  const original = JSON.stringify(inspector)
  await mount(inspector)
  for (const label of ['服务调用超时', '冻结输入快照', '输入快照已保存', '政策变化核查', '标准响应接口', '1,234 个词元', '美联储官员讲话', '官方订阅来源', '搜索公开资料', '耗时 0 毫秒', '耗时未记录', '费用尚未确认']) expect(visibleText()).toContain(label)
  const dates = [...host.querySelectorAll('.lineage-times > div')]
  expect(dates.find(item => item.querySelector('span')?.textContent === '发布时间')?.querySelector('strong')?.textContent).toBe('未记录')
  for (const original of ['fake.v1', 'provider_timeout', 'freeze_snapshot', 'snapshot.frozen', 'policy_delta', 'responses', 'fed-speeches', 'official_feed', inspector.snapshot_hash!, inspector.evidence_lineage[0].content_hash]) expect(visibleText()).not.toContain(original)
  expect([...host.querySelectorAll('details')].every(item => !item.open)).toBe(true)
  const audit = [...host.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === '查看任务与版本原始记录')!
  await act(async () => audit.querySelector<HTMLElement>('summary')!.click())
  expect(audit.open).toBe(true)
  const raw = JSON.parse(audit.querySelector('pre')!.textContent!)
  expect(raw.run).toEqual(inspector.run)
  expect(raw.versions).toEqual(inspector.versions)
  expect(raw.snapshot_hash).toBe(inspector.snapshot_hash)
  expect(raw.snapshot_cutoff_at).toBe(stamp)
  expect(JSON.stringify(inspector)).toBe(original)
})

it('marks unfamiliar fields as needing explanation and never assumes a missing interface is offline', async () => {
  const inspector = fixture()
  inspector.run.error_code = 'custom_error_91'
  inspector.run.headline = 'Original English result without a Chinese summary'
  inspector.steps[0] = { ...inspector.steps[0], step_name: 'custom_step_91', status: 'custom_status_91' }
  inspector.timeline[0] = { ...inspector.timeline[0], event_type: 'custom_event_91', reference_type: 'custom_reference_91' }
  inspector.calls[0] = { ...inspector.calls[0], role: 'custom_role_91', api_mode: null, error_code: 'custom_call_error_91' }
  inspector.evidence_lineage[0] = { ...inspector.evidence_lineage[0], source_id: 'custom_source_91', source_type: 'custom_source_type_91' }
  inspector.orchestration.required_capabilities = ['custom_capability_91']
  await mount(inspector)
  for (const noun of ['异常原因', '步骤名称', '步骤状态', '活动类别', '引用类别', '调用角色', '来源类别', '能力名称']) expect(visibleText()).toContain(`${noun}待解释`)
  expect(visibleText()).toContain('来源名称待解释')
  expect(host.querySelector('h2')?.textContent).toBe('执行记录详情')
  const titleRecord = [...host.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === '核对原始任务与报告标题')!
  expect(titleRecord.open).toBe(false)
  expect(JSON.parse(titleRecord.querySelector('pre')!.textContent!)).toEqual({ task_headline: inspector.run.headline, report_headline: null })
  expect(visibleText()).not.toContain(inspector.run.headline)
  expect(visibleText()).not.toContain('离线调用记录')
  for (const code of ['custom_error_91', 'custom_step_91', 'custom_status_91', 'custom_event_91', 'custom_reference_91', 'custom_role_91', 'custom_call_error_91', 'custom_source_91', 'custom_source_type_91', 'custom_capability_91']) {
    expect(visibleText()).not.toContain(code)
    expect(host.textContent).toContain(code)
  }
})

it('uses a native session receipt instead of a legacy fake version label and keeps refresh, technical entry and close actions', async () => {
  const inspector = fixture()
  inspector.orchestration.mode = 'dsh_native'; inspector.calls = []; inspector.steps = []
  const { refresh, report, close } = await mount(inspector)
  expect(visibleText()).toContain('本次由 DSH 研究会话执行')
  expect(visibleText()).toContain('旧调用账本为空，不代表本次未调用模型')
  expect(visibleText()).not.toContain('确定性测试数据')
  expect(visibleText()).not.toContain('打开研究报告')
  expect(visibleText()).not.toContain('fake.v1')
  await act(async () => button('刷新执行详情').click()); expect(refresh).toHaveBeenCalledOnce()
  await act(async () => button('查看已留存材料与恢复入口').click()); expect(report).toHaveBeenCalledWith('technical')
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="关闭"]')!.click()); expect(close).toHaveBeenCalledOnce()
})

it('preserves an unknown read result and offers refresh without turning missing records into success', async () => {
  const { refresh } = await mount(null, { failed: true, loading: true })
  expect(host.querySelector('[role="status"]')?.textContent).toContain('正在读取')
  expect(visibleText()).toContain('当前详情尚未确认')
  expect(visibleText()).not.toContain('执行完成')
  expect(visibleText()).not.toContain('确定性测试数据')
  await act(async () => button('刷新执行详情').click()); expect(refresh).toHaveBeenCalledOnce()
})

it('keeps explicit test provenance, gates and exact English report content without inventing translations', async () => {
  const inspector = fixture()
  inspector.run.status = 'completed'; inspector.run.error_code = null
  inspector.run.headline = `  用户保存的原始研究问题：${'黄金与宏观市场的历史证据、情景条件和风险边界；'.repeat(30)}  `
  inspector.versions.provider_ids = ['fake']; inspector.versions.models = ['deterministic-fixture']
  inspector.artifact = { artifact_id: 'artifact-legacy', run_id: inspector.run.run_id, event_id: inspector.run.event_id, gate_status: 'publish', headline: 'Original saved report', summary: 'An original English conclusion remains unverified.', facts: ['现货价格已归档'], inferences: [], counter_thesis: '', uncertainty: [], transmission_chain: [], citations: ['evidence-legacy'], created_at: stamp, gate_decisions: [{ rule_id: 'facts_present', status: 'pass', reason_code: 'ok', input_hash: 'c'.repeat(64) }, { rule_id: 'custom_gate_91', status: 'custom_gate_status_91', reason_code: 'custom_gate_reason_91', input_hash: 'd'.repeat(64) }], forecasts: [{ forecast_id: 'forecast-legacy', artifact_id: 'artifact-legacy', instrument: 'BTC', horizon: '30m', direction: 'short', probability: 0.5801234567, trigger: 'Original English trigger', invalidation: 'Original English invalidation', expires_at: stamp }] }
  inspector.artifact.headline = `  ${'用户自定义的报告标题与已保存结论；'.repeat(40)}  `
  const original = JSON.stringify(inspector)
  const { report } = await mount(inspector)
  expect(host.querySelector('.artifact-summary')?.textContent).toContain('报告摘要以英文保存，未提供这段摘要的中文版本')
  expect(host.querySelector('.artifact-summary')?.textContent).not.toContain('材料以英文保存，尚无中文摘要')
  expect(visibleText()).toContain('现货价格已归档')
  expect(host.querySelector('h2')?.textContent).toBe('执行记录详情')
  expect(visibleText()).not.toContain(inspector.run.headline)
  expect(visibleText()).not.toContain(inspector.artifact.headline)
  const titleRecord = [...host.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === '核对原始任务与报告标题')!
  expect(titleRecord.open).toBe(false)
  await act(async () => titleRecord.querySelector<HTMLElement>('summary')!.click())
  expect(titleRecord.open).toBe(true)
  expect(JSON.parse(titleRecord.querySelector('pre')!.textContent!)).toEqual({ task_headline: inspector.run.headline, report_headline: inspector.artifact.headline })
  await act(async () => titleRecord.querySelector<HTMLElement>('summary')!.click())
  expect(visibleText()).toContain('确定性测试数据')
  expect(visibleText()).toContain('原记录概率 58%')
  expect(visibleText()).toContain('校准状态未记录')
  expect(visibleText()).toContain('此项检查通过')
  expect(visibleText()).toContain('检查项目待解释')
  expect(visibleText()).not.toContain(inspector.artifact.summary)
  const reportRecord = [...host.querySelectorAll('details')].find(item => item.querySelector('summary')?.textContent === '查看报告与引用原始记录')!
  expect(reportRecord.open).toBe(false)
  expect(JSON.parse(reportRecord.querySelector('pre')!.textContent!)).toEqual(inspector.artifact)
  expect(JSON.stringify(inspector)).toBe(original)
  await act(async () => button('打开研究报告').click()); expect(report).toHaveBeenCalledWith('report')
})

it('distinguishes recorded zero, a tiny nonzero estimate and missing usage without rounding away cost or score', async () => {
  const inspector = fixture()
  inspector.run.cost_usd = 0
  inspector.calls[0] = { ...inspector.calls[0], total_tokens: null, cost_usd: 0.0000000123, cost_status: 'estimated', latency_ms: 0 }
  inspector.evaluations = [{ evaluation_id: 'evaluation-legacy', forecast_id: 'forecast-legacy', direction_correct: false, brier_score: 0.0000000123, net_return_pct: -0.0000000345, label_status: 'estimated', evaluated_at: stamp }]
  inspector.evaluation_count = 1
  await mount(inspector)
  expect(visibleText()).toContain('记录费用 $0')
  expect(visibleText()).toContain('估算费用 $1.2300e-8')
  expect(visibleText()).toContain('词元未记录')
  expect(visibleText()).toContain('概率评分 1.2300e-8')
  expect(visibleText()).toContain('记录收益 -3.4500e-8%')
  expect(visibleText()).not.toContain('估算费用 $0')
})
