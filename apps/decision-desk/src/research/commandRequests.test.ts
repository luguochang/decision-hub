// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { acknowledgeCommand, checkedCommandReceipt, checkedCommandResult, createPendingCommand, readPendingCommand } from './commandRequests'
import { listPendingRequests, pendingRequest, pendingRequestWithId } from './pendingRequests'
import { commandReceipt, commandResult } from './commandTestFixtures'

afterEach(() => { sessionStorage.clear(); vi.restoreAllMocks() })

describe('frozen research command intent and complete receipt', () => {
  it('freezes one key, owner, source and full reason, and refuses a second intent for the Run', () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原理由，包括资料授权限制。')
    expect(saved.record.id).toBe(saved.intent.command.request_id)
    expect(readPendingCommand('run-a', 'owner')).toEqual(saved)
    expect(() => createPendingCommand('run-a', 'owner', 'retry', '新理由')).toThrow('尚未确认')
    expect(listPendingRequests('research-command')).toHaveLength(1)
    expect(createPendingCommand('run-b', 'owner', 'recheck', '另一任务')).toBeDefined()
  })
  it.each(['owner', 'source', 'key', 'reason', 'action', 'result-key', 'target', 'status'] as const)('rejects complete receipt %s mismatch without deleting original intent', field => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '保留原理由')
    const receipt = commandReceipt('run-a', saved.intent.command)
    if (field === 'owner') receipt.owner_id = 'someone-else'
    if (field === 'source') receipt.source_run_id = 'run-b'
    if (field === 'key') receipt.command.request_id = 'other-key'
    if (field === 'reason') receipt.command.reason = '被改写的理由'
    if (field === 'action') receipt.command.command = 'cancel'
    if (field === 'result-key') receipt.result.request_id = 'other-key'
    if (field === 'target') receipt.result.target_run_id = 'run-a'
    if (field === 'status') receipt.result.status = 'already_applied'
    expect(() => checkedCommandReceipt(receipt, saved)).toThrow('不一致')
    expect(readPendingCommand('run-a', 'owner')?.record.id).toBe(saved.record.id)
  })
  it('requires POST identities and accepted child lineage before accepting the later full receipt', () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '重试')
    const receipt = commandReceipt('run-a', saved.intent.command)
    expect(checkedCommandReceipt(receipt, saved, { ...receipt.result, status: 'already_applied' })).toEqual(receipt)
    for (const target of [null, '', 'run-a']) expect(() => checkedCommandResult({ ...receipt.result, target_run_id: target }, saved)).toThrow('不一致')
    expect(() => checkedCommandReceipt(receipt, saved, { ...receipt.result, created_at: '2026-10-01T05:00:00Z' })).toThrow('已保存回执不一致')
    expect(() => checkedCommandReceipt(receipt, saved, { ...receipt.result, status: 'rejected', target_run_id: null })).toThrow('已保存回执不一致')
  })
  it('preserves rejection rather than normalizing it into an applied success', () => {
    const saved = createPendingCommand('run-a', 'owner', 'cancel', '停止')
    const receipt = commandReceipt('run-a', saved.intent.command, 'owner', 'rejected')
    expect(checkedCommandReceipt(receipt, saved, commandResult('run-a', saved.intent.command, 'rejected')).result.status).toBe('rejected')
    expect(() => checkedCommandReceipt(receipt, saved, { ...receipt.result, status: 'already_applied' })).toThrow('不一致')
  })
  it('recognizes legacy intent for GET only and retains its original key', () => {
    const record = pendingRequest('research-command', { runId: 'run-a', command: 'retry', reason: '旧原理由' })
    const pending = readPendingCommand('run-a', 'owner')!
    expect(pending.legacy).toBe(true)
    expect(pending.intent.command.request_id).toBe(record.id)
    expect(checkedCommandReceipt(commandReceipt('run-a', pending.intent.command), pending).command.reason).toBe('旧原理由')
  })
  it('fails closed for wrong owner, duplicate Run intents and tampered storage', () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原理由')
    expect(() => readPendingCommand('run-a', 'other-owner')).toThrow('用户身份不一致')
    const otherKey = 'research-command:other'
    pendingRequestWithId('research-command', { ...saved.intent, command: { ...saved.intent.command, request_id: otherKey } }, otherKey)
    expect(() => readPendingCommand('run-a', 'owner')).toThrow('多份')
    const storageKey = sessionStorage.key(0)!
    const rows = JSON.parse(sessionStorage.getItem(storageKey)!)
    rows[0].payload.command.reason = '被更改'
    sessionStorage.setItem(storageKey, JSON.stringify(rows))
    expect(() => readPendingCommand('run-a', 'owner')).toThrow('无法读取')
  })
  it('keeps a verified outcome conservatively when acknowledgement storage cleanup fails', () => {
    const saved = createPendingCommand('run-a', 'owner', 'cancel', '停止')
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied') })
    expect(acknowledgeCommand(saved)).toBe(false)
    expect(readPendingCommand('run-a', 'owner')?.record.id).toBe(saved.record.id)
  })
})
