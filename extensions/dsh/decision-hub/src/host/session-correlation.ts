import { createHash } from 'node:crypto'
import type { DshRunSessionLinkView, DshSessionSubmit } from '@decision-hub/contracts-ts'

export type LocalState = 'admitted' | 'running' | 'idle' | 'completed' | 'failed' | 'cancelled' | 'unknown'
const DEFAULT_MODEL_STEP_TIMEOUT_MS = 150_000

export interface Correlation {
  runId: string
  requestHash: string
  sessionId: string
  requestId: string
  generation: number
  /** Host-owned watchdog budget for each DSH model step. */
  modelStepTimeoutMs: number
  /** Set before invoking the official cancel seam on watchdog expiry. */
  modelStepTimeoutTriggered: boolean
  state: LocalState
  lastSeq: number
  errorCode: string | null
  acceptedAt: string | null
  terminalAt: string | null
}

export function deterministicIds(runId: string, requestHash: string, generation = 1): { sessionId: string; requestId: string } {
  if (!Number.isInteger(generation) || generation < 1) throw new Error('host_generation_invalid')
  const digest = createHash('sha256')
    .update(`dsh-host-bridge.v1\0${runId}\0${requestHash}`)
    .digest('hex')
  const requestDigest = generation === 1 ? digest : createHash('sha256')
    .update(`dsh-host-turn.v1\0${runId}\0${requestHash}\0${generation}`)
    .digest('hex')
  return { sessionId: `dsh_${digest}`, requestId: `req_${requestDigest}` }
}

export class SessionCorrelations {
  private readonly byRun = new Map<string, Correlation>()
  private readonly runBySession = new Map<string, string>()

  admit(submit: DshSessionSubmit): { correlation: Correlation; duplicate: boolean } {
    const ids = deterministicIds(submit.run_id, submit.request_hash, submit.generation)
    if (ids.sessionId !== submit.deterministic_session_id || ids.requestId !== submit.deterministic_request_id) {
      throw new Error('host_deterministic_id_mismatch')
    }
    const existing = this.byRun.get(submit.run_id)
    if (existing !== undefined) {
      if (existing.requestHash !== submit.request_hash || existing.sessionId !== submit.deterministic_session_id) {
        throw new Error('host_session_conflict')
      }
      if (existing.generation === submit.generation) {
        if (existing.requestId !== submit.deterministic_request_id) throw new Error('host_session_conflict')
        return { correlation: existing, duplicate: true }
      }
      if (submit.generation !== existing.generation + 1 || existing.state !== 'completed') {
        throw new Error('host_generation_conflict')
      }
      existing.requestId = submit.deterministic_request_id
      existing.generation = submit.generation
      existing.modelStepTimeoutMs = submit.model_step_timeout_ms
      existing.modelStepTimeoutTriggered = false
      existing.state = 'admitted'
      existing.errorCode = null
      existing.acceptedAt = null
      existing.terminalAt = null
      return { correlation: existing, duplicate: false }
    }
    const otherRun = this.runBySession.get(submit.deterministic_session_id)
    if (otherRun !== undefined && otherRun !== submit.run_id) throw new Error('host_session_conflict')
    const correlation: Correlation = {
      runId: submit.run_id,
      requestHash: submit.request_hash,
      sessionId: submit.deterministic_session_id,
      requestId: submit.deterministic_request_id,
      generation: submit.generation,
      modelStepTimeoutMs: submit.model_step_timeout_ms,
      modelStepTimeoutTriggered: false,
      state: 'admitted',
      lastSeq: 0,
      errorCode: null,
      acceptedAt: null,
      terminalAt: null,
    }
    this.byRun.set(correlation.runId, correlation)
    this.runBySession.set(correlation.sessionId, correlation.runId)
    return { correlation, duplicate: false }
  }

  recover(view: DshRunSessionLinkView): Correlation {
    const ids = deterministicIds(view.run_id, view.request_hash, view.generation)
    if (ids.sessionId !== view.dsh_session_id) throw new Error('host_session_conflict')
    const correlation: Correlation = {
      runId: view.run_id,
      requestHash: view.request_hash,
      sessionId: view.dsh_session_id,
      requestId: ids.requestId,
      generation: view.generation,
      // A watchdog reason is persisted in the link's error_code, allowing a
      // restarted Host to retain the same terminal semantics.
      // The submit payload is not part of the durable link view yet. Keep a
      // bounded recovery default until the next generation re-submits its
      // canonical budget instead of disabling the watchdog after restart.
      modelStepTimeoutMs: DEFAULT_MODEL_STEP_TIMEOUT_MS,
      modelStepTimeoutTriggered: view.error_code === 'dsh_model_step_timeout',
      state: view.state,
      lastSeq: view.last_seq,
      errorCode: view.error_code,
      acceptedAt: view.accepted_at,
      terminalAt: view.terminal_at,
    }
    this.byRun.set(correlation.runId, correlation)
    this.runBySession.set(correlation.sessionId, correlation.runId)
    return correlation
  }

  getRun(runId: string): Correlation | undefined { return this.byRun.get(runId) }
  getSession(sessionId: string): Correlation | undefined {
    const runId = this.runBySession.get(sessionId)
    return runId === undefined ? undefined : this.byRun.get(runId)
  }
}
