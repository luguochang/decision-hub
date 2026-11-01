import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'

afterEach(() => vi.restoreAllMocks())
const receipt = {
  schema_version: 'research-run-command-receipt.v1', request_id: 'key/one',
  owner_id: 'owner/one', source_run_id: 'run/one',
  command: { schema_version: 'research-run-command.v1', request_id: 'key/one', command: 'cancel', reason: '保留原理由' },
  result: { schema_version: 'research-run-command-result.v1', request_id: 'key/one', command: 'cancel', source_run_id: 'run/one', target_run_id: null, status: 'rejected', created_at: '2026-10-01T02:00:00Z' },
}

describe('research command receipt transport', () => {
  it('GETs the encoded original identity with the original owner and no body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(receipt)))
    expect(await api.researchCommandReceipt('run/one', 'key/one', 'owner/one')).toEqual(receipt)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/v1/research/runs/run%2Fone/commands/key%2Fone')
    expect(init?.method ?? 'GET').toBe('GET')
    expect(init?.body).toBeUndefined()
    expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner/one')
  })

  it('allows retry only on the exact receipt-not-found response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'research_command_receipt_not_found' }), { status: 404 }))
    expect(await api.researchCommandReceipt('run', 'key', 'owner')).toBeNull()
  })

  it.each([
    [404, 'research_run_not_found'], [403, 'owner_required'],
    [409, 'research_command_request_reused'], [503, 'service_unavailable'],
    [500, 'research_command_receipt_not_found'],
  ])('preserves %s %s as failure instead of permission to POST', async (status, detail) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail }), { status }))
    await expect(api.researchCommandReceipt('run', 'key', 'owner')).rejects.toThrow(detail)
  })

  it('rejects malformed successful content', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...receipt, command: { ...receipt.command, reason: '' } })))
    await expect(api.researchCommandReceipt('run', 'key', 'owner')).rejects.toThrow()
  })

  it('retains the legacy POST shape and the same key/owner headers', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(receipt.result)))
    expect(await api.researchCommand('run/one', receipt.command as Parameters<typeof api.researchCommand>[1], 'owner/one')).toEqual(receipt.result)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/v1/research/runs/run%2Fone/commands')
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('key/one')
    expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('owner/one')
  })
})
