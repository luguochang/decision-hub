import { researchDisplayText } from '../product/researchLanguage'
import type { ResearchRunDetailView } from '../api/types'

export const requirementNames: Record<string, string> = {
  event_identity: '事件身份与原始公告', policy_or_data_delta: '政策或数据变化',
  expectation_pricing: '市场预期与定价', macro_transmission: '宏观传导数据',
  market_transmission: '宏观传导数据', cross_asset_confirmation: '跨资产确认',
  crypto_spot_confirmation: '现货行情确认', derivatives_crowding: '衍生品与拥挤度',
  market_regime_context: '日线趋势背景', market_narrative: '最新公开信息', counter_thesis: '反向解释',
  usd_reserve_balances: '美元准备金余额',
  real_yield_10y: '美国10年期实际收益率',
  inflation_compensation_10y: '10年期通胀补偿',
}
export const capabilityNames: Record<string, string> = {
  'market.cross_asset': '跨资产行情', 'market.crypto_derivatives': '现货与衍生品行情',
  'market.crypto_crowding': '订单簿与拥挤度', 'data.historical': '历史行情',
  'web.search': '搜索公开资料', 'web.fetch': '读取公开网页', 'document.read': '读取文档',
  'browser.read': '浏览器读取', 'dsh.internal.skill': '加载研究方法',
  'browser.readback': '浏览器回读', 'document.readback': '文档回读',
  'dsh.internal.decision_hub_synthesis_submit': '提交综合结果',
  'dsh.internal.interrupt_agent': '结束专家协作', 'dsh.internal.list_agents': '查看协作状态',
  'dsh.internal.subagent_reviewer': '专家复核',
  'dsh.internal.todo_write': '更新任务清单', 'dsh.internal.todo_read': '读取任务清单',
  'dsh.internal.task': '专家协作', 'dsh.internal.research': '研究工具',
}
export const technicalLabels: Record<string, string> = {
  sufficient: '材料充分', insufficient: '材料不足', conflicted: '材料冲突',
  partial: '部分充分', unavailable: '不可用', causal: '已形成论据', bounded: '有保留', bounded_fallback: '有保留的图谱结果',
  accepted: '已采纳', candidate: '待核验', rejected: '未采纳', stale: '已过期',
  market: '行情数据', document: '文档材料', web: '网页材料', calendar: '事件日历',
  official: '官方来源', exchange: '交易所', audited_aggregator: '经检查的聚合来源',
  primary: '原始来源', secondary: '二手来源', unknown: '未核验',
  hard: '必需材料', soft: '补充材料', missing: '尚未取得',
  insufficient_sources: '来源数量不足', insufficient_fields: '关键字段不足',
  conflict: '材料冲突', future_data: '材料晚于截止时间',
  critical_data_unavailable: '关键数据仍不可用', sufficient_evidence: '已达到材料要求',
  independent_review_not_accepted: '独立复核尚未接受',
  capability_exhausted: '可用取证路径已用尽', budget_exhausted: '已达到预算上限',
  deadline_reached: '已达到任务时限', owner_cancelled: '用户已停止',
  no_progress: '连续两轮没有研究进展', no_actionable_path: '剩余问题没有可执行的取证路径',
  synthesis_incomplete: '综合结果未满足提交要求', mixed: '尚有未解决的证据冲突',
  legacy_provider_attempts_unverifiable: '历史调用次数未能核验，请查看原始记录',
  cost_budget: '已达到估算费用预算', tool_budget: '已达到工具或协作次数上限',
  round_budget: '已达到取证轮次上限', runtime_unavailable: '研究执行器不可用', cancelled: '已取消',
  done: '任务结束', planning: '制定计划', acquiring_evidence: '取得证据',
  assessing_sufficiency: '检查材料', reviewing: '复核', synthesizing: '综合判断',
  replanning: '调整计划', gating: '发布检查', running: '进行中', succeeded: '成功',
  failed: '失败', degraded: '有保留',
  session_started: '研究开始', plan_created: '计划已保存', task_started: '任务开始',
  model_step_started: '模型步骤开始', model_step_completed: '模型步骤结束',
  tool_started: '工具调用开始', tool_completed: '工具调用完成', tool_failed: '工具调用失败',
  subagent_started: '专家协作开始', subagent_completed: '专家协作结束',
  evidence_accepted: '证据已采纳', evidence_rejected: '材料未采纳',
  coverage_assessed: '材料覆盖已检查', replan: '计划已调整', round_completed: '本轮结束',
  synthesis_started: '综合判断开始', session_stopped: '本次研究结束',
  fact: '事实', inference: '推断', scenario: '情景', independent_reviewer: '独立复核',
  reviewer_receipt: '复核回执', automated_check: '自动检查',
  trade_not_authorized: '交易未授权', probability_uncalibrated: '概率尚未校准',
  directional_value_unvalidated: '方向效果尚未验证',
  research_document_not_found: '来源文档不存在', research_capability_timeout: '取证调用超时',
  capability_deadline: '取证调用达到本次执行期限', dsh_model_step_timeout: '模型步骤达到原执行期限',
  research_acquisition_closing_reserve_reached: '新取证请求达到收尾预留界限',
  original_closing_time_reserved: '保留原任务时间用于整理与复核',
  research_pit_violation: '材料时间未通过检查', dsh_sdk_unavailable: '研究运行环境不可用',
  research_provider_native_context_invalid: '研究输入核验未通过',
  research_observation_unavailable: '研究原件未确认',
  research_requirement_not_found: '请求的材料要求未登记',
  research_capability_mode_denied: '取证工具不支持本次执行模式',
  research_capability_not_enabled: '取证工具尚未启用',
  research_baseline_http_refused: '基础材料请求被拒绝',
  research_capability_not_audited: '取证工具尚未通过准入检查',
  research_domain_denied: '来源域名不在本次许可范围',
  research_source_unknown: '来源尚未登记', research_source_not_approved: '来源尚未获准使用',
  provider_timeout: '服务调用超时', provider_transport_unavailable: '服务连接不可用',
  research_worker_failed: '研究执行未完成，请展开 AI 执行记录核对首个失败原因',
  research_status_storage_pending: '研究运行状态未能落库确认',
  dsh_status_initial_writer_busy: '研究状态写入的初始数据库锁繁忙',
  research_method_binding_failed: '研究运行身份核验未完成',
  dsh_model_binding_budget_elapsed: '模型准备核验超时',
  cancellation_projection_wait_elapsed: '研究原件核验等待时限耗尽',
  dsh_web_deadline_elapsed: '原研究执行时限已到',
  research_context_selection_deadline_elapsed: '本次研究的原执行期限已到',
  cancellation_projection_busy: '研究状态核验暂不可执行',
  original_preparation_deadline_elapsed: '报告原件核验超过任务时限',
  dsh_provider_input_transport_timeout: '模型输入记录回传超时',
  dsh_provider_emission_transport_timeout: '模型输出记录回传超时',
  dsh_provider_finish_transport_timeout: '最终结果回传确认超时',
  dsh_host_read_transport_timeout: '研究上下文读取超时',
  dsh_host_read_deadline_elapsed: '任务剩余时间不足以读取研究上下文',
  dsh_host_read_cancelled: '研究上下文读取已停止',
  dsh_session_not_found: '研究执行会话未找到',
  dsh_native_session_lost: '研究执行会话在服务恢复后已不存在',
  dsh_session_recovery_failed: '研究执行会话恢复失败',
  provider_route_unavailable: '没有获准且支持该请求的服务路径',
  provider_request_rejected: '服务拒绝请求', provider_execution_failed: '服务处理失败',
  orchestration: '任务编排层', dsh_web_readiness_deadline_elapsed: '任务剩余时间不足以确认 DSH 已就绪',
  dsh_evidence_unattested: '研究材料未通过执行凭据核验',
  semantic_mismatch: '材料不满足所需事实字段或指标要求', low_authority: '来源权威等级不足',
  no_baseline: '缺少事件前的比较基准', window_missing: '缺少所需时间窗口材料',
  transport: '连接层', provider: '服务提供方', runtime: '运行环境',
  deadline: '超过时限', snapshot: '触发快照', coverage: '覆盖检查',
  capability_call: '取证调用', evidence: '证据', stop_reason: '结束原因',
  run: '任务记录', round: '研究轮次', task: '取证任务', review: '复核记录',
  synthesis: '综合结果', gateway: '取证入口', synthesis_attestation: '综合结果核验',
}
export function technicalName(value: string | null | undefined, fallback = '未提供') {
  return value ? technicalLabels[value] ?? requirementNames[value] ?? capabilityNames[value] ?? value : fallback
}
// Use only beside a retained original record: unknown codes are not invented translations.
export function technicalSummaryName(value: string | null | undefined, fallback = '未提供', unknown = '尚无中文说明，见原始记录') {
  if (!value) return fallback
  if (value === 'Research execution stopped without a committable result.') return '本次研究尚未形成可提交的完整结果。'
  return technicalLabels[value] ?? requirementNames[value] ?? capabilityNames[value]
    ?? (/[\u3400-\u9fff]/.test(value) ? researchDisplayText(value) : unknown)
}

export const observationUnavailableExplanation = '执行原始记录尚未核验通过，本次研究未完成。已有任务与材料继续保留；核验服务恢复后，可从原任务发起关联的新研究。'
export const originalPreparationDeadlineExplanation = '后台原件核验未能在本次任务时限内完成，未发布完整报告。已收集材料和失败记录保留；修复收尾耗时后可发起关联的新研究。'
export const modelBindingWriterBusyExplanation = '模型调用前取得数据库写锁时暂时繁忙，本次没有写入这次模型绑定，也未生成完整报告。已收集证据和原任务保留；确认服务恢复后可发起关联的新研究，不会自动重试原任务。'
export const statusStoragePendingExplanation = '初始数据库写锁繁忙，研究运行状态未能在本次等待时限内落库确认。本次未生成可提交的完整报告，原任务、证据与失败记录保留；请先核对原执行和存储状态。'

export const researchExecutionDeadlineExplanation = '本次研究的原执行时限已到，尚未形成可提交的完整结果。已收集材料与失败记录保留。'
export const researchBindingProofWaitExplanation = '本次原件核验等待已达到时限，后台未确认可以继续研究。此原因本身不能证明整个研究时限已到；原任务、已取得材料与失败记录保留。'

type AcquisitionClosingError = Pick<NonNullable<ResearchRunDetailView['run']['failure']>, 'error_code' | 'cause_code' | 'origin' | 'retryable'>
export const acquisitionClosingSummary = '新取证已截止，正在等待整理与独立复核结果；已有证据保留。'
export const acquisitionClosingRecordSummary = '本次研究保留取证收尾阶段的拒绝记录；完成情况见报告与审查记录。'
export const acquisitionClosingExplanation = '此次新取证请求被拒绝，为整理与独立复核保留原任务时间；该调用仍记失败，不允许重试。整理与复核是否完成，请结合本次报告和审查记录核对。'
export function isAcquisitionClosingReserve(error: AcquisitionClosingError | null | undefined) {
  return error?.error_code === 'research_acquisition_closing_reserve_reached'
    && error.cause_code === 'original_closing_time_reserved'
    && error.origin === 'gateway' && error.retryable === false
}

export function failureExplanation(errorCode: string, causeCode: string | null, error?: AcquisitionClosingError | null) {
  if (errorCode === 'dsh_model_binding_budget_elapsed' || causeCode === 'dsh_model_binding_budget_elapsed') return '研究在模型准备核验阶段超时，未完成本次分析。原任务与失败记录已保留；请确认问题修复后，再决定是否明确发起新研究，请勿重复提交当前任务。'
  if (errorCode === 'research_baseline_http_refused') return '本次研究的基础材料请求被拒绝，尚未形成可提交的完整结果。原任务与失败记录保留；具体拒绝原因请展开原始任务记录核对。'
  if (errorCode === 'dsh_protocol_invalid' && (causeCode === 'decision_synthesis_attestation_invalid' || causeCode === 'perpetual_synthesis_attestation_invalid')) return '研究结果的完整性核验未通过，本次报告未发布。已收集的材料与调用记录保留，可在问题详情中核对。'
  if (errorCode === 'dsh_native_session_lost' || causeCode === 'dsh_native_session_lost') return '服务恢复后已无法找到本次原生研究会话，本次原任务已停止，系统没有重放模型请求。已取得材料和失败记录保留；请从原记录发起关联的新研究。'
  if (errorCode === 'dsh_session_recovery_failed' || causeCode === 'dsh_session_recovery_failed') return '研究执行会话未能恢复，本次原任务已停止，系统没有把它显示为成功。已取得材料和失败记录保留；请先核对服务状态，再从原记录发起关联的新研究。'
  if (errorCode === 'dsh_session_not_found' || causeCode === 'dsh_session_not_found') return '研究执行会话状态无法从 DSH 服务读取，本次未形成可用报告。已取得材料和失败记录保留；请核对服务恢复记录后，从原记录发起关联的新研究。'
  if (error?.error_code === errorCode && error.cause_code === causeCode && isAcquisitionClosingReserve(error)) return acquisitionClosingExplanation
  if (errorCode === 'provider_timeout' && (causeCode === 'dsh_web_deadline_elapsed' || causeCode === 'research_context_selection_deadline_elapsed')) return researchExecutionDeadlineExplanation
  if (errorCode === 'research_method_binding_failed' && causeCode === 'original_preparation_readset_changed') return '研究执行期间的原件核对发现变化，未能确认继续执行的身份；已保留原任务、材料与失败记录，尚未形成完整报告。请先核对原记录及运行状态。'
  if (errorCode === 'research_method_binding_failed' && causeCode === 'cancellation_projection_wait_elapsed') return researchBindingProofWaitExplanation
  if (errorCode === 'research_status_storage_pending' && causeCode === 'dsh_status_initial_writer_busy') return statusStoragePendingExplanation
  if (errorCode === 'research_method_binding_failed' && causeCode === 'cancellation_projection_busy') return '研究运行状态核验未完成，后台尚未确认可以继续执行。原任务与较早的异常记录保留；需先核对已有执行，此提示不表示报告完成或允许重复发起研究。'
  if (errorCode === 'dsh_model_binding_writer_busy' || causeCode === 'dsh_model_binding_writer_busy') return modelBindingWriterBusyExplanation
  if (causeCode === 'original_preparation_deadline_elapsed') return originalPreparationDeadlineExplanation
  if (errorCode === 'research_observation_unavailable' || causeCode === 'research_observation_unavailable') return observationUnavailableExplanation
  return errorCode === 'dsh_evidence_unattested' && causeCode === 'synthesis_attestation' ? '综合结果的证据校验失败：模型引用了可信输入和实际工具结果以外的材料。' : null
}

// Display the original record clock; this does not attest when execution began.
export function researchRecordTime(value: string | null | undefined) {
  const unknown = (label: string) => ({ label, dateTime: undefined, instantMs: null })
  if (!value) return unknown('时间未知（未提供）')
  if (typeof value !== 'string') return unknown('时间未知（格式或时区无法核验）')
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-](\d{2}):(\d{2}))$/.exec(value)
  if (!parts) return unknown('时间未知（格式或时区无法核验）')
  const [year, month, day, hour, minute, second] = parts.slice(1, 7).map(Number)
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]
    || hour > 23 || minute > 59 || second > 59 || parts[7] === '-00:00'
    || (parts[7] !== 'Z' && (Number(parts[8]) > 23 || Number(parts[9]) > 59))) {
    return unknown('时间未知（格式或时区无法核验）')
  }
  const date = new Date(value), instantMs = date.getTime()
  if (!Number.isFinite(instantMs)) return unknown('时间未知（格式或时区无法核验）')
  const pad = (number: number, width = 2) => String(number).padStart(width, '0')
  const offset = -date.getTimezoneOffset(), absoluteOffset = Math.abs(offset)
  const zone = `UTC${offset >= 0 ? '+' : '-'}${pad(Math.floor(absoluteOffset / 60))}:${pad(absoluteOffset % 60)}`
  const label = `${pad(date.getFullYear(), 4)}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}（本地 ${zone}）`
  return { label, dateTime: value, instantMs }
}

export function researchTraceTimeNotice(recordedAt: string | null | undefined, runCreatedAt: string | null | undefined) {
  const record = researchRecordTime(recordedAt), created = researchRecordTime(runCreatedAt)
  return record.instantMs !== null && created.instantMs !== null && record.instantMs < created.instantMs
    ? '这条记录时间早于任务创建；请核对时间口径，不能据此判断实际执行开始。' : null
}

type WaitingTrace = ResearchRunDetailView['trace'][number]
export const preparationWaitingText = '原任务与材料保留，等待原租约到期后核对检查点恢复；无需重复提交。'

// This describes a saved trace. It does not attest the checkpoint, lease or an execution grant.
export function isPreparationWaitingTrace(event: WaitingTrace, runId: string) {
  return event.schema_version === 'research-trace-event.v1'
    && event.run_id === runId && event.research_session_id === `product:${runId}`
    && event.reference_type === 'run' && event.reference_id === runId
    && event.event_type === 'session_stopped' && event.status === 'running' && event.stage === 'planning'
    && event.error_code === 'research_method_binding_failed'
    && event.error?.error_code === event.error_code && event.error.origin === 'orchestration'
    && event.error.cause_code === 'cancellation_projection_wait_elapsed' && event.error.retryable === true
}

export function currentPreparationWaiting(detail: Pick<ResearchRunDetailView, 'run' | 'trace'> | null | undefined) {
  if (!detail) return null
  const { run } = detail, traces = detail.trace ?? []
  if (run.status !== 'researching' || run.stage !== 'planning' || run.artifact_id !== null || run.stop_reason !== null) return null
  const candidates = traces.filter(event => event.sequence_no === run.latest_sequence_no)
  if (candidates.length !== 1 || traces.some(event => event.sequence_no > run.latest_sequence_no)) return null
  const event = candidates[0], failure = run.failure, error = event.error
  if (!isPreparationWaitingTrace(event, run.run_id) || !failure || !error || run.current_action !== event.summary) return null
  if (failure.error_code !== error.error_code || failure.origin !== error.origin || failure.cause_code !== error.cause_code
    || failure.retryable !== error.retryable || failure.capability_id !== error.capability_id
    || failure.tool_call_id !== error.tool_call_id || failure.deadline_ms !== error.deadline_ms
    || JSON.stringify(failure.provider_attempts) !== JSON.stringify(error.provider_attempts)) return null
  return event
}

export function researchTraceTitle(event: WaitingTrace, runId: string, fallback = technicalSummaryName(event.event_type)) {
  return isPreparationWaitingTrace(event, runId) ? '执行准备尚未确认' : fallback
}

export function warningName(value: string) {
  const gap = /^gap:([^:]+):(.+)$/.exec(value)
  if (gap) return `${requirementNames[gap[1]] ?? '其他材料要求'}：${technicalSummaryName(gap[2])}`
  return value.startsWith('review:') ? `复核${technicalSummaryName(value.slice(7))}` : technicalSummaryName(value)
}
export function directionLabel(value: string | null) {
  return value === null ? '尚无记录' : ({ bullish: '偏多', bearish: '偏空', range: '区间', unresolved: '未形成方向', long: '偏多', short: '偏空', neutral: '中性观察', no_trade: '未形成方向' } as Record<string, string>)[value] ?? value
}
export function boundedEvidenceText(value: string) {
  const text = researchDisplayText(value.replace(/\s+/g, ' ').trim())
  if (/[a-zA-Z]{3}/.test(text) && !/[\u4e00-\u9fff]/.test(text)) return '材料以英文保存，尚无中文摘要；请展开原始记录核对内容。'
  return text.length <= 480 ? text : `${text.slice(0, 477)}…`
}
const numericFields: Record<string, string> = {
  spot_price: '现货价格', spot_volume: '现货成交量（来源口径）',
  funding_rate: '资金费率', funding: '资金费率', basis: '期现基差',
  open_interest: '未平仓量（来源口径）', open_interest_change: '未平仓量变化',
  oi_change: '未平仓量变化', mark_price: '标记价格', index_price: '指数价格',
  ask_depth: '卖方深度（来源口径）', bid_depth: '买方深度（来源口径）',
  book_imbalance: '买卖盘失衡度', crowding_signal: '拥挤度指标',
  period_close: '末日收盘', row_count: '日线数量', level: '指标值',
  change_5d: '五个交易日变化（来源口径）', return_5d: '五个交易日变化率（来源口径）',
}

type SavedEvidence = Pick<ResearchRunDetailView['evidence'][number], 'kind' | 'source_id' | 'excerpt' | 'requirement_id'>

// Describe the saved provider contract; freshness and acceptance remain backend decisions.
function hasSavedYahooDailyContract(evidence: SavedEvidence) {
  if (evidence.kind !== 'market' || evidence.source_id !== 'yahoo-finance-public') return false
  try {
    const data = JSON.parse(evidence.excerpt)
    return data?.provider === evidence.source_id && data?.completion_contract === 'yahoo-provider-declared-daily.v2'
  } catch { return false }
}

export function savedDailyRequirementLabel(evidence: SavedEvidence) {
  if (!hasSavedYahooDailyContract(evidence)) return null
  if (['macro_transmission', 'market_transmission'].includes(evidence.requirement_id)) return '宏观传导参考（日线）'
  return evidence.requirement_id === 'cross_asset_confirmation' ? '跨资产参考（日线）' : null
}

export function missingEvidencePublicationText(evidence: SavedEvidence) {
  if (hasSavedYahooDailyContract(evidence)) return '来源未提供发布时间；按已保存的日线区间核对时效'
  return evidence.kind === 'market'
    ? '未保存可核验的发布时间；时效以已保存核验记录为准'
    : '未保存可核验的发布时间；不能用读取时间代替'
}

function numericText(value: unknown) {
  if ((typeof value !== 'string' && typeof value !== 'number') || String(value).trim() === '') return null
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  if (number === 0 && /[1-9]/.test(String(value).split(/[eE]/)[0])) return String(value)
  // Keep tiny nonzero rates visible; exact precision remains in the original record.
  return Math.abs(number) > 0 && Math.abs(number) < 0.000001
    ? number.toExponential(4) : number.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
}
export function evidenceSummary(value: string, sourceId?: string) {
  const normalized = value.trim()
  if (!normalized) return '暂无证据摘要'
  if (normalized.startsWith('{') || normalized.startsWith('[')) {
    try {
      const data = JSON.parse(normalized)
      if (data?.dataset_id === 'crypto.btc_usdt.ohlcv_1d' && data.summary && Array.isArray(data.actual_coverage)) {
        const parts = ['比特币现货日线', `数据日期：${String(data.actual_coverage[0] ?? '未知').slice(0, 10)} 至 ${String(data.actual_coverage[1] ?? '未知').slice(0, 10)}`]
        if (data.summary.row_count != null) parts.push(`${data.summary.row_count} 根日线`)
        if (data.summary.period_close != null) parts.push(`末日收盘 ${numericText(data.summary.period_close) ?? data.summary.period_close} USDT`)
        parts.push(data.coverage_status === 'complete' ? '分区覆盖完整' : '分区覆盖有限，需核对缺口')
        return parts.join(' · ')
      }
      const fields: string[] = []
      if (typeof data?.symbol === 'string') fields.push(data.symbol)
      else if (typeof data?.instrument === 'string') fields.push(researchDisplayText(data.instrument))
      const values = data?.values && typeof data.values === 'object' ? data.values : data
      // The Yahoo adapter emits yield_percent, bps and relative yield-level percent for US10Y.
      // Do not scale these stored values or describe relative yield changes as bond returns.
      if (data?.provider === 'yahoo-finance-public' && data?.instrument === 'US10Y'
        && (sourceId === undefined || sourceId === data.provider)) {
        for (const [key, label, unit] of [
          ['level', '收益率水平', '%'],
          ['change_5d', '五个交易日变动', ' 基点'],
          ['return_5d', '收益率水平相对变化', '%'],
        ] as const) {
          const number = numericText(values?.[key])
          if (number !== null) fields.push(`${label} ${number}${unit}`)
        }
        if (typeof data?.latest_date === 'string') fields.push(`数据日期 ${data.latest_date}`)
        return fields.join(' · ')
      }
      if (data?.provider === 'yahoo-finance-public' && ['DXY', 'SPX', 'VIX'].includes(data?.instrument)
        && (sourceId === undefined || sourceId === data.provider)) {
        const level = numericText(values?.level)
        const change = numericText(values?.change_5d)
        const relative = numericText(values?.return_5d)
        if (level !== null) fields.push(`指数水平 ${level} 点`)
        // This provider emits both fields as the same percentage for these indices.
        // Collapse only exact aliases; divergent originals remain visible.
        if (change !== null && relative !== null && Object.is(values.change_5d, values.return_5d)) {
          fields.push(`五个交易日变化率 ${relative}%`)
        } else {
          if (change !== null) fields.push(`五个交易日变动 ${change}%`)
          if (relative !== null) fields.push(`五个交易日变化率 ${relative}%`)
          if (change !== null && relative !== null) fields.push('两个变动字段不一致，请核对原始记录')
        }
        if (typeof data?.latest_date === 'string') fields.push(`数据日期 ${data.latest_date}`)
        return fields.join(' · ')
      }
      function collectMetrics(values: unknown, depth = 0) {
        if (!values || typeof values !== 'object' || Array.isArray(values) || depth > 3) return
        for (const [key, label] of Object.entries(numericFields)) {
          const number = numericText((values as Record<string, unknown>)[key])
          if (number !== null) fields.push(`${label} ${number}`)
        }
        for (const [key, child] of Object.entries(values)) {
          if (!['source_urls', 'metadata'].includes(key)) collectMetrics(child, depth + 1)
        }
      }
      collectMetrics(values)
      if (typeof data?.latest_date === 'string') fields.push(`数据日期 ${data.latest_date}`)
      if (fields.length) return fields.slice(0, 8).join(' · ')
      return '已保存结构化材料，指标含义与精确数值请查看原始记录。'
    } catch { return '已保存原始材料，当前摘要格式无法解析；请展开核对原文。' }
  }
  if (/<[a-z][\s\S]*>/i.test(normalized) && typeof DOMParser !== 'undefined') {
    const document = new DOMParser().parseFromString(normalized, 'text/html')
    document.querySelectorAll('script, style, nav, header, footer, aside').forEach((node) => node.remove())
    return boundedEvidenceText(document.body.textContent ?? '')
  }
  return boundedEvidenceText(normalized)
}
