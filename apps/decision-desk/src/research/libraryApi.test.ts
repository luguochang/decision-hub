import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { analystClaimSchema, researchDocumentVersionSchema } from '../api/schemas'
import { libraryCommands } from './libraryApi'

afterEach(() => vi.restoreAllMocks())
const date = '2026-10-01T00:00:00Z'
const version = researchDocumentVersionSchema.parse({
  schema_version: 'research-document-version.v1', version_id: 'docver_recovered', document_id: 'doc_source', original_file_ref: 'document://original', original_file_hash: 'a'.repeat(64),
  status: 'received', correction_status: 'not_requested', published_at: null, observed_at: date, received_at: date, created_at: date, supersedes_version_id: 'docver_failed', parse_receipt_ref: null, corrected_content_ref: null, block_count: 0, claim_count: 0,
  quality: { layout_coverage: 0, text_readability: 0, table_integrity: 0, image_coverage: 0, numeric_suspicion: 0, encoding_errors: 0, locator_completeness: 0, quality_status: 'needs_review', issues: [] },
})
const claim = analystClaimSchema.parse({
  schema_version: 'analyst-claim.v1', claim_id: 'claim_local', version_id: 'docver_recovered', claim_type: 'opinion', stance: 'bullish', claim_text: 'BTC看多', supporting_quote: 'BTC看多原句', locator_refs: ['document://p1'], market_scope: 'crypto_macro', instrument_scope: ['BTC-USDT'], horizon: null, conditions: [], invalidation_conditions: [], status: 'accepted', attribution_confidence: 'low', requires_independent_evidence: true, created_at: date, reviewed_at: date,
})
const response = (value: unknown) => new Response(JSON.stringify(value), { status: 200 })

it('sends the exact owner, key, failed version and original hash for recovery', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(version))
  expect(await libraryCommands.recover('docver_failed', version.original_file_hash, 'document-recovery:stable')).toEqual(version)
  const [path, init] = fetch.mock.calls[0]
  expect(path).toBe('/v1/research-library/versions/docver_failed/recover')
  expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner')
  expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('document-recovery:stable')
  expect(JSON.parse(String(init?.body))).toEqual({ schema_version: 'document-recovery-command.v1', request_id: 'document-recovery:stable', original_file_hash: version.original_file_hash })
})

it.each([
  { ...version, supersedes_version_id: 'docver_other' },
  { ...version, original_file_hash: 'b'.repeat(64) },
  { ...version, version_id: 'docver_failed' },
])('rejects an unrelated or reused recovery receipt before a journal can acknowledge it', async result => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(result))
  await expect(libraryCommands.recover('docver_failed', version.original_file_hash, 'document-recovery:stable')).rejects.toThrow('回执与这份原文不匹配')
})

it('sends the fixed review decision and retains independent evidence in the validated receipt', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(claim))
  expect((await libraryCommands.reviewClaim(claim.claim_id, 'accept', 'owner_confirmed', 'claim-review:stable')).requires_independent_evidence).toBe(true)
  const [path, init] = fetch.mock.calls[0]
  expect(path).toBe('/v1/research-library/claims/claim_local/review')
  expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner')
  expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('claim-review:stable')
  expect(JSON.parse(String(init?.body))).toEqual({ schema_version: 'claim-review-command.v1', request_id: 'claim-review:stable', decision: 'accept', reason: 'owner_confirmed' })
})

it.each([{ ...claim, claim_id: 'claim_other' }, { ...claim, status: 'rejected' }])('rejects a claim receipt for another identity or decision', async result => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(result))
  await expect(libraryCommands.reviewClaim(claim.claim_id, 'accept', 'owner_confirmed', 'claim-review:stable')).rejects.toThrow('审核回执与这条观点不匹配')
})

it('keeps existing direct upload, import, correction and review callers authorized', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async path => response(String(path).includes('/claims/') ? claim : version))
  const metadata = { schema_version: 'document-upload.v1' as const, title: 'PC isolated test', source_type: 'private_file' as const, source_profile_id: null, author: null, published_at: null, market_scopes: ['crypto_macro' as const], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only' as const, visibility: 'private' as const, supersedes_version_id: null, notes: null }
  await api.uploadResearchDocument(new File(['source'], 'source.txt'), metadata)
  await api.importResearchAcquisition('receipt_local', metadata)
  await api.requestResearchCorrection('docver_recovered')
  await api.acceptResearchCorrection('docver_recovered')
  await api.reviewResearchClaim('claim_local', 'accept', 'owner_confirmed', 'claim-review:legacy-stable')
  for (const [, init] of fetch.mock.calls) expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner')
  const review = fetch.mock.calls[4][1]
  expect(new Headers(review?.headers).get('Idempotency-Key')).toBe('claim-review:legacy-stable')
  expect(JSON.parse(String(review?.body)).request_id).toBe('claim-review:legacy-stable')
})

it('keeps the default Owner identity and stable request key when admitting library research', async () => {
  const receipt = { schema_version: 'research-run-queued.v1', event_id: 'event_isolated', run_id: 'run_isolated', status: 'queued', status_url: '/v1/runs/run_isolated' }
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(receipt))
  const payload: Parameters<typeof api.submitResearchWithLibrary>[0] = { schema_version: 'research-request.v1', research_family: 'crypto_macro', instrument_scope: ['BTC-USDT'], focus: 'PC isolated acceptance', library_context_mode: 'selected', allow_remote_library_content: true, selected_version_ids: ['docver_recovered'] }
  expect(await api.submitResearchWithLibrary(payload, 'library-research:stable')).toEqual(receipt)
  const [path, init] = fetch.mock.calls[0]
  expect(path).toBe('/v1/research-library/research')
  expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner')
  expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('library-research:stable')
  expect(JSON.parse(String(init?.body))).toEqual(payload)
})
