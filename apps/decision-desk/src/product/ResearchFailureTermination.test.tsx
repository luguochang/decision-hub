// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { DesignStatePage } from '../DesignStatePage'
import { api } from '../api/client'
import { researchRunDetailViewSchema, runViewSchema } from '../api/schemas'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const at = '2026-10-18T13:09:59.436755Z'
const systemFallback = 'Research execution stopped without a committable result.'

function retainedTask(runId: string, errorCode: string, causeCode: string, stop: string, status: 'failed' | 'cancelled' = 'failed') {
  const failure = { error_code: errorCode, cause_code: causeCode,
    origin: 'orchestration', retryable: false, capability_id: null, tool_call_id: null, deadline_ms: null, provider_attempts: [] }
  const basic = runViewSchema.parse({ run_id: runId, event_id: 'engineering', status,
    strategy_version: 'research.v1', runtime_version: 'engineering', snapshot_id: null, artifact_id: null,
    created_at: at, updated_at: at, finished_at: at, latency_ms: null, cost_usd: null,
    error_code: errorCode, headline: null, gate_status: null })
  const detail = researchRunDetailViewSchema.parse({
    schema_version: 'research-run-detail-view.v1',
    run: { schema_version: 'research-run-view.v2', run_id: runId, event_id: 'engineering', event_title: '工程回执展示',
      admission_origin: 'manual', priority: 'normal', status, stage: 'done', runtime_id: 'engineering',
      profile_ref: null, current_round: 0, budget: null, coverage: null,
      current_action: 'Capability data.historical was rejected before execution.',
      stop_reason: { code: status === 'cancelled' ? 'cancelled' : 'runtime_unavailable', detail: stop, bounded: true, remaining_hard_gaps: [] },
      failure, latest_sequence_no: 8, artifact_id: null, available_at: null, parent_run_id: null, created_at: at, updated_at: at },
    research_focus: null, trigger_snapshot: null, decision_snapshot: null, evidence: [], rounds: [], causal_case: null,
    horizons: [], trace: [], action_summary: { schema_version: 'research-action-summary.v1', recommendation: 'unavailable',
      headline: '工程控制无报告', rationale: stop, reliability: 'unavailable', trade_authorized: false, artifact_status: 'pending',
      data_status: 'insufficient', accepted_evidence_count: 0, stale_evidence_count: 0, rejected_evidence_count: 0,
      blocking_gap_ids: [], warning_codes: [failure.error_code], supporting_evidence_refs: [], next_review_at: null },
    process_steps: [{ step_id: 'engineering-failure', order: 1, kind: 'plan', label: '工程展示控制', status: 'failed',
      objective: '核对停止依据', outcome: stop, evidence_refs: [], tool_call_refs: [], warning_codes: [], occurred_at: at }],
    total_tool_calls: 0, total_subagents: 0, total_tokens: null, estimated_cost_usd: null, scheduled_recheck_at: null,
  })
  return { basic, detail }
}

async function readFailedTask(task: ReturnType<typeof retainedTask>, check: (element: HTMLDivElement, reason: Element) => void) {
  const basicRead = vi.spyOn(api, 'run').mockResolvedValue(task.basic)
  const detailRead = vi.spyOn(api, 'researchRun').mockResolvedValue(task.detail)
  vi.spyOn(api, 'researchLinkedCommandReceipt').mockResolvedValue(null)
  const post = vi.spyOn(api, 'researchCommand'), submit = vi.spyOn(api, 'submitResearch')
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><DesignStatePage scene="failed" params={new URLSearchParams({ run: task.basic.run_id, back: '#/home' })} onNavigate={vi.fn()} /></QueryClientProvider>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(basicRead).toHaveBeenCalledTimes(1)
    expect(detailRead).not.toHaveBeenCalled()
    await act(async () => [...element.querySelectorAll('button')].find(button => button.textContent === '读取完整任务与停止依据')!.click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(detailRead).toHaveBeenCalledTimes(1)
    const reason = [...element.querySelectorAll('.design-state-notice')].find(node => node.querySelector('strong')?.textContent === (task.basic.status === 'cancelled' ? '停止原因' : '未完成原因'))!
    check(element, reason)
    expect(post).not.toHaveBeenCalled(); expect(submit).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear(); element.remove(); sessionStorage.clear(); vi.restoreAllMocks() }
}

it.each([
  ['research_baseline_http_refused', '基础材料请求被拒绝'],
  ['unclassified_terminal_control', '本次研究尚未形成可提交的完整结果'],
])('explains structured %s after an explicit full-record read without guessing its underlying cause', async (errorCode, explanation) => {
  const task = retainedTask(`engineering-${errorCode}`, errorCode, 'failed', systemFallback)
  await readFailedTask(task, (element, reason) => {
    expect(reason.textContent).toContain(explanation)
    expect(reason.textContent).not.toContain(systemFallback)
    expect(reason.textContent).not.toContain('已保存的执行原因：失败')
    expect(reason.textContent).not.toMatch(/SDK|网络|行情过期|数据过期/)
    const audit = [...element.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === '原始任务记录')!
    expect(audit.textContent).toContain(systemFallback)
    expect(audit.textContent).toContain(errorCode)
  })
})

it.each(['failed', 'cancelled'] as const)('keeps the %s terminal cause separate from later denied work without submitting a retry', async status => {
  const stop = status === 'cancelled' ? '用户主动停止了这次研究，原任务与已取得材料保留。'
    : '研究执行期间的原件核对发现变化，未能确认继续执行的身份；已保留原任务、材料与失败记录，尚未形成完整报告。请先核对原记录及运行状态。'
  const task = retainedTask(`engineering-terminal-${status}`, 'research_method_binding_failed', 'original_preparation_readset_changed', stop, status)
  await readFailedTask(task, (element, reason) => {
    expect(reason.textContent).toContain(status === 'cancelled' ? '用户主动停止' : '原件核对发现变化')
    expect(reason.textContent).not.toContain(task.detail.run.current_action)
    if (status === 'cancelled') expect(reason.textContent).not.toContain('未能确认继续执行的身份')
    const audit = [...element.querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === '原始任务记录')!
    const paragraphs = [...audit.querySelectorAll('p')]
    expect(paragraphs[2].textContent).toContain(stop)
    expect(paragraphs[3].textContent).toContain('research_method_binding_failed')
    expect(paragraphs[3].textContent).toContain('original_preparation_readset_changed')
    expect(paragraphs[3].textContent).toContain('orchestration')
    expect(paragraphs[3].textContent).not.toContain('research_run_terminal')
    expect(paragraphs[4].textContent).toContain('最近工作记录（不替代任务停止依据）')
    expect(paragraphs[4].textContent).toContain(task.detail.run.current_action)
  })
})
