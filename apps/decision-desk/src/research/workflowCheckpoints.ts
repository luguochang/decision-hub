import type { ResearchRunDetailView } from '../api/types'

const STEP_ID = 'workflow-checkpoints'
const WARNING = 'workflow_checkpoint:final_review_required'

/** Display the server's verified projection; old or incomplete records stay failures. */
type ToolInvocation = ResearchRunDetailView['rounds'][number]['tool_invocations'][number]
type CheckpointView = {
  readonly process_steps: ResearchRunDetailView['process_steps']
  readonly rounds: readonly { readonly tool_invocations: readonly ToolInvocation[] }[]
}
export function workflowCheckpointCallIds(detail: CheckpointView): ReadonlySet<string> {
  const steps = detail.process_steps.filter(step => step.step_id === STEP_ID)
  if (steps.length !== 1) return new Set()
  const step = steps[0]
  if (step.kind !== 'synthesize' || !['completed', 'limited', 'waiting'].includes(step.status)
    || step.warning_codes.length !== 1 || step.warning_codes[0] !== WARNING
    || step.tool_call_refs.length === 0 || new Set(step.tool_call_refs).size !== step.tool_call_refs.length) return new Set()
  const calls = detail.rounds.flatMap(round => round.tool_invocations)
  return new Set(step.tool_call_refs.filter(id => {
    const matches = calls.filter(call => call.tool_call_id === id)
    return matches.length > 0 && matches.every(call => call.status === 'failed'
      && call.error?.tool_call_id === id && call.error.error_code === 'dsh_tool_failed'
      && call.error.cause_code === 'decision_hub_synthesis_submit')
  }))
}

export function reviewStatusText(steps: ResearchRunDetailView['process_steps'], reviewCount: number,
  runStatus: ResearchRunDetailView['run']['status']): string {
  // Zero verified records says nothing about whether a native reviewer ran.
  // Never infer acceptance or non-execution from a missing verified projection.
  if (reviewCount === 0) return runStatus === 'cancelled'
    ? '本次已取消，尚无可核验的独立复核记录' : '尚无可核验的独立复核记录'
  const candidates = steps.filter(step => step.kind === 'review' || step.step_id === 'review')
  if (candidates.length !== 1 || candidates[0].kind !== 'review') return '独立复核状态未核验'
  const step = candidates[0]
  if (step.label === '独立复核' && step.status === 'completed') return '独立复核已通过'
  if (step.label === '自动证据检查' && step.status === 'limited') return '仅自动检查，独立复核未完成'
  if (step.label === '审查（有保留）' && step.status === 'limited') return '审查有保留，尚未通过'
  return '独立复核状态未核验'
}
