import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { uploadMetadata, uploadReceipt } from './uploadTestFixtures'

afterEach(() => vi.restoreAllMocks())
const intent = { schema_version: 'document-upload-intent.v1' as const, owner_id: 'owner', file_name: 'source.pdf', file_size_bytes: 7, file_sha256: 'a'.repeat(64), metadata: uploadMetadata }
const receipt = uploadReceipt(intent, 'document-upload:stable')
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })

it('sends full intent, original file, owner and stable request key to the receipt endpoint', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(receipt, 202))
  expect(await api.uploadResearchDocumentReceipt(new File(['content'], 'source.pdf'), intent, receipt.request_id)).toEqual(receipt)
  const [path, init] = fetch.mock.calls[0]
  expect(path).toBe('/v1/research-library/uploads')
  expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner')
  expect(new Headers(init?.headers).get('Idempotency-Key')).toBe(receipt.request_id)
  const form = init?.body as FormData
  expect(JSON.parse(String(form.get('intent')))).toEqual(intent)
  expect((form.get('file') as File).name).toBe('source.pdf')
  expect(form.has('metadata')).toBe(false)
})

it('GETs exactly the encoded original request and validates the fixed 20 MiB policy', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(response(receipt)).mockResolvedValueOnce(response({ schema_version: 'document-upload-policy.v1', max_file_size_bytes: 20_971_520 }))
  expect(await api.researchUploadReceipt(receipt.request_id)).toEqual(receipt)
  expect(fetch.mock.calls[0][0]).toBe('/v1/research-library/uploads/document-upload%3Astable')
  expect(new Headers(fetch.mock.calls[0][1]?.headers).get('X-Owner-Id')).toBe('owner')
  expect(fetch.mock.calls[0][1]?.method).toBeUndefined()
  expect((await api.researchUploadPolicy()).max_file_size_bytes).toBe(20_971_520)
})

it.each([[404, 'document_upload_receipt_not_found', true], [503, 'document_upload_receipt_not_found', false], [404, 'other_not_found', false], [409, 'product_request_reused', false]] as const)('only accepts the explicit 404 receipt miss (%s/%s)', async (status, detail, missing) => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({ detail }, status))
  if (missing) expect(await api.researchUploadReceipt(receipt.request_id)).toBeNull()
  else await expect(api.researchUploadReceipt(receipt.request_id)).rejects.toThrow(detail)
})

it('does not convert a network failure or malformed receipt into a permission to POST', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce(response({ request_id: receipt.request_id }))
  await expect(api.researchUploadReceipt(receipt.request_id)).rejects.toThrow('网络连接中断')
  await expect(api.researchUploadReceipt(receipt.request_id)).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(2)
})
