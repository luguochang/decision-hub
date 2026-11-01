import type { ResearchSessionRequest } from '@decision-hub/contracts-ts'
import { synthesisProtocol } from '../synthesis-protocol.js'
import type { CancellationBinding } from './command-cancellation.js'
import type { ModelAgentLineage } from './dsh-public.js'

const SKILL_TOOL = 'decision_hub_skill'
export type TaskSkillCapability = 'decision' | 'disabled'

/** A real frozen Task protocol decides visibility; this grants no callback rights. */
export function taskSkillCapability(request: Pick<ResearchSessionRequest,
  'output_schema_ref' | 'execution_mode' | 'research_mode'>): TaskSkillCapability {
  let protocol: ReturnType<typeof synthesisProtocol>
  try { protocol = synthesisProtocol(request.output_schema_ref) }
  catch { throw new Error('skill_task_protocol_unknown') }
  // Server SkillPreparation currently selects packages only for V1 decision
  // Tasks. V2 has its own perpetual method and must not request a spot package
  // that was never selected; ordinary research and synthesis stay available.
  if (protocol !== 'decision') return 'disabled'
  return request.execution_mode === 'live' && request.research_mode === 'current_decision' ? 'decision' : 'disabled'
}

type TaskMetadata = Readonly<{ sessionId: string; requestHash: string; capability: TaskSkillCapability }>
type AgentVisibility = { denied?: () => void; guard: () => void; binding?: CancellationBinding }

/** Only SDK agent scopes are restricted. Each new scope starts with no Skill capability. */
export class TaskSkillVisibility {
  private readonly tasks = new Map<string, TaskMetadata>()
  private readonly agents = new Map<ModelAgentLineage, AgentVisibility>()

  constructor(private readonly current: (agent: ModelAgentLineage, binding: CancellationBinding) => boolean) {}

  observed(agent: ModelAgentLineage): boolean { return this.agents.has(agent) }

  has(binding: CancellationBinding): boolean {
    const task = this.tasks.get(binding.runId)
    return task?.sessionId === binding.sessionId && task.requestHash === binding.requestHash
  }

  remember(binding: CancellationBinding, request: Pick<ResearchSessionRequest,
    'run_id' | 'request_id' | 'output_schema_ref' | 'execution_mode' | 'research_mode'>): void {
    if (request.run_id !== binding.runId || request.request_id !== `research-request:${binding.runId}`) {
      throw new Error('skill_task_identity_invalid')
    }
    const metadata = Object.freeze({ sessionId: binding.sessionId, requestHash: binding.requestHash,
      capability: taskSkillCapability(request) })
    const prior = this.tasks.get(binding.runId)
    if (prior && (prior.sessionId !== metadata.sessionId || prior.requestHash !== metadata.requestHash
      || prior.capability !== metadata.capability)) throw new Error('skill_task_identity_changed')
    this.tasks.set(binding.runId, metadata)
  }

  observe(agent: ModelAgentLineage): void {
    if (this.agents.has(agent)) return
    const tools = agent.ctx?.tools
    if (!tools) throw new Error('skill_agent_scope_unavailable')
    // restrict() itself rejects root/plain contexts and unknown global names.
    const denied = tools.restrict({ deny: [SKILL_TOOL] })
    const state: AgentVisibility = { denied, guard: () => {} }
    try {
      state.guard = tools.guard(execution => execution.name !== SKILL_TOOL ? undefined
        : execution.agent !== agent || state.binding === undefined || !this.current(agent, state.binding)
          ? 'skill_root_session_required' : undefined)
      this.agents.set(agent, state)
    } catch (error) { denied(); throw error }
  }

  deny(agent: ModelAgentLineage): void {
    this.observe(agent)
    const state = this.agents.get(agent)!
    state.binding = undefined
    if (!state.denied) state.denied = agent.ctx!.tools.restrict({ deny: [SKILL_TOOL] })
  }

  /** Called before queueing a prompt, hence before the SDK's first assembly. */
  apply(agent: ModelAgentLineage, binding: CancellationBinding): void {
    this.observe(agent)
    const task = this.tasks.get(binding.runId)
    if (agent.id !== binding.sessionId || !this.has(binding) || !this.current(agent, binding)) {
      this.deny(agent)
      throw new Error('skill_task_identity_invalid')
    }
    if (task!.capability !== 'decision') { this.deny(agent); return }
    const state = this.agents.get(agent)!
    state.binding = Object.freeze({ ...binding })
    const lift = state.denied
    state.denied = undefined
    lift?.()
  }

  forgetRun(runId: string): void {
    this.tasks.delete(runId)
    for (const [agent, state] of this.agents) if (state.binding?.runId === runId) this.deny(agent)
  }

  disposed(agent: ModelAgentLineage): void {
    const state = this.agents.get(agent)
    if (!state) return
    this.agents.delete(agent)
    state.binding = undefined
    state.denied?.()
    state.guard()
  }
}
