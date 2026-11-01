// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { acknowledgeRequest, findPendingRequest, listPendingRequests, pendingRequest, pendingRequestWithId, pendingResearchText } from './pendingRequests'

afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })

it('persists the full intent with the same network key across reload and rejects key reuse', async () => {
  const payload = { run_id: 'run-a', owner_id: 'owner', command: { request_id: 'research-command:original', command: 'cancel', reason: '原理由' } }
  const record = pendingRequestWithId('research-command', payload, payload.command.request_id)
  expect(record.id).toBe(payload.command.request_id)
  vi.resetModules()
  const reloaded = await import('./pendingRequests')
  expect(reloaded.listPendingRequests('research-command')).toEqual([record])
  expect(reloaded.pendingRequestWithId('research-command', payload, record.id)).toEqual(record)
  expect(() => reloaded.pendingRequestWithId('research-command', { ...payload, owner_id: 'other' }, record.id)).toThrow('尚未发送请求')
  expect(reloaded.listPendingRequests('research-command')).toEqual([record])
})

it('refuses explicit-key dispatch if the original journal cannot be read or written', () => {
  const payload = { command: { request_id: 'network-key' } }
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('QuotaExceededError') })
  expect(() => pendingRequestWithId('research-command', payload, 'network-key')).toThrow('尚未发送请求')
  expect(listPendingRequests('research-command')).toEqual([])
})

it('survives a module reload without dispatching and separates changed intents', async () => {
  const original = pendingRequest('research-submit', { text: 'BTC' })
  vi.resetModules()
  const reloaded = await import('./pendingRequests')
  expect(reloaded.pendingResearchText()).toBe('BTC')
  expect(reloaded.pendingRequest('research-submit', { text: 'BTC' }).id).toBe(original.id)
  const changed = reloaded.pendingRequest('research-submit', { text: 'rates' })
  expect(changed.id).not.toBe(original.id)
  expect(reloaded.pendingRequest('research-command', { text: 'BTC' }).id).not.toBe(original.id)
  reloaded.acknowledgeRequest('research-submit', changed.id)
  expect(reloaded.pendingResearchText()).toBe('BTC')
  reloaded.acknowledgeRequest('research-submit', original.id)
  expect(reloaded.pendingResearchText()).toBeUndefined()
  expect(reloaded.pendingRequest('research-submit', { text: 'BTC' }).id).not.toBe(original.id)
})

it('keeps a key beyond the old fifteen-minute boundary until a receipt arrives', () => {
  const original = pendingRequest('research-submit', { text: '' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 24 * 60 * 60 * 1000)
  expect(pendingRequest('research-submit', { text: '' }).id).toBe(original.id)
})

it('fails before dispatch when session storage cannot persist the command', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('QuotaExceededError') })
  expect(() => pendingRequest('research-submit', { text: 'BTC' })).toThrow('尚未发送请求')
  expect(findPendingRequest('research-submit', { text: 'BTC' })).toBeUndefined()
})

it('keeps an acknowledged identity conservatively when cleanup is unavailable', () => {
  const original = pendingRequest('research-submit', { text: 'BTC' })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('SecurityError') })
  acknowledgeRequest('research-submit', original.id)
  expect(pendingRequest('research-submit', { text: 'BTC' }).id).toBe(original.id)
})

it.each(['{bad json', '{}', '[null]'])('does not replace an unreadable pending journal: %s', (corrupted) => {
  pendingRequest('research-submit', { text: 'BTC' })
  const key = sessionStorage.key(0)!
  sessionStorage.setItem(key, corrupted)
  expect(pendingResearchText()).toBeUndefined()
  expect(() => pendingRequest('research-submit', { text: 'BTC' })).toThrow('尚未发送请求')
  expect(sessionStorage.getItem(key)).toBe(corrupted)
  expect(() => listPendingRequests('research-submit')).toThrow('无法读取尚未确认')
})

it('retains separately targeted feedback and decisions for explicit recovery after remount', async () => {
  const first = pendingRequest('report-feedback', { revision: 'revision-1', owner: 'owner', verdict: 'useful', notes: '可核对' })
  const second = pendingRequest('report-feedback', { revision: 'revision-2', owner: 'owner', verdict: 'incorrect', notes: '另一份报告' })
  const decision = pendingRequest('evolution-decision', { domain_pack_ref: 'crypto_macro', candidate_id: 'candidate-1', owner: 'owner', decision: 'reject', reason: '保留原方法', evaluation_refs: [], expected_generation: 3 })
  vi.resetModules()
  const recovered = await import('./pendingRequests')
  expect(recovered.listPendingRequests('report-feedback').map(row => row.id)).toEqual([first.id, second.id])
  expect(recovered.listPendingRequests('evolution-decision')[0]).toEqual(decision)
  recovered.acknowledgeRequest('report-feedback', first.id)
  expect(recovered.listPendingRequests('report-feedback').map(row => row.id)).toEqual([second.id])
  expect(recovered.listPendingRequests('evolution-decision')[0]).toEqual(decision)
})

it('does not hide unresolved submissions when storage access fails', () => {
  pendingRequest('report-feedback', { revision: 'revision-1', owner: 'owner', verdict: 'useful', notes: '可核对' })
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('SecurityError') })
  expect(() => listPendingRequests('report-feedback')).toThrow('尚未发送新请求')
})

it('does not recover a changed payload under an existing journal signature', () => {
  pendingRequest('report-feedback', { revision: 'original', owner: 'owner', verdict: 'useful', notes: '原内容' })
  const key = sessionStorage.key(0)!
  const rows = JSON.parse(sessionStorage.getItem(key)!)
  rows[0].payload.revision = 'changed-target'
  const corrupted = JSON.stringify(rows)
  sessionStorage.setItem(key, corrupted)
  expect(() => listPendingRequests('report-feedback')).toThrow('无法读取尚未确认')
  expect(sessionStorage.getItem(key)).toBe(corrupted)
})


it('restores upload identity without storing original bytes and distinguishes changed metadata', async () => {
  const intent = { schema_version: 'document-upload-intent.v1', owner_id: 'owner', file_name: 'private.txt', file_sha256: 'a'.repeat(64), file_size_bytes: 12, metadata: { title: '原资料', notes: '原用途' } }
  const original = pendingRequest('document-upload', intent)
  const key = sessionStorage.key(0)!
  expect(sessionStorage.getItem(key)).not.toContain('originalBytes')
  vi.resetModules()
  const recovered = await import('./pendingRequests')
  expect(recovered.listPendingRequests('document-upload')).toEqual([original])
  expect(recovered.pendingRequest('document-upload', intent).id).toBe(original.id)
  const changed = recovered.pendingRequest('document-upload', { ...intent, metadata: { ...intent.metadata, notes: '不同用途' } })
  expect(changed.id).not.toBe(original.id)
  recovered.acknowledgeRequest('document-upload', original.id)
  expect(recovered.listPendingRequests('document-upload').map(row => row.id)).toEqual([changed.id])
})
