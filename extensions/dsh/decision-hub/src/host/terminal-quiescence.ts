import { z } from 'zod'
import { CommandCancellation, freezeCommandBinding, readCommandQuiescence, sameCommandBinding, type CancellationBinding } from './command-cancellation.js'
import type { HostContextPublic, ModelAgentLineage } from './dsh-public.js'

const hash = z.string().regex(/^[a-f0-9]{64}$/)
export const terminalAuthoritySchema = z.object({
  schema_version: z.literal('dsh-terminal-queue-authority.internal.v1'),
  purpose: z.enum(['before_failed_terminal', 'acknowledged_terminal']),
  run_id: z.string().min(1), dsh_session_id: z.string().min(1), generation: z.number().int().min(1),
  request_hash: hash, original_hash: hash, terminal_hash: hash.nullable(), hub_status: z.string().min(1),
  lease_owner: z.string().nullable(), lease_expires_at: z.string().datetime({ offset: true }).nullable(),
  native_state: z.string().min(1), terminal_at: z.string().datetime({ offset: true }).nullable(),
  observed_at: z.string().datetime({ offset: true }),
}).strict()
export type TerminalAuthority = z.infer<typeof terminalAuthoritySchema>

/** Its latch is separate from explicit owner cancellation and cannot relabel a failure. */
export class TerminalQuiescence {
  private readonly drains = new Map<string, CommandCancellation>()
  private readonly closing = new Set<string>()
  private readonly activeFailureReads = new Map<AbortController, { binding: CancellationBinding; cancelRequested: boolean }>()
  constructor(private readonly ctx: HostContextPublic,
    private readonly authority: (runId: string, signal?: AbortSignal, originalDeadlineAt?: string) => Promise<TerminalAuthority>,
    private readonly current: (binding: CancellationBinding) => boolean,
    private readonly streams: (ids: readonly string[]) => boolean) {}

  closingForSession(id: string): boolean { return this.closing.has(id) }

  /** Cooperatively stop this binding after its owned authority response closes. */
  requestCancel(binding: CancellationBinding): void {
    // A fetch abort is not acknowledgement that the API owned native Future
    // exited. Keep its one HTTP response until completion; valid() refuses any
    // subsequent native side effect once this local intent has been requested.
    for (const expected of this.activeFailureReads.values()) {
      if (sameCommandBinding(binding, expected.binding)) expected.cancelRequested = true
    }
  }

  assertModelAdmission(sessionId: string | undefined): void {
    if (!sessionId || this.closing.size === 0) return
    const visited = new Set<string>()
    let id: string | undefined = sessionId
    while (id) {
      if (this.closing.has(id)) throw new Error('host_activity_terminal_drain_pending')
      if (visited.has(id) || visited.size >= 128) throw new Error('host_activity_lineage_invalid')
      visited.add(id)
      const agent: ModelAgentLineage | undefined = this.ctx.agents?.get(id)
      if (agent && agent.id !== id) throw new Error('host_activity_lineage_invalid')
      if (!agent && !/^dsh_[a-f0-9]{64}$/.test(id)) throw new Error('host_activity_lineage_unavailable')
      id = agent?.session?.header?.parentSession
    }
  }

  async drain(binding: CancellationBinding, purpose: TerminalAuthority['purpose'], timeoutMs: number,
    expected?: TerminalAuthority, originalDeadlineAt?: string) {
    const controller = new AbortController()
    // Native pre-failure cleanup keeps the accepted Task ceiling; already
    // acknowledged historical cleanup keeps its independent bounded contract.
    const originalCeiling = purpose === 'before_failed_terminal' ? originalDeadlineAt : undefined
    const deadline = originalCeiling === undefined ? Date.now() + timeoutMs : Date.parse(originalCeiling)
    const remainingAtStart = deadline - Date.now()
    if (!Number.isFinite(deadline) || !Number.isSafeInteger(Math.floor(remainingAtStart))
      || remainingAtStart <= 0 || remainingAtStart > 2_147_483_647) throw new Error('host_terminal_drain_deadline_elapsed')
    const timer = setTimeout(() => controller.abort(), remainingAtStart)
    const failureRead = { binding: freezeCommandBinding(binding), cancelRequested: false }
    if (purpose === 'before_failed_terminal') this.activeFailureReads.set(controller, failureRead)
    try {
      const original = await this.authority(binding.runId, controller.signal, originalCeiling)
      const valid = (proof: TerminalAuthority) => {
        // The explicit failure request binds the original owner/lease/native
        // tuple as well as immutable source hashes. A new owner cannot inherit it.
        if (expected !== undefined && ((Object.keys(proof) as (keyof TerminalAuthority)[])
          .some(key => key !== 'observed_at' && proof[key] !== expected[key])
          || Date.parse(expected.observed_at) > Date.parse(proof.observed_at))) {
          throw new Error('host_terminal_drain_authority_changed')
        }
        if (controller.signal.aborted || failureRead.cancelRequested || !this.current(binding) || proof.purpose !== purpose
          || proof.run_id !== binding.runId || proof.dsh_session_id !== binding.sessionId
          || proof.generation !== binding.generation || proof.request_hash !== binding.requestHash
          || proof.original_hash !== original.original_hash || proof.hub_status !== original.hub_status
          || proof.terminal_hash !== original.terminal_hash
          || proof.lease_owner !== original.lease_owner || proof.terminal_at !== original.terminal_at
          || (purpose === 'acknowledged_terminal' && (proof.lease_owner !== null || proof.lease_expires_at !== null
            || proof.terminal_hash === null
            || !['completed','degraded','research_only','rejected','failed','cancelled'].includes(proof.hub_status)
            || proof.terminal_at === null || !['completed','failed','cancelled'].includes(proof.native_state)))
          || (purpose === 'before_failed_terminal' && (proof.hub_status !== 'running'
            || proof.terminal_hash !== null
            || proof.lease_owner === null || proof.lease_expires_at === null
            || Date.parse(proof.lease_expires_at) <= Date.now() || proof.terminal_at !== null))) {
          throw new Error('host_terminal_drain_authority_changed')
        }
      }
      valid(original)
      let quiet = await readCommandQuiescence(this.ctx, this.ctx.sessionController, binding.sessionId, controller.signal)
      valid(await this.authority(binding.runId, controller.signal, originalCeiling))
      if (purpose === 'before_failed_terminal' || quiet === null || this.streams([binding.sessionId, ...quiet.descendantIds])) {
        const key = `${binding.runId}:${binding.generation}:${binding.requestHash}`
        let drain = this.drains.get(key)
        if (drain === undefined || drain.readyForFreshQuiescence(binding, this.ctx)) {
          if (drain === undefined && this.drains.size >= 256) throw new Error('host_terminal_drain_capacity')
          drain = new CommandCancellation()
          this.drains.set(key, drain)
        }
        this.closing.add(binding.sessionId)
        drain.begin(binding)
        const remaining = deadline - Date.now()
        if (remaining <= 0) throw new Error('host_terminal_drain_pending')
        await drain.stop(this.ctx, binding, remaining)
        valid(await this.authority(binding.runId, controller.signal, originalCeiling))
        quiet = await readCommandQuiescence(this.ctx, this.ctx.sessionController, binding.sessionId, controller.signal)
      }
      valid(await this.authority(binding.runId, controller.signal, originalCeiling))
      if (quiet === null || this.streams([binding.sessionId, ...quiet.descendantIds])) {
        throw new Error('host_terminal_drain_pending')
      }
      this.closing.delete(binding.sessionId)
      this.drains.delete(`${binding.runId}:${binding.generation}:${binding.requestHash}`)
      return { schema_version: 'dsh-terminal-queue-receipt.internal.v1' as const,
        run_id: binding.runId, dsh_session_id: binding.sessionId, generation: binding.generation,
        request_hash: binding.requestHash, original_hash: original.original_hash, terminal_hash: original.terminal_hash,
        native_state: original.native_state, terminal_at: original.terminal_at,
        last_seq: quiet.lastSeq, observed_at: new Date().toISOString(), quiet: true as const }
    } finally {
      this.activeFailureReads.delete(controller)
      clearTimeout(timer); controller.abort()
    }
  }
}
