// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it } from 'vitest'
import { researchObservabilityViewSchema, researchValueEvaluationSchema } from '../api/schemas'
import { ResearchDiagnostics } from './ResearchPage'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const original = {
  schema_version: 'research-value-evaluation.v1', evaluation_id: 'v', run_id: 'r',
  artifact_id: null, evaluation_version: 'old', mode: 'failed',
  hard_coverage_ratio: 0.8, soft_coverage_ratio: 0.5, accepted_evidence_count: 4,
  rejected_evidence_count: 0, typed_fact_count: 3, baseline_status: 'unavailable',
  latency_ms: null, cost_status: 'unknown', known_cost_usd: null, citation_traceability: 0,
  usefulness: 'partial', terminal_reason: 'deadline', outcomes: [],
  evaluated_at: '2026-10-01T00:00:00Z',
}

describe('saved coverage basis', () => {
  it.each(['unavailable', 'invalid', 'saved_pack'])('explains the saved configuration scope for %s', async packBasis => {
    const versions = { domain_pack_ref: null, domain_pack_version: null, role_profile_ref: null, runtime_id: 'dsh', runtime_version: 'recorded-build', capability_versions: [], gate_policy_ref: null, source_registry_ref: null, pack_basis: packBasis }
    const observability = researchObservabilityViewSchema.parse({ schema_version: 'research-observability-view.v1', run_id: 'r', versions, readiness: [], source_attempts: [], cost: { status: 'unknown', known_subtotal_usd: null, total_usd: null, currency: 'USD', unknown_components: [], components: [] }, trajectory_ref: null, telemetry_ref: null, ledger_ref: 'r', generated_at: original.evaluated_at })
    const container = document.createElement('div'); const root = createRoot(container)
    await act(async () => root.render(<ResearchDiagnostics observability={observability} loading={false} />))
    expect(container.textContent).toContain('版本未记录')
    expect(container.querySelector('[aria-label="历史配置核验"]')?.textContent).toContain('不能单独证明该规则已执行')
    expect(container.textContent).toContain('工具版本：未保存可核验版本')
    expect(container.textContent).not.toContain('crypto_macro.v1')
    expect(container.textContent).not.toContain('0 项证据要求未就绪')
    expect(container.textContent).toContain(packBasis === 'saved_pack' ? '来自本次冻结凭据' : packBasis === 'invalid' ? '原研究包凭据无法核验' : '原研究包未保存完整版本凭据')
    await act(async () => root.unmount())
  })

  it.each([undefined, 'unavailable', 'invalid', 'saved_request', 'round_request_only'])('hides unverifiable percentages for %s', async basis => {
    const evaluation = researchValueEvaluationSchema.parse({ ...original, coverage_basis: basis })
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => root.render(<ResearchDiagnostics evaluation={evaluation} loading={false} />))
    const text = container.querySelector('[aria-label="证据覆盖评估依据"]')!.textContent!
    expect(text).not.toContain('80%')
    expect(text).not.toContain('0%')
    expect(text).toMatch(/旧评估|未知|无法核验/)
    expect(container.textContent).toContain('部分有帮助')
    expect(container.textContent).toContain('任务失败')
    expect(container.textContent).not.toContain('research_only')
    await act(async () => root.unmount())
  })

  it.each(['saved_task_request', 'saved_checkpoint', 'saved_result'])('shows retained coverage with provenance for %s', async basis => {
    const evaluation = researchValueEvaluationSchema.parse({ ...original, coverage_basis: basis,
      coverage_cutoff_at: '2026-09-29T00:00:00Z', soft_coverage_ratio: null })
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => root.render(<ResearchDiagnostics evaluation={evaluation} loading={false} />))
    const text = container.querySelector('[aria-label="证据覆盖评估依据"]')!.textContent!
    expect(text).toContain('必需证据覆盖 80%')
    expect(text).not.toContain('补充证据覆盖')
    expect(text).toContain('覆盖率不代表结论正确率')
    expect(text).toContain('信息截止时间')
    await act(async () => root.unmount())
  })

  it('does not promise a directional score for a no-trade decision', async () => {
    const evaluation = researchValueEvaluationSchema.parse({ ...original, outcomes: [{
      forecast_id: 'f', horizon: '30m', direction: 'no_trade', quality_status: 'unavailable',
      return_pct: null, net_return_pct: null, brier_score: null, direction_correct: null,
      mfe_pct: null, mae_pct: null, unavailable_reason: 'direction_not_applicable', observed_at: null,
    }] })
    const container = document.createElement('div')
    const root = createRoot(container)
    await act(async () => root.render(<ResearchDiagnostics evaluation={evaluation} loading={false} />))
    expect(container.textContent).toContain('方向：不计成绩')
    expect(container.textContent).toContain('不计入方向准确率')
    expect(container.textContent).not.toContain('方向：待复核')
    expect(container.textContent).not.toContain('direction_not_applicable')
    await act(async () => root.unmount())
  })
})
