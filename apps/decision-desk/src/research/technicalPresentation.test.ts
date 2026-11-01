import { describe, expect, it } from 'vitest'
import { researchRunDetailViewSchema } from '../api/schemas'
import type { ResearchRunDetailView } from '../api/types'
import { sourceLabel } from '../product/operationLabels'
import { directionLabel, evidenceSummary, savedDailyRequirementLabel, missingEvidencePublicationText, technicalName, technicalSummaryName, warningName, currentPreparationWaiting, researchTraceTitle, researchRecordTime, researchTraceTimeNotice, failureExplanation } from './technicalPresentation'

it('explains the exact retained original deadline without treating an unknown permission error as that cause', () => {
  expect(technicalName('original_preparation_deadline_elapsed')).toBe('报告原件核验超过任务时限')
  expect(failureExplanation('research_worker_failed', 'original_preparation_deadline_elapsed')).toContain('后台原件核验未能在本次任务时限内完成')
  expect(failureExplanation('research_worker_failed', 'PermissionError')).toBeNull()
})

it('explains only the bounded pre-BEGIN model binding busy code without guessing an unknown commit cause', () => {
  expect(failureExplanation('dsh_model_binding_writer_busy', null)).toContain('取得数据库写锁时暂时繁忙')
  expect(failureExplanation('research_worker_failed', 'dsh_model_binding_writer_busy')).toContain('不会自动重试原任务')
  expect(failureExplanation('research_worker_failed', 'model_binding_commit_unknown')).toBeNull()
  expect(technicalName('dsh_native_session_lost')).toBe('研究执行会话在服务恢复后已不存在')
  expect(failureExplanation('dsh_native_session_lost', null)).toContain('没有重放模型请求')
  expect(failureExplanation('dsh_session_recovery_failed', null)).toContain('没有把它显示为成功')
  expect(failureExplanation('dsh_session_not_found', null)).toContain('无法从 DSH 服务读取')
})

describe('saved technical material presentation', () => {
  it('labels the emitted bounded graph fallback without rewriting an unknown status or implying complete causal evidence', () => {
    expect(technicalName('bounded_fallback')).toBe('有保留的图谱结果')
    expect(technicalSummaryName('bounded_fallback')).toBe('有保留的图谱结果')
    expect(technicalName('causal')).toBe('已形成论据')
    expect(technicalName('custom_bounded_fallback_v2')).toBe('custom_bounded_fallback_v2')
    expect(technicalSummaryName('custom_bounded_fallback_v2')).toBe('尚无中文说明，见原始记录')
  })
  it('explains unconfirmed research originals without claiming lost writes or an unavailable SDK', () => {
    const explanation = '执行原始记录尚未核验通过，本次研究未完成。已有任务与材料继续保留；核验服务恢复后，可从原任务发起关联的新研究。'
    expect(technicalName('research_observation_unavailable')).toBe('研究原件未确认')
    expect(technicalSummaryName('research_observation_unavailable')).toBe('研究原件未确认')
    expect(failureExplanation('research_observation_unavailable', 'research_observation_unavailable')).toBe(explanation)
    expect(failureExplanation('research_observation_unavailable', 'unknown-original-cause')).toBe(explanation)
    expect(failureExplanation('unknown-original-error', 'research_observation_unavailable')).toBe(explanation)
    expect(failureExplanation('unknown-original-error', 'unknown-original-cause')).toBeNull()
    expect(technicalName('unknown-original-cause')).toBe('unknown-original-cause')
    expect(technicalSummaryName('unknown-original-cause')).toBe('尚无中文说明，见原始记录')
    expect(failureExplanation('dsh_evidence_unattested', 'synthesis_attestation')).toBe('综合结果的证据校验失败：模型引用了可信输入和实际工具结果以外的材料。')
  })
  it('prioritizes actual metrics over URLs, hashes and provider metadata', () => {
    const result = evidenceSummary(JSON.stringify({ provider: 'venue-a', source_urls: { stats: 'https://example.test/secret-location' }, content_hash: 'a'.repeat(64), values: { basis: '-0.000448461700903595', funding_rate: '0.00000003415', open_interest: '1234.12' } }))
    expect(result).toContain('期现基差 -0.000448')
    expect(result).toContain('资金费率 3.4150e-8')
    expect(result).toContain('未平仓量（来源口径）1,234.12'.replace('）', '） '))
    expect(result).not.toContain('https:')
    expect(result).not.toContain('content_hash')
    expect(result).not.toContain('资金费率 0 ·')
    expect(evidenceSummary('{"values":{"funding_rate":"1e-400"}}')).toContain('资金费率 1e-400')
  })
  it('does not invent a metric meaning for unknown or truncated JSON', () => {
    expect(evidenceSummary('{"model_confidence":0.99,"url":"https://example.test"}')).toBe('已保存结构化材料，指标含义与精确数值请查看原始记录。')
    expect(evidenceSummary('{"funding_rate":"0.01')).toContain('当前摘要格式无法解析')
    expect(evidenceSummary('{"values":{"basis":null,"funding_rate":""}}')).not.toContain('资金费率 0')
  })
  it('shows Yahoo Treasury yield units without treating a yield change as a bond return or rescaling stored values', () => {
    const original = JSON.stringify({ provider: 'yahoo-finance-public', instrument: 'US10Y', level: '5.236999988555908', change_5d: '7.4999809265136500', return_5d: '1.452921477337899534902794692', latest_date: '2026-10-01' })
    const summary = evidenceSummary(original)
    expect(summary).toContain('收益率水平 5.237%')
    expect(summary).toContain('五个交易日变动 7.499981 基点')
    expect(summary).toContain('收益率水平相对变化 1.452921%')
    expect(summary).not.toContain('债券回报')
    expect(summary).not.toContain('五个交易日收益率')
    expect(JSON.parse(original).level).toBe('5.236999988555908')
    const unknownProvider = evidenceSummary(original.replace('yahoo-finance-public', 'unknown-provider'))
    expect(unknownProvider).toContain('指标值 5.237')
    expect(unknownProvider).not.toMatch(/基点|收益率水平|%/)
    expect(evidenceSummary(original, 'unknown-provider')).not.toMatch(/基点|收益率水平|%/)
    const absent = evidenceSummary('{"provider":"yahoo-finance-public","instrument":"US10Y","level":null,"change_5d":false,"return_5d":""}')
    expect(absent).not.toContain('0%')
    expect(absent).not.toContain('基点')
  })
  it('describes only the saved Yahoo daily contract and never promotes acquisition time to publication time', () => {
    const evidence = { kind: 'market' as const, source_id: 'yahoo-finance-public', requirement_id: 'macro_transmission', excerpt: '{"provider":"yahoo-finance-public","completion_contract":"yahoo-provider-declared-daily.v2"}' }
    expect(savedDailyRequirementLabel(evidence)).toBe('宏观传导参考（日线）')
    expect(savedDailyRequirementLabel({ ...evidence, requirement_id: 'cross_asset_confirmation' })).toBe('跨资产参考（日线）')
    expect(missingEvidencePublicationText(evidence)).toBe('来源未提供发布时间；按已保存的日线区间核对时效')
    for (const other of [
      { ...evidence, source_id: 'unknown-provider' },
      { ...evidence, excerpt: evidence.excerpt.replace('.v2', '.v1') },
      { ...evidence, excerpt: '{"observed_at":"2026-10-02T18:53:01Z"}' },
      { ...evidence, excerpt: '{"completion_contract":' },
    ]) {
      expect(savedDailyRequirementLabel(other)).toBeNull()
      expect(missingEvidencePublicationText(other)).toBe('未保存可核验的发布时间；时效以已保存核验记录为准')
    }
    expect(missingEvidencePublicationText({ ...evidence, kind: 'web' })).toContain('不能用读取时间代替')
  })
  it('labels saved gap codes and compares direction without changing the values', () => {
    expect(warningName('gap:market_narrative:insufficient_sources')).toBe('最新公开信息：来源数量不足')
    expect(warningName('review:bounded')).toBe('复核有保留')
    expect(directionLabel('bullish')).toBe('偏多')
    expect(directionLabel(null)).toBe('尚无记录')
    expect(directionLabel('new-direction')).toBe('new-direction')
  })
  it('explains failure boundaries without inventing an unknown code meaning', () => {
    expect(warningName('legacy_provider_attempts_unverifiable')).toBe('历史调用次数未能核验，请查看原始记录')
    expect(technicalSummaryName('research_capability_mode_denied')).toBe('取证工具不支持本次执行模式')
    expect(technicalSummaryName('semantic_mismatch')).toBe('材料不满足所需事实字段或指标要求')
    expect(technicalSummaryName('provider_route_unavailable')).toBe('没有获准且支持该请求的服务路径')
    expect(technicalSummaryName('new-provider-error')).toBe('尚无中文说明，见原始记录')
    expect(technicalSummaryName('已登记的中文原因')).toBe('已登记的中文原因')
    expect(technicalSummaryName(null, '结束原因未记录')).toBe('结束原因未记录')
    expect(warningName('gap:new-requirement:new-gap')).toBe('其他材料要求：尚无中文说明，见原始记录')
    expect(sourceLabel('yahoo-finance-public')).toBe('雅虎财经')
    expect(sourceLabel('new-internal-source')).toBe('来源名称待核对')
  })
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

describe('current preparation waiting identity', () => {
  it('does not conflate unknown provider attempt records in malformed raw data with saved empty attempts', () => {
    const sample = preparationWaitingDetail()
    expect(researchRunDetailViewSchema.safeParse(sample).success).toBe(true)
    // Deliberately corrupt a runtime value; null is rejected by the canonical API parser.
    Object.defineProperty(sample.run.failure!, 'provider_attempts', { value: null })
    expect(researchRunDetailViewSchema.safeParse(sample).success).toBe(false)
    expect(sample.trace[0].error!.provider_attempts).toEqual([])
    expect(currentPreparationWaiting(sample)).toBeNull()
  })
  const changes: [string, (sample: ResearchRunDetailView) => void][] = [
    ['terminal failure', sample => { sample.run.status = 'failed' }],
    ['saved report', sample => { sample.run.artifact_id = 'original-report' }],
    ['unknown cause', sample => { sample.trace[0].error!.cause_code = 'other-wait-timeout' }],
    ['different provenance', sample => { sample.run.failure!.deadline_ms = 2000 }],
    ['different origin', sample => { sample.trace[0].error!.origin = 'transport' }],
    ['permanent failure', sample => { sample.trace[0].error!.retryable = false }],
    ['missing error', sample => { sample.trace[0].error = null }],
    ['wrong Run', sample => { sample.trace[0].run_id = 'another-run' }],
    ['wrong session', sample => { sample.trace[0].research_session_id = 'product:another-run' }],
    ['wrong reference', sample => { sample.trace[0].reference_id = 'another-run' }],
    ['wrong reference kind', sample => { sample.trace[0].reference_type = 'stop_reason' }],
    ['unrelated current action', sample => { sample.run.current_action = '取得新的原始资料' }],
    ['later sequence missing', sample => { sample.run.latest_sequence_no = 5 }],
    ['duplicate sequence', sample => { sample.trace.push({ ...sample.trace[0] }) }],
    ['new normal trace', sample => { sample.trace.push({ ...sample.trace[0], sequence_no: 5, event_type: 'plan_created', error: null, error_code: null }); sample.run.latest_sequence_no = 5 }],
    ['inconsistent sequence projection', sample => { sample.trace.push({ ...sample.trace[0], sequence_no: 5 }) }],
    ['storage report without saved trace', sample => { sample.trace = []; sample.run.failure = null; sample.run.current_action = null }],
  ]
  it.each(changes)('does not claim current waiting from %s', (_name, change) => {
    const sample = preparationWaitingDetail(); change(sample)
    expect(currentPreparationWaiting(sample)).toBeNull()
  })
  it('separates a historical waiting event from current terminal status without changing originals', () => {
    const sample = preparationWaitingDetail(), before = JSON.stringify(sample)
    expect(currentPreparationWaiting(sample)).toBe(sample.trace[0])
    expect(JSON.stringify(sample)).toBe(before)
    sample.run.status = 'completed'; sample.run.artifact_id = 'saved-original'
    expect(currentPreparationWaiting(sample)).toBeNull()
    expect(researchTraceTitle(sample.trace[0], sample.run.run_id)).toBe('执行准备尚未确认')
    expect(researchTraceTitle({ ...sample.trace[0], status: 'failed' }, sample.run.run_id)).toBe('本次研究结束')
    expect(researchTraceTitle(sample.trace[0], 'another-run')).toBe('本次研究结束')
  })
})

describe('saved trace clock semantics', () => {
  it('distinguishes records one day apart even when their original time of day matches', () => {
    const earlier = researchRecordTime('2026-10-01T00:21:46.490229Z')
    const later = researchRecordTime('2026-10-02T00:21:46.490229Z')
    expect(earlier.label).not.toBe(later.label)
    expect(earlier.label).toMatch(/^2026-\d{2}-\d{2} \d{2}:\d{2}:\d{2}（本地 UTC[+-]\d{2}:\d{2}）$/)
    expect(later.instantMs! - earlier.instantMs!).toBe(24 * 60 * 60 * 1000)
    expect(earlier.dateTime).toBe('2026-10-01T00:21:46.490229Z')
  })

  it('shows the same local instant for aware offsets while retaining each original timestamp', () => {
    const utc = researchRecordTime('2026-10-02T18:51:50.210750Z')
    const offset = researchRecordTime('2026-10-03T02:51:50.210750+08:00')
    expect(offset.label).toBe(utc.label)
    expect(offset.instantMs).toBe(utc.instantMs)
    expect(offset.dateTime).toBe('2026-10-03T02:51:50.210750+08:00')
    expect(researchTraceTimeNotice(offset.dateTime, utc.dateTime)).toBeNull()
  })

  it.each([undefined, null, '', 'unparseable', '2026-10-01T00:21:46', '2026-02-30T00:21:46Z', '2026-10-01T00:21:46+00:99', '2026-10-01T00:21:46-00:00'])('keeps missing or unverified original time %s unknown', value => {
    const result = researchRecordTime(value)
    expect(result.label).toContain('时间未知')
    expect(result.instantMs).toBeNull()
    expect(result.dateTime).toBeUndefined()
    expect(researchTraceTimeNotice(value, '2026-10-02T18:51:50Z')).toBeNull()
  })

  it('only asks to check an earlier record clock and does not claim actual earlier execution', () => {
    const notice = researchTraceTimeNotice('2026-10-01T00:21:46.490229Z', '2026-10-02T18:51:50.210750Z')
    expect(notice).toContain('请核对时间口径')
    expect(notice).toContain('不能据此判断实际执行开始')
    expect(researchTraceTimeNotice('2026-10-02T18:52:19Z', '2026-10-02T18:51:50Z')).toBeNull()
    expect(researchTraceTimeNotice('2026-10-01T00:21:46Z', 'unknown')).toBeNull()
  })
})
