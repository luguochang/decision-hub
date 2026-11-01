import { boundedEvidenceText, technicalSummaryName } from '../research/technicalPresentation'
import { researchDisplayText, researchDisplayTitle } from './researchLanguage'

// Names are emitted by decision_graph.py, research_graph.py, steps.py, calls.py
// and decision/gate.py. Unknown values retain their original audit record.
const names: Record<string, string> = {
  mark_running: '开始执行', freeze_snapshot: '冻结输入快照', research: '分析输入材料',
  gate_and_commit: '检查并保存报告', interrupted: '已中断',
  'run.started': '执行开始', 'snapshot.frozen': '输入快照已保存',
  'research.completed': '分析步骤结束', 'decision.committed': '报告与检查结果已保存',
  'research.task_policy.created': '研究取证政策已保存',
  'research.agentic.failed': '研究执行失败', 'research.agentic.completed': '研究执行结束',
  'research.scheduled_recheck.admitted': '定期复核任务已接收',
  policy_delta: '政策变化核查', counter_thesis: '反向解释核查',
  decision_candidate: '形成分析候选', decision_synthesis: '综合判断',
  research_supervisor: '研究协调', data_quality: '资料质量核查',
  manual: '手工输入', transcript: '转录材料', official_feed: '官方订阅来源',
  responses: '标准响应接口', chat: '对话接口', offline: '离线调用记录',
  candidate: '候选执行记录', artifact: '报告记录', experiment: '实验记录',
  step_interrupted: '执行步骤已中断', step_execution_failed: '执行步骤失败',
  unknown_runtime_error: '执行器异常，原因待核对', run_execution_failed: '任务执行失败',
  duplicate_observation: '相同输入已有记录', budget_accounting_unavailable: '费用尚无法核对',
  facts_present: '事实材料', citations_present: '引用依据',
  counter_thesis_present: '反向解释', manual_action_fields: '触发与失效条件',
  probability_cap: '概率上限', directional_value: '方向效果验证',
  facts_missing: '缺少事实材料', citations_missing: '缺少引用依据',
  counter_thesis_missing: '缺少反向解释', action_fields_missing: '缺少触发或失效条件',
  uncalibrated_probability_capped: '未校准概率超过允许上限',
  pass: '通过', fail: '未通过', ok: '此项检查通过',
}

export function legacyRunName(value: string | null | undefined, noun = '记录') {
  return value ? names[value] ?? technicalSummaryName(value, '未记录', `${noun}待解释，见原始记录`) : '未记录'
}
export function legacyRunTitle(value: string | null | undefined) {
  if (!value) return '尚未生成报告'
  const title = researchDisplayTitle(value)
  return /[a-zA-Z]{3}/.test(title) && !/[\u3400-\u9fff]/.test(title)
    ? '执行记录（原始标题以英文保存）' : title
}
export function legacyContent(value: string) {
  return value.trim() ? boundedEvidenceText(value) : '暂无内容记录'
}
export function legacyReportSummary(value: string) {
  const text = researchDisplayText(value.replace(/\s+/g, ' ').trim())
  if (/[a-zA-Z]{3}/.test(text) && !/[\u4e00-\u9fff]/.test(text)) {
    return '报告摘要以英文保存，未提供这段摘要的中文版本；下方事实与依据仍可继续阅读，完整摘要见原始记录。'
  }
  return legacyContent(value)
}
export function legacyNumber(value: number) {
  return Math.abs(value) > 0 && Math.abs(value) < 0.0001
    ? value.toExponential(4) : value.toLocaleString('zh-CN', { maximumSignificantDigits: 8 })
}
export function legacyDuration(value: number | null) {
  return value === null ? '耗时未记录' : `耗时 ${legacyNumber(value)} 毫秒`
}
export function legacyCost(value: number | null, status?: string) {
  if (status && !['known', 'estimated'].includes(status)) return '费用尚未确认'
  if (value === null) return '费用未记录'
  return `${status === 'estimated' ? '估算费用' : '记录费用'} $${legacyNumber(value)}`
}

const ledgerPreviewLimit = 120
const ledgerLongPreview = '摘要较长，请查看详情阅读完整内容。'
const ledgerUntrustedBoundary = /["'“”‘’()（）\[\]{}【】]|https?:\/\/|\b(?:[a-zA-Z][a-zA-Z0-9_]*_(?:id|ref)|id)\s*[:=]|…|\.{3,}/
const ledgerImmediateReversal = /^(?:但(?:其实|实际上)|更正|纠正|修正|撤销|已失效|不成立|(?:上述|前述|先前|原)(?:结论|判断|观点)(?:已|现已|已经)?(?:失效|撤销|不成立))/
const ledgerPreviewLength = (text: string) => Array.from(text).length

function ledgerPreviewText(text: string) {
  // Only observed Chinese headline prefixes; arbitrary tokens, IDs and URLs stay outside these rules.
  const prose = text.replace(/^Evidence 已保留(?=[，,。；;]|$)/, '证据已保留')
    .replace(/^中文结论 revision ([0-9]+)[:：]/, '中文结论（第$1次修订）：')
  return researchDisplayText(prose)
}

// A list preview of the saved headline, never an assertion about the run or report.
export function legacyLedgerPreview(value: string | null | undefined): { text: string; isExcerpt: boolean } {
  if (value == null || !value.trim()) return { text: '摘要未记录', isExcerpt: false }
  const original = value.replace(/\s+/g, ' ').trim()
  // Inspect the original language before registered term substitutions.
  if (!/[\u3400-\u9fff]/.test(original)) {
    return { text: '摘要未提供中文预览，请查看详情核对原文。', isExcerpt: false }
  }
  const complete = ledgerPreviewText(original)
  if (ledgerPreviewLength(complete) <= ledgerPreviewLimit) return { text: complete, isExcerpt: false }
  const fallback = { text: ledgerLongPreview, isExcerpt: false }
  const boundary = /[。！？]+/.exec(original)
  if (!boundary || boundary.index === undefined) return fallback
  // Slice only at an explicit complete Chinese sentence, never at a character cap.
  const end = boundary.index + boundary[0].length
  const firstOriginal = original.slice(0, end), remainder = original.slice(end).trimStart()
  if (ledgerUntrustedBoundary.test(firstOriginal) || ledgerImmediateReversal.test(remainder)) return fallback
  const first = ledgerPreviewText(firstOriginal)
  if (ledgerPreviewLength(firstOriginal) > ledgerPreviewLimit || ledgerPreviewLength(first) > ledgerPreviewLimit) return fallback
  return { text: first, isExcerpt: true }
}
