import { createHash } from 'node:crypto'
import { dshModelDispatchBindingSchema } from '@decision-hub/contracts-ts'
import type { HostContextPublic, ModelDispatchOptions } from './dsh-public.js'
import { HubClientError, type HubClient } from './hub-client.js'
import { modelBindingErrorCode } from './model-errors.js'
import type { DshModelDispatchBinding } from '@decision-hub/contracts-ts'
import type { HostScope } from './harness-execution.js'
import { prepareProviderInput, recordedProviderStream, type ProviderInputHub, type ProviderStreamStage } from './provider-input.js'
import { verifyRetainedRootContext, type RetainedContextReader } from './retained-context.js'

const managedSession = /^dsh_[a-f0-9]{64}$/

/** Only Host-owned session headers can establish the Run lineage. */
function modelLineage(sessionId: string | undefined, agents: HostContextPublic['agents']) {
  if (!sessionId) return null
  if (managedSession.test(sessionId)) return { root: sessionId, parent: null }
  let current = agents?.get(sessionId)
  if (!current) return null // No registered research Agent; unrelated Host request.
  if (current.id !== sessionId) throw new HubClientError('dsh_model_dispatch_lineage_invalid', false)
  const parent = current.session?.header?.parentSession
  if (!parent) return null
  const visited = new Set<string>([sessionId])
  for (let depth = 0; depth < 128; depth++) {
    const nextId: string | undefined = current.session?.header?.parentSession
    if (!nextId) return null // A complete unrelated tree is outside the Hub Run.
    if (visited.has(nextId)) break
    if (managedSession.test(nextId)) return { root: nextId, parent }
    visited.add(nextId)
    current = agents?.get(nextId)
    if (!current || current.id !== nextId) break
  }
  throw new HubClientError('dsh_model_dispatch_lineage_invalid', false)
}

/** Persist/verify each managed conversation before delegating its first token. */
export async function* guardedModelStream(
  options: ModelDispatchOptions,
  next: () => AsyncIterable<unknown>,
  hub: Pick<HubClient, 'linkBySession' | 'bindModel'> & Partial<ProviderInputHub & Pick<HubClient, 'modelBindingBudgetMs'>>,
  buildHash: string,
  agents?: HostContextPublic['agents'],
  consumeHarness?: (payload: DshModelDispatchBinding, options: ModelDispatchOptions, originalDeadlineAt?: string) => Promise<void>,
  providerScope?: () => Promise<HostScope>,
  retainedContext?: RetainedContextReader,
  afterRecordedInput?: (input: Awaited<ReturnType<typeof prepareProviderInput>>, signal: AbortSignal) => Promise<unknown>,
  originalStepDeadline?: (binding: DshModelDispatchBinding, executionSessionId: string) => string,
  beforeProviderDelegate?: (binding: DshModelDispatchBinding, executionSessionId: string) => void,
  onProviderStage?: (stage: ProviderStreamStage, input: Awaited<ReturnType<typeof prepareProviderInput>>) => void,
): AsyncIterable<unknown> {
  let input: Awaited<ReturnType<typeof prepareProviderInput>> | undefined
  let delegateBinding: DshModelDispatchBinding | undefined
  // Compaction/title have independent policies and remain explicitly unbound.
  if (options.purpose === undefined) {
    try {
      options.signal?.throwIfAborted()
      const lineage = modelLineage(options.sessionId, agents)
      if (lineage) {
        const link = options.signal === undefined ? await hub.linkBySession(lineage.root)
          : await hub.linkBySession(lineage.root, options.signal)
        if (link.dsh_session_id !== lineage.root) throw new HubClientError('dsh_model_dispatch_identity_mismatch', false)
        const payload = dshModelDispatchBindingSchema.parse({
          schema_version: 'dsh-model-dispatch-binding.v1',
          run_id: link.run_id, dsh_session_id: link.dsh_session_id,
          request_hash: link.request_hash, generation: link.generation,
          plugin_build_hash: buildHash, purpose: 'conversation',
          execution_session_id: lineage.parent ? options.sessionId : null,
          parent_session_id: lineage.parent,
          provider: options.provider, model: options.model,
          reasoning_effort: options.reasoningEffort ?? null,
          temperature: options.temperature ?? null, max_tokens: options.maxTokens ?? null,
          // Stop strings may contain application data. Record only their identity.
          stop_sequences_hash: options.stop === undefined ? null
            : createHash('sha256').update(JSON.stringify(options.stop)).digest('hex'),
        })
        // This immutable preparation deadline is captured before binding or
        // retained-context IO. The Provider watchdog starts at the later,
        // explicit receipt-to-delegate boundary.
        const stepDeadline = originalStepDeadline?.(payload, options.sessionId!)
        if (link.harness_required === true) {
          if (!consumeHarness) throw new HubClientError('harness_root_witness_unavailable', false)
          await consumeHarness(payload, options, stepDeadline)
        } else if (stepDeadline !== undefined) await hub.bindModel(payload, options.signal, undefined, stepDeadline)
        else if (options.signal === undefined) await hub.bindModel(payload)
        else await hub.bindModel(payload, options.signal)
        options.signal?.throwIfAborted()
        // The live Bridge always supplies its authenticated actual workspace
        // scope. The standalone legacy helper/Harness ABI remains unchanged.
        if (providerScope) {
          if (!hub.recordProviderInput || !hub.recordProviderToolEmission || !hub.recordProviderFinish) {
            throw new HubClientError('dsh_provider_input_unavailable', false)
          }
          const scope = await providerScope()
          if (lineage.parent === null && retainedContext) {
            const context = await retainedContext(payload, link, options.signal, options.messages)
            verifyRetainedRootContext(options, payload, link, context)
          }
          // Preparation and its durable receipt use the Task ceiling. The live
          // Bridge starts its model window only at the delegate hook below.
          input = await prepareProviderInput(hub as ProviderInputHub, payload, options, scope, options.signal, 60_000, link.deadline_at, stepDeadline)
          delegateBinding = payload
          try { onProviderStage?.('receipt_completed', input) } catch { /* Best-effort structured observation only. */ }
          if (afterRecordedInput) await afterRecordedInput(input, options.signal ?? new AbortController().signal)
        }
      }
    } catch (error) {
      // DSH serializes ordinary thrown middleware errors as UNKNOWN. Its public
      // finish protocol preserves this bounded cause without leaking transport data.
      const code = (error instanceof HubClientError ? modelBindingErrorCode(error.code) : null)
        ?? (error instanceof Error && /^skill_[a-z_]+$/.test(error.message) ? error.message : 'dsh_model_binding_unavailable')
      yield { type: 'finish', reason: { kind: 'error', failure: {
        code: code.toUpperCase(), message: `Model submission blocked: ${code}`,
      } } }
      return
    }
  }
  options.signal?.throwIfAborted()
  if (input) {
    try {
      yield* recordedProviderStream(options, next, hub as ProviderInputHub, input,
        beforeProviderDelegate && delegateBinding
          ? () => beforeProviderDelegate(delegateBinding!, options.sessionId!)
          : undefined,
        onProviderStage ? stage => onProviderStage(stage, input!) : undefined)
    }
    catch (error) {
      const code = error instanceof HubClientError ? modelBindingErrorCode(error.code) : null
      if (!code) throw error
      yield { type: 'finish', reason: { kind: 'error', failure: {
        code: code.toUpperCase(), message: `Model evidence blocked: ${code}`,
      } } }
    }
  }
  else yield* next()
}
