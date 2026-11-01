// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { listPendingRequests } from './pendingRequests'
import { freezeSubmission, receivedSubmissions, rememberSubmission, submissionConflicts, volatileSubmissions } from './submissionReceipts'

const queued = { schema_version: 'research-run-queued.v1' as const, event_id: 'event', run_id: 'run-received', status: 'queued' as const, status_url: '/v1/runs/run-received' }
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear(); receivedSubmissions('research-submit'); submissionConflicts('research-submit') })
function key(fragment: string) { return Object.keys(sessionStorage).find(item => item.includes(fragment))! }

it('captures an independent complete intent and stores the receipt before deleting its pending identity', () => {
  const payload = { text: '原关注点' }, saved = freezeSubmission('research-submit', payload)
  payload.text = '新关注点'
  const originalRemove = Storage.prototype.removeItem
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (this: Storage, name: string) {
    expect(receivedSubmissions('research-submit').find(item => item.record.request.id === saved.id)?.persisted).toBe(true)
    originalRemove.call(this, name)
  })
  const outcome = rememberSubmission('research-submit', saved, '#/compose?focus=original', queued)
  expect(outcome.persisted).toBe(true); expect(outcome.cleanupPending).toBe(false)
  expect(outcome.record.request.payload).toEqual({ text: '原关注点' })
  expect(listPendingRequests('research-submit')).toEqual([])
})

it('keeps the original pending key and a same-tab receipt when confirmation storage cannot write', () => {
  const saved = freezeSubmission('research-submit', { text: '保存失败的原关注点' })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  const outcome = rememberSubmission('research-submit', saved, '#/compose', queued)
  expect(outcome.persisted).toBe(false); expect(outcome.cleanupPending).toBe(true)
  expect(listPendingRequests('research-submit')[0]).toEqual(saved)
  expect(receivedSubmissions('research-submit')[0].record.receipt.run_id).toBe(queued.run_id)
})

it('does not acknowledge when a confirmation write cannot be read back', () => {
  const saved = freezeSubmission('research-submit', { text: '读回失败的原关注点' })
  const get = Storage.prototype.getItem
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, name: string) {
    if (name.includes('received-submissions')) throw new Error('read_blocked')
    return get.call(this, name)
  })
  const outcome = rememberSubmission('research-submit', saved, '#/compose', queued)
  expect(outcome.persisted).toBe(false)
  expect(listPendingRequests('research-submit')).toEqual([saved])
  expect(volatileSubmissions('research-submit')[0].record.request.id).toBe(saved.id)
})

it('keeps confirmation while accurately detecting a swallowed pending cleanup failure', () => {
  const saved = freezeSubmission('research-submit', { text: '清理失败的原关注点' })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('remove_denied') })
  const outcome = rememberSubmission('research-submit', saved, '#/compose', queued)
  expect(outcome.persisted).toBe(true); expect(outcome.cleanupPending).toBe(true)
  expect(listPendingRequests('research-submit')).toEqual([saved])
  expect(receivedSubmissions('research-submit')[0].record.receipt).toEqual(queued)
})

it('does not overwrite or delete a pending identity that changed during the network request', () => {
  const saved = freezeSubmission('research-submit', { text: '等待中的原关注点' })
  const pendingKey = key('pending.v1'), altered = { ...saved, payload: { text: '异变内容' }, signature: JSON.stringify({ text: '异变内容' }) }
  sessionStorage.setItem(pendingKey, JSON.stringify([altered]))
  expect(() => rememberSubmission('research-submit', saved, '#/compose', queued)).toThrow('已有请求绑定不一致')
  expect(listPendingRequests('research-submit')).toEqual([altered])
  expect(submissionConflicts('research-submit')[0].record.request).toEqual(saved)
  expect(receivedSubmissions('research-submit')).toEqual([])
  expect(sessionStorage.getItem(key('received-submissions'))).toBeNull()
})

it('refuses an existing same-key receipt bound to a different returned Run', () => {
  const saved = freezeSubmission('research-submit', { text: '同键原请求' })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('keep_pending') })
  rememberSubmission('research-submit', saved, '#/compose', queued)
  const receiptKey = key('received-submissions'), original = sessionStorage.getItem(receiptKey)
  const changed = { ...queued, run_id: 'different-run', status_url: '/v1/runs/different-run' }
  expect(() => rememberSubmission('research-submit', saved, '#/compose', changed)).toThrow('已有请求绑定不一致')
  expect(sessionStorage.getItem(receiptKey)).toBe(original)
  expect(listPendingRequests('research-submit')).toEqual([saved])
})

it('refuses corrupt receipt journals instead of overwriting prior audit records', () => {
  const first = freezeSubmission('research-submit', { text: '第一笔' })
  rememberSubmission('research-submit', first, '#/compose', queued)
  const receiptKey = key('received-submissions')
  sessionStorage.setItem(receiptKey, '{corrupt')
  const saved = freezeSubmission('research-submit', { text: '第二笔' })
  expect(() => rememberSubmission('research-submit', saved, '#/compose', queued)).toThrow('已有记录无法一致核对')
  expect(sessionStorage.getItem(receiptKey)).toBe('{corrupt')
  expect(listPendingRequests('research-submit')).toEqual([saved])
})

it('does not shadow a durable old receipt with a different Run after reloading without a pending key', async () => {
  const saved = freezeSubmission('research-submit', { text: '重载前已确认' })
  rememberSubmission('research-submit', saved, '#/compose', queued)
  const receiptKey = key('received-submissions'), before = sessionStorage.getItem(receiptKey)
  vi.resetModules()
  const fresh = await import('./submissionReceipts')
  expect(() => fresh.rememberSubmission('research-submit', saved, '#/compose', { ...queued, run_id: 'other-run', status_url: '/v1/runs/other-run' })).toThrow('已有请求绑定不一致')
  expect(sessionStorage.getItem(receiptKey)).toBe(before)
  expect(fresh.volatileSubmissions('research-submit')).toEqual([])
  expect(fresh.submissionConflicts('research-submit')[0].previous).toMatchObject({ receipt: queued })
})

it.each([
  ['research-submit', 'run_id'], ['research-submit', 'event_id'],
  ['observation-submit', 'run_id'], ['observation-submit', 'event_id'],
] as const)('refuses blank %s receipt %s even when its status path is self-consistent', (kind, field) => {
  const saved = freezeSubmission(kind, { text: '空身份核对' })
  const receipt = { ...queued, [field]: ' \t ', status_url: field === 'run_id' ? '/v1/runs/ \t ' : queued.status_url }
  expect(() => rememberSubmission(kind, saved, '#/compose', receipt)).toThrow('invalid_submission_identity')
  expect(listPendingRequests(kind)).toEqual([saved])
  expect(receivedSubmissions(kind)).toEqual([])
  expect(volatileSubmissions(kind)).toEqual([])
})

it('reads the identical durable receipt after reload and acknowledgment without rewriting its original time or origin', async () => {
  const saved = freezeSubmission('research-submit', { text: '原回执已确认' })
  const first = rememberSubmission('research-submit', saved, '#/compose?back=original', queued)
  const receiptKey = key('received-submissions'), before = sessionStorage.getItem(receiptKey)
  expect(listPendingRequests('research-submit')).toEqual([])
  vi.resetModules()
  const fresh = await import('./submissionReceipts')
  const outcome = fresh.rememberSubmission('research-submit', saved, '#/compose?back=different', queued)
  expect(outcome).toEqual(first)
  expect(sessionStorage.getItem(receiptKey)).toBe(before)
  expect(fresh.receivedSubmissions('research-submit')[0]).toEqual(first)
})
