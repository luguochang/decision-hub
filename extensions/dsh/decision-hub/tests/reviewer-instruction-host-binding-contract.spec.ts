import { expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dshSessionResultSchema } from '@decision-hub/contracts-ts'
import { matchesAcknowledgedResult, projectInspection } from '../src/host/session-result.js'
import { acknowledgedLink, fixture } from './support/reviewer-instruction-host-binding.js'

it('accepts the explicit typed Host binding field and preserves omission on legacy wire', () => {
  const f = fixture(), result = projectInspection(f.correlation, f.inspection).result!
  const binding = { schema_version: 'dsh-reviewer-instruction-bindings.v1', run_id: f.correlation.runId,
    dsh_session_id: f.correlation.sessionId, generation: 1, request_id: f.correlation.requestId,
    request_hash: f.correlation.requestHash, bindings: [{ reviewer_call_id: 'review-negative', reviewer_call_seq: 3,
      reviewer_generation: 1, reviewer_request_id: f.correlation.requestId,
      parent_prefix_last_seq: 2, parent_prefix_sha256: 'c'.repeat(64),
      baseline_event: { seq: 1, time: f.inspection.events[1]!.time, type: 'user/message', data: f.inspection.events[1]!.data } }] }
  expect(dshSessionResultSchema.safeParse({ ...result, reviewer_instruction_bindings: binding }).success).toBe(true)
  const legacy = { ...result } as Record<string, unknown>
  delete legacy.reviewer_instruction_bindings
  expect(Object.hasOwn(dshSessionResultSchema.parse(legacy), 'reviewer_instruction_bindings')).toBe(false)
})

it('retains concrete native projection through generated Python result DTO and fresh Host reread', () => {
  const f = fixture(), result = projectInspection(f.correlation, f.inspection).result!, link = acknowledgedLink(f, result)
  const root = fileURLToPath(new URL('../../../../', import.meta.url))
  const proof = JSON.parse(execFileSync(`${root}/.venv/Scripts/python.exe`, ['-c',
    'import sys,json; from packages.contracts_py.decision_hub_contracts.generated.dsh_host_bridge import DshSessionResult; r=DshSessionResult.model_validate_json(sys.stdin.read()); print(json.dumps(r.model_dump(mode="json")["reviewer_instruction_bindings"]))'],
  { cwd: root, input: JSON.stringify(result), encoding: 'utf8', windowsHide: true }))
  expect(matchesAcknowledgedResult({ ...result, reviewer_instruction_bindings: proof }, link, f.inspection)).toBe(true)
})

it.each(['null', 'extra', 'empty', 'oversize', 'float-seq', 'unsafe-seq', 'source', 'digest', 'empty-content', 'extra-content'])
('rejects malformed explicit Host projection: %s', mode => {
  const f = fixture(), result = projectInspection(f.correlation, f.inspection).result as any
  const proof = result.reviewer_instruction_bindings, binding = proof.bindings[0]
  if (mode === 'null') result.reviewer_instruction_bindings = null
  if (mode === 'extra') proof.valid = true
  if (mode === 'empty') proof.bindings = []
  if (mode === 'oversize') proof.bindings = Array.from({ length: 101 }, () => binding)
  if (mode === 'float-seq') binding.reviewer_call_seq = 1.1
  if (mode === 'unsafe-seq') binding.reviewer_call_seq = Number.MAX_SAFE_INTEGER + 1
  if (mode === 'source') binding.baseline_event.data.source.kind = 'user'
  if (mode === 'digest') binding.baseline_event.data.source.changes[0].digest = 'invalid'
  if (mode === 'empty-content') binding.baseline_event.data.content = []
  if (mode === 'extra-content') binding.baseline_event.data.content.push({ type: 'text', text: 'injected' })
  expect(dshSessionResultSchema.safeParse(result).success).toBe(false)
})
