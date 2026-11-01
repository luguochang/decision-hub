import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluatePerpetualFinalReviewGate, trustedSynthesisEvidence } from '../src/final-review-gate.js'
import { providerCanonicalJson } from '../src/host/provider-input.js'
import { activeRootMessages, coverageAt, coverageHarness, nativeEvent, objectDigest,
  receiptAttributes, textDigest } from './finalized-coverage-support.js'

// Deliberately exercise existing production entry points. Before R5j, positive
// cases reach a matched real v4 finalize and fail the expected v5 behavior;
// neither missing-module imports nor a fabricated WeakSet source are RED.
beforeEach(() => vi.spyOn(Date, 'now').mockReturnValue(coverageAt))
afterEach(() => vi.restoreAllMocks())

describe('authenticated child coverage in the prepared synthesis native return', () => {
  it('delivers complete child facts and actual native attribution without copying root-only evidence', async () => {
    const h = await coverageHarness()
    try {
      expect(trustedSynthesisEvidence(h.context).evidence.map(item => item.evidence_id))
        .toEqual(['child-daily-evidence', 'root-only-evidence'])
      expect(h.childResult.evidence_candidates[0]!.tool_call_id).toBe('provider-query-daily')
      const before = providerCanonicalJson(h.context.events)
      const final = await h.finalize()
      expect(final.review_gate.status).toBe('matched')
      expect(final.candidate).toEqual(h.prepared.candidate)
      expect(final.preparation).toEqual(h.prepared.preparation)
      expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v5')
      const wire = final.coverage_delivery!
      expect(Object.keys(wire).sort()).toEqual(['binding', 'child_prefixes', 'evidence_candidates', 'evidence_sources',
        'facts', 'finalize_call_id', 'finalize_call_seq', 'root_cutoff', 'schema_version', 'source_snapshot'])
      expect(wire.schema_version).toBe('decision-hub-finalized-coverage.v1')
      expect(wire.binding).toEqual(h.context.binding)
      expect(wire.evidence_candidates).toEqual(h.childResult.evidence_candidates)
      expect(wire.facts).toEqual(h.childResult.facts)
      expect(wire.facts).toHaveLength(9)
      expect(wire.facts.every(fact => providerCanonicalJson(fact.attributes) === providerCanonicalJson(receiptAttributes))).toBe(true)
      expect(wire.evidence_sources).toEqual([{ evidence_id: 'child-daily-evidence', capability_id: 'data.historical',
        execution_session_id: 'synthetic-authenticated-research-child', native_call_id: 'actual-child-native-call' }])
      expect(wire.child_prefixes).toEqual(h.context.childResearchSources!.map(source => source.prefix))
      expect(wire.finalize_call_id).toBe('finalize')
      expect(wire.finalize_call_seq).toBe(h.finalCall.seq)
      expect(wire.root_cutoff).toEqual({ before_seq: h.finalCall.seq,
        events_sha256: objectDigest(h.context.events.filter(event => event.seq < h.finalCall.seq)) })
      expect(providerCanonicalJson(h.context.events)).toBe(before)
    } finally { h.dispose() }
  })

  it('retains the complete executed v5 witness in durable tool content', async () => {
    const h = await coverageHarness()
    try {
      const final = await h.finalize()
      expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v5')
      const content = await h.synthesis.finalizeContent!(h.exec('finalize'), { value: final, isError: false } as never)
      expect(content).toEqual([{ type: 'text', text: JSON.stringify(final) }])
      expect(Buffer.byteLength(JSON.stringify(final), 'utf8')).toBeLessThanOrEqual(1_000_000)
      expect(JSON.parse((content![0] as { text: string }).text).coverage_delivery.facts[0].attributes)
        .toEqual(receiptAttributes)
    } finally { h.dispose() }
  })

  it('uses authenticated child evidence even when its facts array is empty', async () => {
    const h = await coverageHarness({ facts: false })
    try {
      const final = await h.finalize()
      expect(final.review_gate.status).toBe('matched')
      expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v5')
      expect(final.coverage_delivery!.facts).toEqual([])
      expect(final.coverage_delivery!.evidence_candidates.map(item => item.evidence_id)).toEqual(['child-daily-evidence'])
    } finally { h.dispose() }
  })

  it('does not require a new evidence ID when root and authenticated child return the same accepted content', async () => {
    const h = await coverageHarness({ duplicateRoot: true })
    try {
      expect(trustedSynthesisEvidence(h.context).evidence).toHaveLength(1)
      const final = await h.finalize()
      expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v5')
      expect(final.coverage_delivery!.evidence_candidates).toEqual(h.childResult.evidence_candidates)
      expect(final.coverage_delivery!.facts).toEqual(h.childResult.facts)
      expect(final.coverage_delivery!.evidence_sources[0]!.execution_session_id).toBe(h.child.childId)
    } finally { h.dispose() }
  })

  it.each(['none', 'candidate'] as const)('keeps the old v4 when the child is %s rather than an accepted native contribution', async child => {
    const h = await coverageHarness({ child })
    try {
      const final = await h.finalize()
      expect(final.review_gate.status).toBe('matched')
      expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v4')
      expect(Object.keys(final).sort()).toEqual(['candidate', 'preparation', 'review_gate', 'review_request', 'schema_version'])
      expect(final).not.toHaveProperty('coverage_delivery')
      expect(final.candidate).toEqual(h.prepared.candidate)
      expect(JSON.stringify(await h.finalize())).toBe(JSON.stringify(final))
    } finally { h.dispose() }
  })
})

describe('coverage uses the actual active declaration and frozen child namespace', () => {
  it.each([1, 2])('uses the active SDK replacement after %i legal replacements despite historical identical child prefixes', async replacementCount => {
    const h = await coverageHarness({ replacementCount })
    try {
      const history = h.context.events.filter(event => event.type === 'user/message'
        && (event.data.source as { plugin?: string } | undefined)?.plugin === 'decision-hub-protected-context')
      expect(history).toHaveLength(replacementCount + 1)
      expect(history.map(event => JSON.parse((event.data.content as { text: string }[])[1]!.text).child_research_prefixes)
        .every(prefixes => providerCanonicalJson(prefixes) === providerCanonicalJson(h.declared()!.prefixes))).toBe(true)
      const active = activeRootMessages(h.context).filter(message => message.source.kind === 'plugin')
      expect(active).toHaveLength(1)
      expect(active[0]!.id).not.toBe(h.declaration.first.id)
      const final = await h.finalize()
      expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v5')
      const activeEvent = h.context.events.find(event => event.type === 'user/message' && event.data.id === active[0]!.id)!
      expect(final.coverage_delivery!.source_snapshot).toEqual({ message_id: active[0]!.id,
        message_seq: activeEvent.seq, body_sha256: textDigest((active[0]!.content[1] as { text: string }).text) })
      expect(final.coverage_delivery!.child_prefixes).toEqual(h.declared()!.prefixes)
    } finally { h.dispose() }
  })

  it('does not import later authenticated child progress beyond the delivered prefix', async () => {
    const h = await coverageHarness()
    try {
      const lateEvidence = { ...h.childResult.evidence_candidates[0]!, evidence_id: 'late-undelivered-evidence',
        tool_call_id: 'provider-query-late', content_hash: '0'.repeat(64) }
      const later = [...h.child.events,
        nativeEvent(3, 'tool/call', { callId: 'actual-later-call', name: 'decision_hub_research',
          arguments: { capability_id: 'data.historical', request_id: 'declared-late-query' } }),
        nativeEvent(4, 'tool/result', { callId: 'actual-later-call', result: { ...h.childResult,
          request_id: 'declared-late-query', evidence_candidates: [lateEvidence], facts: [] } },
        { surfaceOp: 'append', sourceEventSeqs: [3] })]
      const pinned = await h.child.read(h.declared()!.prefixes, later)
      h.context.childResearchSources = pinned
      h.context.admittedEvidence.push(lateEvidence)
      const final = await h.finalize()
      expect(final.schema_version).toBe('decision-hub-synthesis-tool-result.v5')
      expect(final.coverage_delivery!.child_prefixes).toEqual(h.declared()!.prefixes)
      expect(final.coverage_delivery!.evidence_candidates.map(item => item.evidence_id)).toEqual(['child-daily-evidence'])
    } finally { h.dispose() }
  })

  it('does not accept a fabricated child object solely because its prefix and approval look correct', async () => {
    const h = await coverageHarness()
    try {
      h.context.childResearchSources = structuredClone(h.context.childResearchSources)
      await expect(h.finalize()).rejects.toThrow('research_provider_native_context_invalid')
    } finally { h.dispose() }
  })

  it('rejects an otherwise approved finalization when its only source declaration is after the actual call', async () => {
    const h = await coverageHarness()
    try {
      const declaration = h.context.events.find(event => event.type === 'user/message'
        && event.data.id === h.declaration.active.id)!
      declaration.seq = h.finalCall.seq + 1
      declaration.time = h.finalCall.time + 1
      // Existing approval still matches: the new source-cutoff check must be
      // what rejects this, rather than a missing module or broken review setup.
      expect(evaluatePerpetualFinalReviewGate(h.context, h.prepared.candidate, h.prepared.review_request, 'finalize').status)
        .toBe('matched')
      await expect(h.finalize()).rejects.toThrow()
    } finally { h.dispose() }
  })
})
