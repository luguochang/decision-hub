import { researchRunCommandReceiptSchema, researchRunCommandResultSchema, researchRunCommandSchema } from '../api/schemas'
import type { ResearchRunCommand, ResearchRunCommandReceipt, ResearchRunCommandResult } from '../api/types'
import { acknowledgeRequest, listPendingRequests, pendingRequestWithId, type PendingRequest } from './pendingRequests'

export type CommandIntent = { schema_version: 'research-command-intent.v1'; run_id: string; owner_id: string; command: ResearchRunCommand }
export type PendingCommand = { record: PendingRequest; intent: CommandIntent; legacy: boolean }
export const commandJournalEvent = 'decision-hub:research-command-journal'

// This is a GET locator, never a persisted receipt or permission to POST.
const locatorScope = [import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin', import.meta.env.VITE_OWNER_ID?.trim() || 'owner']
function locatorKey(runId: string, owner: string): string {
  return `decision-hub.command-locator.v1:${JSON.stringify([locatorScope, runId, owner])}`
}

function checkedLocatorRecord(value: unknown, runId: string, owner: string): PendingCommand {
  if (!value || typeof value !== 'object' || !fields(value, ['id', 'signature', 'payload', 'createdAt']) ||
    !('id' in value) || typeof value.id !== 'string' || !value.id || !('signature' in value) || typeof value.signature !== 'string' ||
    !('createdAt' in value) || typeof value.createdAt !== 'string' || !Number.isFinite(Date.parse(value.createdAt)) ||
    !('payload' in value) || !value.payload || typeof value.payload !== 'object' || JSON.stringify(value.payload) !== value.signature) {
    throw new Error('任务操作原请求指针不完整，请保留原请求键；尚未确认回执，不能发送新操作。')
  }
  const payload = value.payload
  if (!fields(payload, ['schema_version', 'run_id', 'owner_id', 'command']) ||
    !('schema_version' in payload) || payload.schema_version !== 'research-command-intent.v1' ||
    !('run_id' in payload) || payload.run_id !== runId || !('owner_id' in payload) || payload.owner_id !== owner || !('command' in payload)) {
    throw new Error('任务操作原请求指针与当前任务或用户身份不一致，请保留原请求键；尚未确认回执。')
  }
  const parsed = researchRunCommandSchema.safeParse(payload.command)
  if (!parsed.success || parsed.data.request_id !== value.id) throw new Error('任务操作原请求指针与原请求键不一致，尚未确认回执。')
  const intent: CommandIntent = { schema_version: 'research-command-intent.v1', run_id: runId, owner_id: owner, command: parsed.data }
  return { legacy: false, intent, record: { id: value.id, signature: value.signature, payload, createdAt: value.createdAt } }
}

export function readCommandLocator(runId: string, owner: string): PendingCommand | null {
  try {
    const raw = sessionStorage.getItem(locatorKey(runId, owner))
    return raw === null ? null : checkedLocatorRecord(JSON.parse(raw), runId, owner)
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('任务操作')) throw error
    throw new Error('任务操作原请求指针无法读取，请保留原请求键；尚未确认回执，不能发送新操作。')
  }
}

export function rememberCommandLocator(saved: PendingCommand, latestOwnDiscovery = false): void {
  if (saved.legacy) return
  try {
    checkedLocatorRecord(saved.record, saved.intent.run_id, saved.intent.owner_id)
    const existing = readCommandLocator(saved.intent.run_id, saved.intent.owner_id)
    const pending = readPendingCommand(saved.intent.run_id, saved.intent.owner_id)
    // An older explicit GET must never replace a newer operation's pointer.
    if (pending && pending.record.id !== saved.record.id) return
    if (!latestOwnDiscovery && existing && existing.record.id !== saved.record.id && pending?.record.id !== saved.record.id) return
    sessionStorage.setItem(locatorKey(saved.intent.run_id, saved.intent.owner_id), JSON.stringify(saved.record))
    const readBack = readCommandLocator(saved.intent.run_id, saved.intent.owner_id)
    if (readBack?.record.id !== saved.record.id || readBack.record.signature !== saved.record.signature) throw new Error('locator_write_not_retained')
  } catch {
    throw new Error('任务操作原请求指针无法保存，原待确认请求键已保留；请刷新后重新核对，不发送新操作。')
  }
}

function mismatch(message: string, returned: unknown, pending: PendingCommand, posted?: ResearchRunCommandResult): Error {
  return Object.assign(new Error(message), { originalRecord: { returned, expected_intent: pending.intent, ...(posted ? { posted } : {}) } })
}

export function commandErrorRecord(error: Error): string {
  const originalRecord = (error as Error & { originalRecord?: unknown }).originalRecord
  return originalRecord === undefined ? error.message : JSON.stringify({ message: error.message, original_record: originalRecord }, null, 2)
}

function fields(value: object, expected: string[]): boolean {
  return Object.keys(value).sort().join('|') === [...expected].sort().join('|')
}

export function readPendingCommand(runId: string, owner: string): PendingCommand | null {
  const matches: PendingCommand[] = []
  for (const record of listPendingRequests('research-command')) {
    const payload = record.payload
    if (!payload || typeof payload !== 'object') throw new Error('任务操作凭据不完整，尚未发送新操作。请保留原始记录。')
    if ('runId' in payload && 'command' in payload && 'reason' in payload && fields(payload, ['runId', 'command', 'reason'])) {
      if (typeof payload.runId !== 'string' || !payload.runId) throw new Error('旧任务操作凭据不完整，尚未发送新操作。')
      const parsed = researchRunCommandSchema.safeParse({ schema_version: 'research-run-command.v1', request_id: record.id, command: payload.command, reason: payload.reason })
      if (!parsed.success) throw new Error('旧任务操作凭据不完整，尚未发送新操作。')
      if (payload.runId === runId) matches.push({ record, legacy: true, intent: { schema_version: 'research-command-intent.v1', run_id: payload.runId, owner_id: owner, command: parsed.data } })
      continue
    }
    if (!fields(payload, ['schema_version', 'run_id', 'owner_id', 'command']) || !('schema_version' in payload) || payload.schema_version !== 'research-command-intent.v1' ||
      !('run_id' in payload) || typeof payload.run_id !== 'string' || !payload.run_id || !('owner_id' in payload) || typeof payload.owner_id !== 'string' || !payload.owner_id || !('command' in payload)) {
      throw new Error('任务操作凭据不完整，尚未发送新操作。请保留原始记录。')
    }
    const parsed = researchRunCommandSchema.safeParse(payload.command)
    if (!parsed.success || parsed.data.request_id !== record.id) throw new Error('任务操作凭据与原请求键不一致，尚未发送新操作。')
    if (payload.run_id === runId) {
      if (payload.owner_id !== owner) throw new Error('任务操作凭据与当前用户身份不一致，尚未发送新操作。')
      matches.push({ record, legacy: false, intent: { schema_version: 'research-command-intent.v1', run_id: payload.run_id, owner_id: payload.owner_id, command: parsed.data } })
    }
  }
  if (matches.length > 1) throw new Error('这个任务有多份尚未确认的操作凭据，请核对原始记录，暂不能发送新操作。')
  return matches[0] ?? null
}

export function createPendingCommand(runId: string, owner: string, action: ResearchRunCommand['command'], reason: string): PendingCommand {
  if (readPendingCommand(runId, owner)) throw new Error('这个任务还有尚未确认的操作，请先核对原请求。尚未发送新操作。')
  const command = researchRunCommandSchema.parse({ schema_version: 'research-run-command.v1', request_id: `research-command:${crypto.randomUUID()}`, command: action, reason })
  const intent: CommandIntent = { schema_version: 'research-command-intent.v1', run_id: runId, owner_id: owner, command }
  const record = pendingRequestWithId('research-command', intent, command.request_id)
  return { record, intent, legacy: false }
}

export function checkedCommandResult(value: unknown, pending: PendingCommand): ResearchRunCommandResult {
  const parsed = researchRunCommandResultSchema.safeParse(value)
  const command = pending.intent.command
  if (!parsed.success || parsed.data.request_id !== command.request_id || parsed.data.source_run_id !== pending.intent.run_id || parsed.data.command !== command.command) {
    throw mismatch('任务操作回执与原请求不一致，尚未确认结果。请保留原提交凭据。', value, pending)
  }
  const result = parsed.data
  const createsRun = command.command === 'retry' || command.command === 'recheck'
  if (result.status === 'rejected' ? result.target_run_id !== null : createsRun ? !result.target_run_id?.trim() || result.target_run_id === result.source_run_id : result.target_run_id !== null) {
    throw mismatch('任务操作结果与原操作不一致，尚未确认结果。请保留原提交凭据。', value, pending)
  }
  return result
}

export function checkedCommandReceipt(value: unknown, pending: PendingCommand, posted?: ResearchRunCommandResult): ResearchRunCommandReceipt {
  const parsed = researchRunCommandReceiptSchema.safeParse(value)
  const intent = pending.intent
  if (!parsed.success || parsed.data.request_id !== intent.command.request_id || parsed.data.owner_id !== intent.owner_id || parsed.data.source_run_id !== intent.run_id ||
    parsed.data.command.schema_version !== intent.command.schema_version || parsed.data.command.request_id !== intent.command.request_id ||
    parsed.data.command.command !== intent.command.command || parsed.data.command.reason !== intent.command.reason || parsed.data.result.status === 'already_applied') {
    throw mismatch('任务操作完整回执与原请求不一致，尚未确认结果。请保留原提交凭据。', value, pending, posted)
  }
  const result = checkedCommandResult(parsed.data.result, pending)
  if (posted && (result.status !== (posted.status === 'already_applied' ? 'accepted' : posted.status) || result.target_run_id !== posted.target_run_id || result.created_at !== posted.created_at)) {
    throw mismatch('任务操作返回结果与已保存回执不一致，尚未确认结果。请保留原提交凭据。', value, pending, posted)
  }
  return parsed.data
}

export function acknowledgeCommand(pending: PendingCommand): boolean {
  acknowledgeRequest('research-command', pending.record.id)
  try { return !listPendingRequests('research-command').some(record => record.id === pending.record.id) }
  catch { return false }
  finally { window.dispatchEvent(new Event(commandJournalEvent)) }
}

export const commandNames: Record<ResearchRunCommand['command'], string> = { cancel: '停止研究', retry: '重试研究', recheck: '更新公开证据', feedback: '记录反馈' }

export function commandErrorSummary(error: Error): string {
  const localPrefixes = ['任务操作', '旧任务操作', '这个任务', '无法保存本次提交凭据', '无法读取尚未确认的提交凭据', '无法保存指定请求', '请求超时，尚未确认结果', '网络连接中断，尚未确认结果']
  if (localPrefixes.some(prefix => error.message.startsWith(prefix))) return error.message
  return '尚未确认这次任务操作。请先核对原请求回执，再决定是否按原内容重试。'
}
