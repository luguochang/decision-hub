// @vitest-environment jsdom
import { readCommandReceiptSchema, readReceiptViewSchema } from '@decision-hub/contracts-ts'
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { request } from './client'
import { delivery, productOwner, type RevisionReadRequest } from './delivery'

vi.mock('./client', () => ({ request: vi.fn() }))
beforeEach(() => vi.stubGlobal('crypto', webcrypto))
afterEach(() => { vi.resetAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

const stamp = '2026-10-03T04:00:00Z'
const original = {
  revision_id: 'revision-read-1', owner_id: productOwner,
  read_at: stamp, updated_at: stamp,
}

// These are all valid under the CURRENT four-field DTO. They must fail because
// they disagree with the actual submitted intent, not because of a future DTO.
it.each([
  { name: 'different revision', read: true, changed: { revision_id: 'revision-other' } },
  { name: 'different owner', read: true, changed: { owner_id: 'owner-other' } },
  { name: 'unread response to read command', read: true, changed: { read_at: null } },
  { name: 'read response to unread command', read: false, changed: {} },
])('rejects a formatted-valid receipt for $name', async ({ name, read, changed }) => {
  const response = { ...original, ...changed }
  expect(readReceiptViewSchema.safeParse(response).success).toBe(true)
  vi.mocked(request).mockResolvedValue(response)
  console.info('read_receipt_observation', JSON.stringify({
    scenario: name, submitted: { revision: original.revision_id, owner: productOwner, read, id: 'read-original-key' },
    response,
  }))
  await expect(delivery.read(original.revision_id, read, 'read-original-key')).rejects.toThrow()
})

it('sends the original request key and boolean without claiming the weak DTO proves that key', async () => {
  vi.mocked(request).mockResolvedValue(original)
  await delivery.read(original.revision_id, true, 'read-original-key')
  const [path, init] = vi.mocked(request).mock.calls[0]
  expect(path).toBe('/v1/product/reports/revision-read-1/read')
  expect(init).toMatchObject({
    method: 'POST', headers: { 'X-Owner-Id': productOwner, 'Idempotency-Key': 'read-original-key' },
  })
  expect(JSON.parse(init!.body as string)).toEqual({ request_id: 'read-original-key', read: true })
  expect(Object.hasOwn(original, 'request_id')).toBe(false)
})

const submitted: RevisionReadRequest = { revision: original.revision_id, owner: productOwner, read: true, id: 'read-original-key' }
function exactReceipt(value: RevisionReadRequest = submitted) {
  const payload = { read: value.read, request_id: value.id }
  return {
    schema_version: 'read-command-receipt.v1', request_id: value.id, revision_id: value.revision,
    owner_id: value.owner, command: { request_id: value.id, read: value.read },
    result: { ...original, revision_id: value.revision, owner_id: value.owner, read_at: value.read ? stamp : null },
    payload_hash: createHash('sha256').update(JSON.stringify([value.owner, value.revision, 'read_receipt', payload])).digest('hex'),
    confirmation_basis: 'stored_command_hash',
  }
}

it.each(['request', 'command', 'hash'] as const)('rejects a formatted-valid full ACK with wrong %s binding', async field => {
  const response = exactReceipt()
  if (field === 'request') response.request_id = 'other-read-key'
  if (field === 'command') response.command.request_id = 'other-read-key'
  if (field === 'hash') response.payload_hash = 'f'.repeat(64)
  expect(readCommandReceiptSchema.safeParse(response).success).toBe(true)
  vi.mocked(request).mockResolvedValue(response)
  await expect(delivery.readCommandReceipt(submitted)).rejects.toThrow()
})

it('queries the exact original intent and accepts only its stored-command hash', async () => {
  vi.mocked(request).mockResolvedValue(exactReceipt())
  await expect(delivery.readCommandReceipt(submitted)).resolves.toEqual(exactReceipt())
  expect(vi.mocked(request).mock.calls).toEqual([[
    '/v1/product/reports/revision-read-1/read/commands/read-original-key?read=true',
    { headers: { 'X-Owner-Id': productOwner } }, 200,
  ]])
})

it('keeps HTTP errors distinct from a successful absent command and never POSTs during a lookup', async () => {
  vi.mocked(request).mockRejectedValueOnce(new Error('HTTP 404'))
  await expect(delivery.readCommandReceipt(submitted)).rejects.toThrow('HTTP 404')
  vi.mocked(request).mockResolvedValueOnce(null)
  await expect(delivery.readCommandReceipt(submitted)).resolves.toBeNull()
  expect(vi.mocked(request).mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
})

it.each([
  { status: 201, body: null, accepted: false },
  { status: 200, body: null, accepted: true },
  { status: 404, body: { detail: 'read command route missing' }, accepted: false },
  { status: 200, body: exactReceipt(), accepted: true },
])('uses the real fetch boundary for status $status and accepted=$accepted', async ({ status, body, accepted }) => {
  const actual = await vi.importActual<typeof import('./client')>('./client')
  vi.mocked(request).mockImplementation(actual.request)
  const transport = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }))
  const result = delivery.readCommandReceipt(submitted)
  if (accepted) await expect(result).resolves.toEqual(body)
  else await expect(result).rejects.toThrow()
  expect(transport).toHaveBeenCalledTimes(1)
  expect(transport.mock.calls[0][1]).not.toHaveProperty('method', 'POST')
})
