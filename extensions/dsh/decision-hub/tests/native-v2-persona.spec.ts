import { readFileSync } from 'node:fs'
import { expect, it } from 'vitest'
import * as gate from '../src/final-review-gate.js'
import { createFrozenReviewerToolDefinition } from '../src/reviewer-tool.js'

it('passes a V2 system persona and V2 user packet into the actual separate reviewer child', async () => {
  const native = JSON.parse(readFileSync(new URL('../../../../artifacts/d2-native-v2/native-transcript.json', import.meta.url), 'utf8'))
  const context = native.context
  context.events = context.events.filter((event: any) => event.seq <= 4)
  const preset = readFileSync(new URL('../../../../infra/dsh/presets/decision-research/agent.cordis.yml', import.meta.url), 'utf8')
  const section = preset.split('- id: tool-subagent-reviewer')[1]!
  const legacyPersona = section.match(/persona: >-\r?\n([\s\S]*?)\r?\n\s+toolFilter:/)![1]!.split(/\r?\n/).map(line => line.trim()).join(' ')
  expect(legacyPersona).toContain('independent-review-receipt.v1')
  const root = {}, dispose = gate.registerSynthesisReviewContext(root, async () => context, async () => context)
  let childDisposed = false, starts = 0
  const definition = createFrozenReviewerToolDefinition({ provider: 'spawn', toolName: 'subagent_reviewer', backgroundMode: 'one-shot',
    persona: legacyPersona, toolFilter: { allow: [] }, maxDepth: 1 }, { root, readQualified: gate.readQualifiedReviewerContext,
    async start(_provider, request): Promise<any> {
      starts += 1
      expect(request.persona).toContain('action-review-authorization.v2')
      expect(request.persona).not.toContain('independent-review-receipt.v1')
      for (const criterion of ['opposing', 'evidence', 'cost', 'method', 'pending_anchor', 'needs_revision', 'untrusted']) expect(request.persona).toContain(criterion)
      expect(request.toolFilter).toEqual({ allow: [] })
      expect(request.maxDepth).toBe(1)
      const prompt = (request.prompt[0] as any).text as string, lines = prompt.split('\n')
      expect(prompt).toContain('action-review-authorization.v2')
      expect(prompt).toContain('BEGIN_AUTHENTICATED_REVIEW_ORIGINALS_JSON')
      const authorization = JSON.parse(lines[lines.indexOf('AUTHORIZATION_FIXED_FIELDS_JSON') + 1]!)
      // A valid independent negative finding must survive; the persona must not
      // turn successful protocol completion into mandatory approval.
      authorization.verdict = 'needs_revision'
      const findings = JSON.parse(lines[lines.indexOf('FINDINGS_FIXED_FIELDS_JSON') + 1]!)
      findings.issues = [{ issue_id: 'persona-negative', candidate_ids: [], field_paths: ['/analysis/thesis'],
        concern: 'Controlled evidence remains insufficient.', requested_change: 'Retain the uncertainty.', evidence_refs: [] }]
      const output = [{ type: 'text', text: JSON.stringify([authorization, findings]) }]
      const events = [{ seq: 1, type: 'user/message', data: { content: request.prompt } },
        { seq: 2, type: 'assistant/message', data: { message: { content: output } } },
        { seq: 3, type: 'turn/end', data: { reason: { kind: 'completed' } } }]
      return { id: 'actual-persona-child', localAgent: { id: 'actual-persona-child', session: { header: { parentSession: context.binding.sessionId }, events } },
        result: Promise.resolve({ stopReason: 'completed', output }), dispose: async () => { childDisposed = true } }
    } })
  try {
    const result: any = await definition.execute({ description: 'Review the V2 frozen packet', prompt: 'untrusted replacement', run_in_background: false },
      { agent: { id: context.binding.sessionId }, callId: 'review', signal: new AbortController().signal } as never)
    expect(result.schema_version).toBe('decision-hub-frozen-review-result.v4')
    expect(JSON.parse(result.original_output.output[0].text)[0].verdict).toBe('needs_revision')
    expect(starts).toBe(1)
    expect(childDisposed).toBe(true)
  } finally { dispose() }
})
