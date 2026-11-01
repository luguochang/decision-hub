import { dshRunSessionLinkViewSchema, researchRunDetailViewSchema, reviewScheduleViewSchema, type DshRunSessionLinkView, type ResearchRunDetailView, type ReviewScheduleView } from '@decision-hub/contracts-ts'
import { ApiRequestError, requestResponse } from './client'

export type FollowupFailureInspection = {
  stage: string
  cause: string
  occurredAt: string | null
  execution: string
  recovery: string
}

const stageNames: Record<string, string> = {
  admission: '研究准入', planning: '制定研究计划', acquiring_evidence: '收集证据',
  assessing_sufficiency: '核对证据是否充分', synthesis: '形成研究结论',
  gate: '报告发布核验', monitoring: '后续观察', done: '复查结束记录',
}

export function projectFollowupFailure(plan: ReviewScheduleView, detail: ResearchRunDetailView, link: DshRunSessionLinkView | null): FollowupFailureInspection {
  const run = detail.run
  if (!plan.child_run_id || plan.child_run_id === plan.parent_run_id || plan.status !== 'failed'
    || run.run_id !== plan.child_run_id || run.parent_run_id !== plan.parent_run_id
    || run.admission_origin !== 'scheduled_recheck' || run.status !== 'failed'
    || (link && link.run_id !== run.run_id)) throw new Error('followup_inspection_identity_changed')
  const sequences = new Set<number>()
  for (const event of detail.trace) {
    if (event.run_id !== run.run_id || sequences.has(event.sequence_no)) throw new Error('followup_inspection_trace_identity_invalid')
    sequences.add(event.sequence_no)
  }
  const terminal = detail.trace.filter(event => event.event_type === 'session_stopped').sort((a, b) => b.sequence_no - a.sequence_no)[0]
  if (terminal && (terminal.status !== 'failed' || terminal.error_code !== plan.failure_code
    || terminal.research_session_id !== `product:${run.run_id}` || terminal.reference_type !== 'run' || terminal.reference_id !== run.run_id
    || terminal.sequence_no !== run.latest_sequence_no
    || (run.failure && JSON.stringify(run.failure) !== JSON.stringify(terminal.error))
    || (terminal.error && terminal.error.error_code !== terminal.error_code))) throw new Error('followup_inspection_failure_changed')
  const provenance = terminal?.error_verification || !run.failure ? null : terminal?.error
  const cause = provenance?.origin === 'orchestration' && provenance.error_code === 'research_method_binding_failed'
    && provenance.provider_attempts.length === 0 ? provenance.cause_code : null
  const causeText = cause === 'cancellation_projection_wait_elapsed'
    ? '运行状态核验等待超时。记录未证明底层核验已经结束，也不能据此判断尚未执行。'
    : cause === 'cancellation_projection_busy'
      ? '运行状态核验暂不可用。此原因可能表示等待预算已用完或核验通道仍被占用，现有记录未区分两者。'
      : '现有记录没有可明确展示的具体原因，请到复查任务页核对原失败记录。'
  const execution = !link ? '未读到旧执行的关联记录。'
    : link.terminal_at ? '执行关联记录包含结束时间；安全恢复仍需原任务的认证终态与恢复守卫确认。'
      : link.accepted_at ? '旧执行曾被接收，关联记录尚未确认结束。'
        : '关联记录已登记，尚未确认旧执行被接收或结束。'
  return {
    stage: terminal ? stageNames[terminal.stage] ?? '原任务记录了一个未识别的执行阶段' : '尚无可核对的失败阶段记录',
    cause: causeText,
    occurredAt: terminal?.occurred_at ?? null,
    execution,
    recovery: !link?.terminal_at
      ? '暂不能确认安全恢复：旧执行是否结束尚未确认。请先到复查任务页核对；原任务和失败记录保留。'
      : '本页不授予恢复权限。请到复查任务页重新核验恢复条件；失败原因中的“可重试”不等于允许重复执行。',
  }
}

// Metadata inspection only. It does not authenticate a terminal, retry, or create a Run.
export async function inspectFollowupFailure(planInput: ReviewScheduleView, signal: AbortSignal): Promise<FollowupFailureInspection> {
  const plan = reviewScheduleViewSchema.parse(planInput)
  if (plan.status !== 'failed' || !plan.child_run_id || plan.child_run_id === plan.parent_run_id) throw new Error('followup_inspection_identity_changed')
  const deadline = Date.now() + 30_000
  async function read<T>(path: string, parse: (value: unknown) => T): Promise<T> {
    signal.throwIfAborted()
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw new ApiRequestError('复查记录核对超时。', 'timeout')
    const value = await requestResponse(path, async response => parse(await response.json()), { method: 'GET', signal }, undefined, { timeoutMs: remaining })
    signal.throwIfAborted()
    return value
  }
  async function readPlan() {
    const plans = await read(`/v1/research/runs/${encodeURIComponent(plan.parent_run_id)}/review-plans`, value => reviewScheduleViewSchema.array().parse(value))
    const matching = plans.filter(row => row.review_plan_id === plan.review_plan_id)
    if (plans.some(row => row.parent_run_id !== plan.parent_run_id) || matching.length !== 1
      || JSON.stringify(matching[0]) !== JSON.stringify(plan)) throw new Error('followup_inspection_plan_changed')
  }
  await readPlan()
  const [detail, link] = await Promise.all([
    read(`/v1/research/runs/${encodeURIComponent(plan.child_run_id)}`, value => researchRunDetailViewSchema.parse(value)),
    read(`/v1/dsh/sessions/${encodeURIComponent(plan.child_run_id)}`, value => dshRunSessionLinkViewSchema.parse(value)).catch(error => {
      if (error instanceof ApiRequestError && error.status === 404 && error.message === 'dsh_session_link_not_found') return null
      throw error
    }),
  ])
  await readPlan()
  return projectFollowupFailure(plan, detail, link)
}
