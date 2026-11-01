// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { request } from './client'
import { delivery, productOwner } from './delivery'

vi.mock('./client', () => ({ request: vi.fn() }))
afterEach(() => vi.resetAllMocks())
const receipt = {
  feedback_id: 'feedback-1', request_id: 'request-1', target_type: 'report_revision',
  target_id: 'revision-1', created_by: productOwner, verdict: 'needs_review', notes: '核对原文',
  created_at: '2026-10-01T01:00:00Z',
}

it('sends the original key and full version feedback payload and returns its checked receipt', async () => {
  vi.mocked(request).mockResolvedValue(receipt)
  expect(await delivery.feedback('revision-1', 'needs_review', '核对原文', 'request-1')).toEqual(receipt)
  const [path, init] = vi.mocked(request).mock.calls[0]
  expect(path).toBe('/v1/workbench/feedback')
  expect(init).toMatchObject({ method: 'POST', headers: { 'X-Owner-Id': productOwner, 'Idempotency-Key': 'request-1' } })
  expect(JSON.parse(init!.body as string)).toEqual({ request_id: 'request-1', target_type: 'report_revision', target_id: 'revision-1', created_by: productOwner, verdict: 'needs_review', notes: '核对原文' })
})

it.each([
  { request_id: 'another-key' }, { target_type: 'run' }, { target_id: 'another-revision' },
  { created_by: 'another-owner' }, { verdict: 'incorrect' }, { notes: '另一份内容' },
])('rejects a valid but unrelated receipt: %s', async changed => {
  vi.mocked(request).mockResolvedValue({ ...receipt, ...changed })
  await expect(delivery.feedback('revision-1', 'needs_review', '核对原文', 'request-1')).rejects.toThrow('反馈回执与原请求不一致')
})

it('does not turn an incomplete receipt into success', async () => {
  vi.mocked(request).mockResolvedValue({ feedback_id: 'feedback-1' })
  await expect(delivery.feedback('revision-1', 'needs_review', '核对原文', 'request-1')).rejects.toThrow('反馈回执格式不完整')
})

const historicalEvaluation = () => ({
  schema_version: 'research-revision-feedback-evaluation.v1',
  owner_id: productOwner, revision_id: 'revision-1', feedback: { ...receipt },
  status: 'unavailable', reason: 'legacy_feedback_not_bound',
  confirmation_basis: 'legacy_feedback_receipt_only', binding: null, value: null,
})
const submitted = { revision: 'revision-1', verdict: 'needs_review' as const, notes: '核对原文', id: 'request-1', owner: productOwner }

it('reads the exact frozen feedback with encoded query identity and the original owner', async () => {
  const original = { ...submitted, id: 'report-feedback:/原键' }
  const response = historicalEvaluation()
  response.feedback.request_id = original.id
  vi.mocked(request).mockResolvedValue(response)
  const result = await delivery.feedbackEvaluation(original)
  expect(result.feedback.request_id).toBe(original.id)
  const [path, init, status] = vi.mocked(request).mock.calls[0]
  const url = new URL(path, 'https://test.invalid')
  expect(url.pathname).toBe('/v1/product/reports/revision-1/feedback-evaluation/requests')
  expect(url.searchParams.get('request_id')).toBe(original.id)
  expect(init?.headers).toEqual({ 'X-Owner-Id': productOwner })
  expect(status).toBe(200)
})

it('keeps owner latest absence separate from an exact missing or corrupt receipt', async () => {
  vi.mocked(request).mockResolvedValue(null)
  expect(await delivery.latestFeedbackEvaluation('revision-1')).toBeNull()
  await expect(delivery.feedbackEvaluation(submitted)).rejects.toThrow('评估记录格式不完整')
  vi.mocked(request).mockRejectedValue(new Error('API 404'))
  await expect(delivery.feedbackEvaluation(submitted)).rejects.toThrow('API 404')
  expect(vi.mocked(request).mock.calls[0][0]).toBe('/v1/product/reports/revision-1/feedback-evaluation')
})

it.each(['owner', 'revision', 'request', 'verdict', 'notes', 'schema'])('refuses an unrelated or malformed bound lookup: %s', async damage => {
  const response = historicalEvaluation()
  if (damage === 'owner') response.owner_id = 'another-owner'
  if (damage === 'revision') response.revision_id = 'another-revision'
  if (damage === 'request') response.feedback.request_id = 'another-request'
  if (damage === 'verdict') response.feedback.verdict = 'useful'
  if (damage === 'notes') response.feedback.notes = 'another opinion'
  if (damage === 'schema') response.schema_version = 'unknown'
  vi.mocked(request).mockResolvedValue(response)
  await expect(delivery.feedbackEvaluation(submitted)).rejects.toThrow()
})

it('never looks up another owner or fabricates a basis for an old unbound feedback', async () => {
  await expect(delivery.feedbackEvaluation({ ...submitted, owner: 'another' })).rejects.toThrow('所属用户')
  expect(request).not.toHaveBeenCalled()
  vi.mocked(request).mockResolvedValue(historicalEvaluation())
  const result = await delivery.feedbackEvaluation(submitted)
  vi.mocked(request).mockClear()
  await expect(delivery.feedbackEvaluationBasis(result)).rejects.toThrow('历史反馈未记录')
  expect(request).not.toHaveBeenCalled()
})

function unavailableEvaluation() {
  const basisHash = 'a'.repeat(64)
  return {
    ...historicalEvaluation(), reason: 'source_invalid', confirmation_basis: 'stored_feedback_evaluation_binding',
    binding: { binding_id: 'binding-1', recorded_order: 1, source_run_id: 'run-1', artifact_id: 'artifact-1',
      evaluation_version: `research-value.revision-feedback.v1:${basisHash}`, basis_ref: 'basis-1', basis_hash: basisHash,
      feedback_payload_hash: 'b'.repeat(64), value_payload_hash: null, binding_hash: 'c'.repeat(64), recorded_at: receipt.created_at,
      validation_issues: ['source_invalid'], source_witnesses: [] },
  }
}

it('refuses another source Run even when the unavailable binding has a valid shape', async () => {
  vi.mocked(request).mockResolvedValue(unavailableEvaluation())
  await expect(delivery.latestFeedbackEvaluation('revision-1', 'another-run')).rejects.toThrow('这一版报告')
})

it('reads an original basis only from its exact owner revision request and stored binding tuple', async () => {
  vi.mocked(request).mockResolvedValue(unavailableEvaluation())
  const original = await delivery.feedbackEvaluation(submitted, 'run-1')
  const basis = {
    schema_version: 'research-revision-feedback-evaluation-basis.v1', owner_id: productOwner,
    revision_id: 'revision-1', request_id: 'request-1', binding_id: 'binding-1', basis_ref: 'basis-1',
    basis_hash: 'a'.repeat(64), basis_json: '{"original":true}',
  }
  vi.mocked(request).mockClear().mockResolvedValue(basis)
  expect(await delivery.feedbackEvaluationBasis(original)).toEqual(basis)
  const [path, init, status] = vi.mocked(request).mock.calls[0]
  expect(path).toBe('/v1/product/reports/revision-1/feedback-evaluation/basis?request_id=request-1')
  expect(init?.headers).toEqual({ 'X-Owner-Id': productOwner })
  expect(status).toBe(200)
  for (const damage of [{ owner_id: 'another' }, { revision_id: 'another' }, { request_id: 'another' }, { binding_id: 'another' }, { basis_hash: 'f'.repeat(64) }]) {
    vi.mocked(request).mockResolvedValue({ ...basis, ...damage })
    await expect(delivery.feedbackEvaluationBasis(original)).rejects.toThrow('这笔原反馈')
  }
})
