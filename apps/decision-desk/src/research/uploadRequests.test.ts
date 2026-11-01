// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { pendingRequest } from './pendingRequests'
import { checkedCurrentUploadVersion, checkedUploadReceipt, createUploadIntent, readPendingUpload, requireOriginalUploadFile, uploadPolicy, validateUploadFile } from './uploadRequests'
import { uploadMetadata, uploadReceipt } from './uploadTestFixtures'

beforeEach(() => { sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })
const file = () => new File(['PC original content'], 'source.pdf')
async function pending() {
  const intent = await createUploadIntent(file(), uploadMetadata)
  return { record: pendingRequest('document-upload', intent), intent }
}

it('accepts exactly 20 MiB and refuses empty and 20 MiB plus one before reading bytes', () => {
  const fake = (size: number) => ({ size, arrayBuffer: vi.fn() } as unknown as File)
  expect(() => validateUploadFile(fake(uploadPolicy.max_file_size_bytes))).not.toThrow()
  expect(() => validateUploadFile(fake(0))).toThrow('所选文件没有内容')
  const large = fake(uploadPolicy.max_file_size_bytes + 1)
  expect(() => validateUploadFile(large)).toThrow('20,971,520 字节')
  expect(large.arrayBuffer).not.toHaveBeenCalled()
})

it('uses real SHA-256, stores only intent, and requires exact name size and bytes on retry', async () => {
  const saved = await pending()
  expect(saved.intent.file_sha256).toBe('cf7cd7376f77ef52fb5bf7c80b496433db76aa83eda61107c2617c8783f7ad32')
  expect(readPendingUpload()).toEqual(saved)
  expect(sessionStorage.getItem(sessionStorage.key(0)!)).not.toContain('PC original content')
  await expect(requireOriginalUploadFile(file(), saved.intent)).resolves.toBeUndefined()
  await expect(requireOriginalUploadFile(new File(['PC original contenX'], 'source.pdf'), saved.intent)).rejects.toThrow('所选文件与原上传不一致')
  await expect(requireOriginalUploadFile(new File(['PC original content'], 'other.pdf'), saved.intent)).rejects.toThrow('所选文件与原上传不一致')
  await expect(requireOriginalUploadFile(new File(['PC'], 'source.pdf'), saved.intent)).rejects.toThrow('所选文件与原上传不一致')
})

it.each(['key', 'owner', 'name', 'size', 'hash', 'metadata', 'original', 'published', 'supersedes'] as const)('rejects a valid but unrelated %s receipt', async field => {
  const saved = await pending(), result = structuredClone(uploadReceipt(saved.intent, saved.record.id))
  if (field === 'key') result.request_id = 'document-upload:other'
  if (field === 'owner') result.intent.owner_id = 'other'
  if (field === 'name') result.intent.file_name = 'other.pdf'
  if (field === 'size') result.intent.file_size_bytes++
  if (field === 'hash') result.intent.file_sha256 = 'b'.repeat(64)
  if (field === 'metadata') result.intent.metadata.notes = 'not the requested notes'
  if (field === 'original') result.version.original_file_hash = 'b'.repeat(64)
  if (field === 'published') result.version.published_at = '2026-10-01T01:00:00Z'
  if (field === 'supersedes') result.version.supersedes_version_id = 'docver_other'
  expect(() => checkedUploadReceipt(result, saved)).toThrow('上传回执与原请求不一致')
})

it('compares object keys by meaning while preserving array order and exact metadata strings', async () => {
  const saved = await pending(), original = uploadReceipt(saved.intent, saved.record.id)
  const reordered = { ...original, intent: Object.fromEntries(Object.entries(original.intent).reverse()) }
  expect(checkedUploadReceipt(reordered, saved)).toEqual(original)
  original.intent = { ...original.intent, metadata: { ...original.intent.metadata, title: `${saved.intent.metadata.title} ` } }
  expect(() => checkedUploadReceipt(original, saved)).toThrow('不一致')
})

it('reads the current state rather than the frozen received snapshot and checks current identity and hash', async () => {
  const saved = await pending(), receipt = uploadReceipt(saved.intent, saved.record.id)
  expect(checkedCurrentUploadVersion({ ...receipt.version, status: 'parsed', parse_receipt_ref: 'parse://real' }, receipt).status).toBe('parsed')
  for (const changed of [{ version_id: 'docver_other' }, { document_id: 'doc_other' }, { original_file_hash: 'b'.repeat(64) }]) {
    expect(() => checkedCurrentUploadVersion({ ...receipt.version, ...changed }, receipt)).toThrow('当前资料版本与上传回执不一致')
  }
})

it('refuses corrupt, foreign-owner and ambiguous journals before any dispatch', async () => {
  const saved = await pending()
  const storageKey = sessionStorage.key(0)!
  sessionStorage.setItem(storageKey, JSON.stringify([{ ...saved.record, payload: { ...saved.intent, owner_id: 'other' } }]))
  expect(() => readPendingUpload()).toThrow('无法读取尚未确认')
  sessionStorage.clear()
  pendingRequest('document-upload', { ...saved.intent, owner_id: 'other' })
  expect(() => readPendingUpload()).toThrow('当前用户')
  sessionStorage.clear()
  pendingRequest('document-upload', saved.intent)
  pendingRequest('document-upload', { ...saved.intent, file_name: 'other.pdf' })
  expect(() => readPendingUpload()).toThrow('多份尚未确认')
})
