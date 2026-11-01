import type { ResearchSessionResult } from '@decision-hub/contracts-ts'
import type { ResearchQualityServingRead, ResearchQualityView } from '../api/researchQuality'
import { qualityGapText, qualityStopText } from './qualityReportLanguage'
import { technicalName } from '../research/technicalPresentation'

export type ResearchReadingInput = {
  runId: string
  view: ResearchQualityView | null
  servingRead: ResearchQualityServingRead | null
  // The component parses this from the same view.decision.full_report_json.
  // Snapshot authentication/hash verification belongs to the existing API read.
  report: ResearchSessionResult | null
}

const windows = ['30m', '24h', '72h'] as const
const direction = { long: '看涨研究假说', short: '看跌研究假说', neutral: '中性研究假说', no_trade: '暂不判断方向' }
const claimType = { fact: '观察事实', inference: '推断', scenario: '条件情景' }
const resolution = { accepted: '已接受', bounded: '有保留', requires_new_evidence: '需要新增证据', rejected: '已拒绝' }
const horizonNames = { '30m': '30分钟', '24h': '24小时', '72h': '72小时' }
// Existing technical labels cover evidence, gaps and stop codes. These are the
// quality/snapshot-specific statuses already used by ResearchQuality.
const readingLabels: Record<string, string> = {
  eligible: '具备研究资格', blocked: '被关键问题阻断', unknown: '尚未核验', supported: '已核验支持',
  complete: '完整', incomplete: '尚有缺口', untrusted: '未通过核验',
  research_only: '仅供研究', publish: '已发布', partial: '尚有部分缺口', reject: '未通过发布检查',
  sufficient: '已达到本次材料门要求', continue: '仍需继续取证', unresolved: '尚有未解决问题',
  uncalibrated: '概率未校准', calibrated: '原报告标记已校准', completed: '执行已结束',
  active: '运行中', paused: '已暂停', fresh: '未过期', historical: '历史资料', delayed: '延迟资料', realtime: '实时资料',
  open: '尚未解决', answered: '已有回应', deferred: '暂缓处理', rebutted: '已有反驳', unchanged: '不变',
  increased: '增加', reduced: '减少', reversed: '反转', critical: '关键', important: '重要', minor: '待完善',
  direction: '方向', path_risk: '路径风险', execution: '执行', report: '报告',
  verified_web: '已核验网页', search_derived: '搜索所得', unverified: '未核验来源',
  transcript: '文字实录', data_reviewer: '数据审查', policy_reviewer: '政策审查',
  bounded_stop: '到达有界停止条件', resolved: '已解决', permission_denied: '本次取证权限拒绝',
}

export function researchWindowExpiry(expiresAt: unknown, nowMs: number): { status: 'expired' | 'not_expired' | 'unknown'; expiresUtc: string | null; label: string; detail: string } {
  const match = typeof expiresAt === 'string'
    ? /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/.exec(expiresAt) : null
  const expiresMs = match && typeof expiresAt === 'string' ? Date.parse(expiresAt) : NaN
  const year = Number(match?.[1]), month = Number(match?.[2]), day = Number(match?.[3])
  const monthDays = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  const calendarValid = !!match && year >= 1 && month >= 1 && month <= 12 && day >= 1
    && day <= monthDays[month - 1]
    && Number(match[4]) <= 23 && Number(match[5]) <= 59 && Number(match[6]) <= 59
    && (match[7] === 'Z' || (Number(match[7].slice(1, 3)) <= 23 && Number(match[7].slice(4)) <= 59))
  if (!calendarValid || !Number.isFinite(expiresMs) || !Number.isFinite(nowMs)) return {
    status: 'unknown', expiresUtc: null, label: '到期状态无法判断',
    detail: '到期时间缺失或无效，无法判断窗口是否到期；不能据此视为当前有效预测。',
  }
  return nowMs >= expiresMs ? {
    status: 'expired', expiresUtc: new Date(expiresMs).toISOString(), label: '已到期，仅供历史复盘',
    detail: '已到期，仅供历史复盘；不能作为当前预测。',
  } : {
    status: 'not_expired', expiresUtc: new Date(expiresMs).toISOString(), label: '按本机时间尚未到期',
    detail: '按本机时间尚未到期；不代表资料当前新鲜、预测有效或可交易。',
  }
}

export function researchUnresolvedScopeText(originalCount: number | null): string {
  if (originalCount === null) return '结构化反方挑战中暂无登记的未解项；原报告未解问题尚未核对，不能据此认定全部问题已解决。'
  if (originalCount > 0) return `结构化反方挑战中暂无登记的未解项；原报告仍保留 ${originalCount} 项未解问题，不能据此认定全部问题已解决。`
  return '结构化反方挑战中暂无登记的未解项；仍需结合原报告、待映射问题和其他资格项核对，不能据此认定没有风险。'
}

export function researchReadingExportReady(input: ResearchReadingInput): boolean {
  const { runId, view, servingRead, report } = input
  const quality = view?.decision
  return !!(view?.status === 'available' && view.run_id === runId && quality?.run_id === runId
    && servingRead?.run_id === runId && servingRead.read_mode === 'confirmed_report_snapshot'
    && Number.isSafeInteger(servingRead.state_version) && (servingRead.state_version ?? 0) > 0
    && /^[a-f0-9]{64}$/.test(quality.full_report_hash) && view.gate_status !== 'reject'
    && quality.report_status === 'complete' && report?.causal_case
    && ['completed', 'degraded'].includes(report.status) && !report.synthesis_failure_code
    && report.horizons.length === 3 && windows.every(window => report.horizons.filter(item => item.horizon === window).length === 1))
}

// Prose stays prose: no raw HTML, Markdown links/images, tables or code blocks
// can be injected by model/source text. Line breaks remain readable paragraphs.
function text(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return '未提供 / 未知'
  return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replace(/([\\`*_[\]{}()#+.!|~\-])/g, '\\$1')
    .replace(/\r\n?/g, '\n').replace(/\n/g, '\n\n')
}
function utc(value: string | null): string {
  return value === null ? '未提供 / 未知' : text(new Date(value).toISOString())
}
function refs(values: readonly string[]): string { return values.length ? values.map(text).join('、') : '无登记项' }
function code(value: string | null | undefined): string {
  if (!value) return '未提供 / 未知'
  const label = readingLabels[value] ?? technicalName(value)
  return `${text(label === value ? '尚无中文名称，保留原码' : label)}（${text(value)}）`
}
function codes(values: readonly string[]): string { return values.length ? values.map(code).join('、') : '无登记项' }
function requirement(value: string): string { return `${text(qualityGapText(value))}（${text(value)}）` }
function sourceUrl(value: string | null): string {
  if (value === null) return '原报告未提供来源链接'
  try {
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash
      || !host.includes('.') || host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local')
      || /^[\d.]+$/.test(host) || host.includes(':') || url.port) {
      return '该原链接可能含查询身份或内部地址，阅读版省略；完整原报告保留原值'
    }
    // Plain text only, never interpolate a source-controlled Markdown link.
    return text(value)
  } catch { return '原来源链接无法安全显示；完整原报告保留原值' }
}

export function renderResearchReadingMarkdown(input: ResearchReadingInput): string {
  if (!researchReadingExportReady(input)) throw new Error('research_reading_snapshot_not_complete')
  const view = input.view!
  const quality = view.decision!
  const report = input.report!
  const causal = report.causal_case!
  // One browser wall-clock read for this local export, separate from checked_at.
  const exportedAtMs = Date.now()
  const lines: string[] = ['# 研究报告阅读版（基于读取时已确认快照）', '',
    `任务 Run：${text(input.runId)}`, '', `原报告 SHA256：${quality.full_report_hash}`, '',
    `快照核验 UTC：${utc(input.servingRead!.checked_at)}；状态版本：${input.servingRead!.state_version}`, '',
    '这是同一份已确认报告的阅读排版，不是新结论，也不是完整原字节。完整 JSON 原件仍由原下载入口提供。', '',
    `报告发布 UTC：${utc(view.published_at)}`, '', `研究开始 / 结束 UTC：${utc(report.started_at)} / ${utc(report.finished_at)}`, '',
    `导出时浏览器参考 UTC：${utc(new Date(exportedAtMs).toISOString())}`, '',
    '以下窗口状态仅按此浏览器时点与原到期时间计算，离线文件不会自动更新。确认保存与读取不刷新原证据、窗口或预测资格；这不是点击导出时再次核验当前许可。', '',
    ...report.horizons.flatMap(item => { const expiry = researchWindowExpiry(item.expires_at, exportedAtMs); return [`${horizonNames[item.horizon]}：${text(expiry.detail)} 原到期 UTC：${expiry.expiresUtc ? utc(expiry.expiresUtc) : '无法核对'}`, ''] }),
    '## 完整综合判断', '', text(causal.thesis), '', `综合引用：${refs(causal.evidence_refs)}`, '']
  const list = (title: string, values: readonly string[]) => {
    lines.push(`### ${title}`, '')
    if (!values.length) lines.push('原报告无登记项。', '')
    values.forEach((value, index) => lines.push(`${index + 1}. ${text(value)}`, ''))
  }
  for (const [title, chain] of [['主要作用机制', causal.main_chain], ['反方作用机制', causal.opposite_chain]] as const) {
    lines.push(`### ${title}`, '')
    chain.forEach((link, index) => lines.push(`#### ${index + 1}. ${claimType[link.claim_type]}`, '', text(link.statement), '',
      `确认条件：${text(link.confirmation)}`, '', `失效条件：${text(link.invalidation)}`, '',
      `涉及窗口：${link.affected_horizons.map(window => horizonNames[window]).join('、')}；引用：${refs(link.evidence_refs)}`, ''))
  }
  list('综合未解问题', causal.unresolved_questions)
  const probabilityBoundary = report.horizons.some(item => item.probability_status === 'uncalibrated')
    ? '本报告含未校准的主观概率，不能作为经验证的预测或路径风险概率。'
    : '原报告的校准标记保留；阅读导出不额外验证预测或路径风险概率。'
  const publicationBoundary = quality.publication_mode === 'research_only' ? '本版为仅供研究的材料。'
    : quality.publication_mode === 'partial' ? '本版研究材料仍有部分缺口。' : '本版为已保存的研究材料。'
  lines.push('## 适用范围', '', `${publicationBoundary}${probabilityBoundary}保存确认、阅读导出和审查记录均不授予交易执行或杠杆许可；原缺口和资格记录完整列在下文。`, '',
    '## 三个时间窗口', '', '基准观点按原方向记录；原合同没有独立基准价字段，阅读版不补造价位。确认条件完整保留原触发说明。', '', '各窗口的建议复核时间是本次原报告保存的参考值，不表示已经登记或执行自动复查；实际计划与执行状态请返回产品页面的“本报告的后续复查”核对。', '')
  for (const item of report.horizons) {
    const expiry = researchWindowExpiry(item.expires_at, exportedAtMs)
    lines.push(`### ${horizonNames[item.horizon]}`, '', `基准观点：${direction[item.action]}（${text(item.action)}）`, '',
      `确认 / 触发：${text(item.trigger)}`, '', `失效：${text(item.invalidation)}`, '',
      `导出时窗口状态：${text(expiry.detail)}`, '', `到期 UTC：${expiry.expiresUtc ? utc(expiry.expiresUtc) : '无法核对'}`, '', `原报告建议复核时间 UTC（原保存值）：${utc(item.next_review_at)}`, '',
      `原主观概率：${text(item.subjective_probability)}；校准状态：${code(item.probability_status)}。${item.probability_status === 'uncalibrated' ? '此概率未经校准，不能作为经验证的预测或路径风险概率。' : '原报告的校准标签保留，不由阅读导出重新验证。'}`, '',
      `原置信限制：${text(item.confidence_cap_reason)}`, '', `引用：${refs(item.evidence_refs)}`, '')
    list('本窗口缺失事实', item.missing_facts)
  }
  lines.push('## 审查与反方保留', '', '以下仅导出本份已确认原报告内的审查记录，不混入研究详情的执行检查记录；角色或保存状态本身不追加原生审查认证或接受资格。', '')
  if (!report.reviews.length) lines.push('原报告无审查记录。', '')
  report.reviews.forEach((review, index) => {
    lines.push(`### 审查 ${index + 1}`, '', `原角色：${code(review.role)}；原处理：${resolution[review.resolution]}（${text(review.resolution)}）`, '',
      `审查目标原文：${text(review.target_claim)}`, '', `反方挑战原文：${text(review.challenge)}`, '',
      `审查 UTC：${utc(review.created_at)}；审查结果 SHA256：${text(review.result_hash)}`, '', `引用：${refs(review.evidence_refs)}`, '')
    list('审查限制原文', review.limitations)
  })
  lines.push('## 关键风险与质量缺口', '')
  if (!quality.key_risks.length) lines.push('原质量记录未登记关键风险清单；不能据此认定没有风险。', '')
  quality.key_risks.forEach(item => lines.push(`### ${text(item.risk_id)}`, '', text(item.statement), '',
    `未知原因：${text(item.unknown_reason)}；影响：${codes(item.affected_scope)}；引用：${refs(item.evidence_refs)}`, ''))
  lines.push(`尚未解决的结构化挑战 ID：${refs(quality.unresolved_challenge_ids)}`, '')
  if (!quality.unresolved_challenge_ids.length) lines.push(text(researchUnresolvedScopeText(causal.unresolved_questions.length)), '')
  quality.challenges.forEach(item => lines.push(`### 挑战 ${text(item.challenge_id)}`, '', text(item.reason), '',
    `严重度：${code(item.severity)}；状态：${code(item.status)}；处置：${code(item.disposition)}；阻断范围：${codes(item.blocking_scope)}`, '',
    `方向 / 尾部风险 / 监控影响：${code(item.effect_on_direction)} / ${code(item.effect_on_tail_risk)} / ${code(item.effect_on_monitor)}`, '',
    `报告影响：${code(item.effect_on_report)}；引用：${refs(item.evidence_refs)}；回应引用：${refs(item.resolution_evidence_refs)}`, ''))
  list('全部质量未知项', quality.unknowns)
  list('尚无数据接入的问题', quality.unmapped_inquiries)
  lines.push('## 覆盖与冲突', '', `原覆盖状态：${code(report.final_coverage.status)}；必需材料比例：${report.final_coverage.hard_coverage_ratio}；补充材料比例：${report.final_coverage.soft_coverage_ratio}`, '',
    `覆盖评估 UTC：${utc(report.final_coverage.assessed_at)}；已覆盖需求：${refs(report.final_coverage.covered_requirement_ids)}`, '')
  if (!report.final_coverage.gaps.length) lines.push('原覆盖记录没有登记缺口；不因此新增金融资格。', '')
  report.final_coverage.gaps.forEach(gap => lines.push(`### 缺口 ${requirement(gap.requirement_id)}`, '',
    `重要性：${code(gap.importance)}；原原因：${code(gap.reason_code)}；阻断方向输出：${gap.blocks_directional_output ? '是' : '否'}`, '',
    text(gap.query_hint), '', `已尝试能力：${refs(gap.attempted_capabilities)}`, ''))
  report.final_coverage.conflicts.forEach(item => lines.push(`### 冲突 ${text(item.conflict_id)}`, '', text(item.summary), '',
    `需求：${requirement(item.requirement_id)}；严重度：${code(item.severity)}；处理：${code(item.resolution_status)}；引用：${refs(item.evidence_refs)}`, ''))
  lines.push('## 全部观察事实', '', '数值与质量标记按原事实记录；待核验、未采纳、过期或冲突的材料不因阅读导出变成已采纳。各项原码在括注中保留。', '')
  report.facts.forEach((fact, index) => lines.push(`### 事实 ${index + 1}：${text(fact.field)}`, '',
    `原值：${text(fact.value)}；单位：${text(fact.unit)}；指标族：${text(fact.metric_family)}`, '',
    `标的：${text(fact.instrument)}；场所：${text(fact.venue)}；质量：${code(fact.quality)}；延迟类型：${code(fact.delay_class)}`, '',
    `窗口 UTC：${utc(fact.window_start_at)} — ${utc(fact.window_end_at)}；事件偏移：${text(fact.event_offset)}`, '',
    `观测 / 发布 / 接收 UTC：${utc(fact.observed_at)} / ${utc(fact.published_at)} / ${utc(fact.received_at)}`, '',
    `事实 ID：${text(fact.fact_id)}；证据 ID：${text(fact.evidence_id)}；需求：${requirement(fact.requirement_id)}；来源：${text(fact.source_id)}；独立组：${text(fact.independence_group)}`, '',
    `原载荷 SHA256：${text(fact.payload_hash)}`, ''))
  lines.push('## 全部来源与证据', '')
  report.evidence_candidates.forEach((item, index) => lines.push(`### 证据 ${index + 1}：${text(item.evidence_id)}`, '',
    `需求：${requirement(item.requirement_id)}；来源：${text(item.source_id)}；类型：${code(item.kind)}；权威级别：${code(item.authority)}`, '',
    `原质量：${code(item.quality)}；新鲜度：${code(item.freshness_status)}；冲突组：${text(item.conflict_group)}`, '',
    `来源链接：${sourceUrl(item.source_url)}`, '',
    `观测 / 发布 / 接收 UTC：${utc(item.observed_at)} / ${utc(item.published_at)} / ${utc(item.received_at)}`, '',
    `原内容 SHA256：${text(item.content_hash)}；来源内容 SHA256：${text(item.source_content_hash)}`, '',
    '原摘录：', '', text(item.excerpt), ''))
  lines.push('## 资格记录明细（原码对照）', '',
    `发布方式：${code(quality.publication_mode)}；质量门：${code(view.gate_status)}；质量状态：${code(quality.status)}`, '',
    `事实：${code(quality.factual_status)}；方向：${code(quality.direction_status)}；路径风险：${code(quality.path_risk_status)}；执行：${code(quality.execution_status)}；上下文：${code(quality.context_status)}；报告：${code(quality.report_status)}`, '',
    `阻断完成：${quality.blocks_completion ? '是' : '否'}；监控状态：${code(quality.monitor_state)}；原原因：${codes(quality.reason_codes)}`, '',
    '## 上下文、预算与停止边界', '', `原执行状态：${code(report.status)}；停止原因：${code(report.stop_reason.code)}；有界停止：${report.stop_reason.bounded ? '是' : '否'}`, '',
    text(qualityStopText(report.stop_reason.code, report.stop_reason.remaining_hard_gaps, quality.report_status)), '',
    text(report.stop_reason.detail), '', `剩余硬缺口：${refs(report.stop_reason.remaining_hard_gaps)}`, '',
    `原取证调用：${report.total_tool_calls}；原协作数量：${report.total_subagents}；原 Token：${text(report.total_tokens)}；原费用 USD：${text(report.estimated_cost_usd)}`, '')
  if (view.budget) lines.push(`准备上限（不是实际消耗）：工具 ${view.budget.max_tool_calls}；协作 ${view.budget.max_subagents}；轮次 ${view.budget.max_evidence_rounds}；费用 USD ${view.budget.max_estimated_cost_usd}；截止 UTC ${utc(view.budget.deadline_at)}`, '')
  if (report.context_manifest) {
    lines.push(`上下文版本：${report.context_manifest.revision}；原输入指纹：${text(report.context_manifest.input_fingerprint)}`, '',
      `选择输入：${refs(report.context_manifest.selected_inputs)}`, '', `实际输入：${refs(report.context_manifest.effective_inputs)}`, '')
    report.context_manifest.excluded_inputs.forEach(item => lines.push(`排除输入：${text(item.evidence_ref)}；原原因：${text(item.reason)}`, ''))
  } else lines.push('原上下文清单缺失，阅读版不补认。', '')
  lines.push('阅读版未导出内部存储路径、工具日志和机器元数据；这些内容仍在完整 JSON 原件中。原文仅做 Markdown / HTML 安全转义，不摘要、不去重、不改变原结论或权限。', '')
  return lines.join('\n')
}
