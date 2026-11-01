import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { UserMessage } from '@deepseek-ai/dsh-session'
import { researchSessionRequestSchema } from '@decision-hub/contracts-ts'
import { trustedSynthesisEvidence, type SynthesisReviewContext } from '../src/final-review-gate.js'
import { makeRetainedRootContextMessage, verifyRetainedRootContext } from '../src/host/retained-context.js'
import { declaredChildResearchDelivery, readChildResearchSources } from '../src/host/child-research-context.js'
import { restoreFactAttributes } from '../src/host/baseline-projection.js'
import { providerCanonicalJson } from '../src/host/provider-input.js'
import { build, childAttributesFixture, event, fixtureAt, largeAttributes, modelBinding } from './retained-context-child-attributes-support.js'

const V2 = 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v2'
const V3 = 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v3'
const legacy = 'agentic_research.schema.yaml#/$defs/research_session_result'
beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(fixtureAt))
afterEach(() => vi.restoreAllMocks())

interface Body {
  schema_version: string
  evidence_candidates: unknown[]
  facts: Record<string, unknown>[]
  shared_fact_attributes?: { body_hash: string; body: unknown }[]
  child_research_prefixes?: unknown[]
  pending_evidence_delivery?: unknown[]
}
const decode = (message: UserMessage): Body => JSON.parse((message.content[1] as { text: string }).text)
function schema(context: SynthesisReviewContext, ref: string): SynthesisReviewContext {
  return { ...context, request: researchSessionRequestSchema.parse({ ...context.request, output_schema_ref: ref }) }
}
function delivered(context: SynthesisReviewContext, messages: readonly UserMessage[]): SynthesisReviewContext {
  return { ...context, events: [...context.events, ...messages.map((message, index) =>
    event(context.events.at(-1)!.seq + index + 1, 'user/message', { ...message }))] }
}
function verify(context: SynthesisReviewContext, messages: UserMessage[], surface = messages) {
  const current = delivered(context, messages), { dispatch, link } = modelBinding(current)
  return verifyRetainedRootContext({ sessionId: current.binding.sessionId, messages: surface,
    provider: dispatch.provider, model: dispatch.model }, dispatch, link, current)
}

it.each([
  ['V2', V2, 'v5', [{ small: 'complete' }]],
  ['V3', V3, 'v5', [{ small: 'complete' }]],
  ['V2', V2, 'v6', [largeAttributes, largeAttributes]],
  ['V3', V3, 'v6', [largeAttributes, largeAttributes]],
] as const)('delivers authenticated child originals for %s using %s and verifies their actual frozen model bytes', async (_label, ref, version, attributes) => {
  const f = await childAttributesFixture(attributes), context = schema(f.context, ref)
  const originals = providerCanonicalJson({ root: context.events, child: f.childEvents, proof: f.proof })
  const message = makeRetainedRootContextMessage(context, build), body = decode(message)
  expect(body.schema_version).toBe(`decision-hub-protected-context.${version}`)
  expect(body.evidence_candidates).toEqual([f.evidence])
  expect(version === 'v6' ? restoreFactAttributes(body.facts, body.shared_fact_attributes) : body.facts).toEqual(f.result.facts)
  expect(body.child_research_prefixes).toEqual(context.childResearchSources!.map(source => source.prefix))
  expect(body.pending_evidence_delivery).toEqual([{ evidence_id: f.pending.evidence_id,
    requirement_id: f.pending.requirement_id, reason_code: 'native_delivery_pending' }])
  expect(trustedSynthesisEvidence(context).ids).toEqual(['historical-child-evidence', 'synthetic-input'])
  expect(() => verify(context, [message])).not.toThrow()
  const declared = declaredChildResearchDelivery(delivered(context, [message]).events, [message])!
  expect((await f.readSources(declared.prefixes)).map(source => source.prefix)).toEqual(body.child_research_prefixes)
  expect(providerCanonicalJson({ root: context.events, child: f.childEvents, proof: f.proof })).toBe(originals)
})

it.each([
  ['v5', [{ small: 'complete' }]],
  ['v6', [largeAttributes, largeAttributes]],
] as const)('reads an explicit authenticated %s under V3 without depending on fresh V3 selection', async (version, attributes) => {
  const f = await childAttributesFixture(attributes)
  // V2 is an established wire control, independent of the V3 default selector.
  const message = makeRetainedRootContextMessage(f.context, build)
  expect(decode(message).schema_version).toBe(`decision-hub-protected-context.${version}`)
  expect(() => verify(schema(f.context, V3), [message])).not.toThrow()
})

it('keeps explicit historical full v5 readable when fresh V3 would pool the complete attributes into v6', async () => {
  const f = await childAttributesFixture([largeAttributes, largeAttributes])
  const v5Control = await childAttributesFixture([{ small: 'complete' }])
  const template = makeRetainedRootContextMessage(v5Control.context, build)
  const source = makeRetainedRootContextMessage(f.context, build)
  const body = { ...decode(source), schema_version: 'decision-hub-protected-context.v5', facts: f.result.facts }
  delete body.shared_fact_attributes
  const message = { ...source, content: [template.content[0]!,
    { type: 'text' as const, text: providerCanonicalJson(body) }] }
  const original = providerCanonicalJson(message)
  expect(() => verify(schema(f.context, V3), [message])).not.toThrow()
  expect(providerCanonicalJson(message)).toBe(original)
  expect(decode(makeRetainedRootContextMessage(schema(f.context, V3), build)).schema_version)
    .toBe('decision-hub-protected-context.v6')
})

it.each([legacy, 'research_signal.schema.yaml#/$defs/research_decision_synthesis_v1'])(
  'preserves legacy default behavior and rejects child wire for %s', async ref => {
    const f = await childAttributesFixture([{ small: 'complete' }])
    const context = { ...schema(f.context, ref), admittedEvidence: [] }
    const message = makeRetainedRootContextMessage(context, build), body = decode(message)
    expect(body.schema_version).toBe('decision-hub-protected-context.v1')
    expect(body.facts).toEqual([])
    expect(body.child_research_prefixes).toBeUndefined()
    expect(() => verify(context, [message])).not.toThrow()
    const childMessage = makeRetainedRootContextMessage(f.context, build)
    expect(() => verify(schema(f.context, ref), [childMessage])).toThrow('research_provider_native_context_invalid')
  })

it.each(['unsupported-schema', `${V3}-unknown`, 'decision_product_v2.schema.yaml#/$defs/research_decision_synthesis_v4'])(
  'keeps unsupported schema rejection unchanged for %s', async ref => {
    const f = await childAttributesFixture([{ small: 'complete' }])
    const context = { ...schema(f.context, ref), admittedEvidence: [] }
    expect(() => makeRetainedRootContextMessage(context, build)).toThrow('research_provider_native_context_invalid')
    expect(() => verify(context, [makeRetainedRootContextMessage(f.context, build)]))
      .toThrow('research_provider_native_context_invalid')
  })

it('reads historical V3 v1 by its original wire without promoting an undelivered child original', async () => {
  const f = await childAttributesFixture([{ small: 'complete' }])
  const historical = makeRetainedRootContextMessage({ ...schema(f.context, legacy), admittedEvidence: [] }, build)
  const context = schema(f.context, V3), original = providerCanonicalJson(historical)
  expect(() => verify({ ...context, admittedEvidence: [] }, [historical])).not.toThrow()
  expect(() => verify(context, [historical])).toThrow('research_provider_native_context_invalid')
  expect(decode(historical).schema_version).toBe('decision-hub-protected-context.v1')
  expect(providerCanonicalJson(historical)).toBe(original)
})

it('keeps missing V3 child originals pending and rejects losing proof for an already delivered original', async () => {
  const f = await childAttributesFixture([{ small: 'complete' }]), context = schema(f.context, V3)
  const missing = { ...context, childResearchSources: undefined }
  const pending = decode(makeRetainedRootContextMessage(missing, build))
  expect(pending.evidence_candidates).toEqual([])
  expect(pending.facts).toEqual([])
  expect(pending.pending_evidence_delivery).toHaveLength(2)
  expect(trustedSynthesisEvidence(missing).ids).toEqual(['synthetic-input'])
  const message = makeRetainedRootContextMessage(context, build)
  expect(() => verify(missing, [message])).toThrow('research_provider_native_context_invalid')
})

it.each(['copied proof', 'source hash', 'different task'] as const)('does not trust V3 child evidence with %s', async mutation => {
  const f = await childAttributesFixture([{ small: 'complete' }]), context = schema(f.context, V3)
  const changed = mutation === 'copied proof' ? { ...context, childResearchSources: structuredClone(context.childResearchSources) }
    : { ...context, admittedEvidence: [{ ...f.evidence, ...(mutation === 'source hash'
      ? { content_hash: '0'.repeat(64) } : { requirement_id: 'different-task' }) }] }
  expect(() => makeRetainedRootContextMessage(changed, build)).toThrow('research_provider_native_context_invalid')
})

it.each(['run', 'generation', 'parent', 'arguments hash'] as const)('rejects %s changes at child authentication before V3 projection', async mutation => {
  const f = await childAttributesFixture(), context = schema(f.context, V3)
  const prefix = context.childResearchSources![0]!.prefix
  const proof = { ...f.proof, ...(mutation === 'run' ? { run_id: 'foreign-run' }
    : mutation === 'generation' ? { generation: 2 }
      : mutation === 'parent' ? { root_session_id: 'foreign-root' } : { arguments_hash: '0'.repeat(64) }) }
  await expect(readChildResearchSources({ binding: context.binding, descriptors: [prefix],
    signal: new AbortController().signal, deadlineAt: context.request.deadline_at,
    controller: { inspect: async () => ({ meta: { id: prefix.sessionId, parentSession: prefix.parentSessionId }, events: f.childEvents }) },
    hub: { providerToolEmission: async () => proof },
  })).rejects.toThrow('research_provider_native_context_invalid')
})

it.each(['missing', 'duplicate', 'changed native body'] as const)('rejects %s protected V3 markers at frozen dispatch', async mutation => {
  const f = await childAttributesFixture([{ small: 'complete' }]), context = schema(f.context, V3)
  const message = makeRetainedRootContextMessage(context, build)
  if (mutation === 'missing') expect(() => verify(context, [message], [])).toThrow('research_provider_native_context_invalid')
  else if (mutation === 'duplicate') expect(() => verify(context, [message], [message, message])).toThrow('research_provider_native_context_invalid')
  else {
    const changed = { ...message, content: [message.content[0]!, { type: 'text' as const,
      text: providerCanonicalJson({ ...decode(message), evidence_candidates: [] }) }] }
    expect(() => verify(context, [message], [changed])).toThrow('research_provider_native_context_invalid')
  }
})
