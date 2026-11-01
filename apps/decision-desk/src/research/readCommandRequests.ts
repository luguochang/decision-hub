import { productOwner, type RevisionReadRequest } from '../api/delivery'

const scope = [import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin', productOwner]
const key = `decision-hub.read-command.v1:${JSON.stringify(scope)}`
const cleanupKey = `${key}:cleanup`
const listeners = new Set<() => void>()
let activeOperation: symbol | null = null
type Payload = Omit<RevisionReadRequest, 'id'>
type StoredRead = { id: string; signature: string; payload: Payload; createdAt: string }
export type ReadCommandState = { pending: RevisionReadRequest | null; error: string | null; busy: boolean }

export class ReadCommandJournalError extends Error {}
function changed() { for (const listener of listeners) listener() }
function exactKeys(value: object, keys: string[]) {
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
}
function validIdentifier(value: unknown): value is string {
  return typeof value === 'string' && Boolean(value.trim()) && value.length <= 128
}
function storedRaw(): string | null {
  const primary = sessionStorage.getItem(key)
  const cleanup = sessionStorage.getItem(cleanupKey)
  if (primary !== null && cleanup !== null && primary !== cleanup) throw new Error('ambiguous_read_journal')
  return primary ?? cleanup
}
function read(): StoredRead | null {
  const raw = storedRaw()
  if (raw === null) return null
  if (!raw || raw.length > 16_384) throw new Error('invalid_read_journal')
  const value: unknown = JSON.parse(raw)
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || !exactKeys(value, ['id', 'signature', 'payload', 'createdAt'])) throw new Error('invalid_read_journal')
  const record = value as Partial<StoredRead>
  const payload = record.payload
  if (!validIdentifier(record.id) || !payload || typeof payload !== 'object' || Array.isArray(payload)
    || !exactKeys(payload, ['revision', 'read', 'owner']) || !validIdentifier(payload.revision)
    || typeof payload.read !== 'boolean' || payload.owner !== productOwner
    || record.signature !== JSON.stringify(payload)
    || typeof record.createdAt !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(record.createdAt)
    || !Number.isFinite(Date.parse(record.createdAt))
    || new Date(record.createdAt).toISOString() !== record.createdAt) throw new Error('invalid_read_intent')
  return record as StoredRead
}
function intent(record: StoredRead): RevisionReadRequest { return { ...record.payload, id: record.id } }
function sameIntent(left: RevisionReadRequest, right: RevisionReadRequest) {
  return left.id === right.id && left.owner === right.owner && left.revision === right.revision && left.read === right.read
}
export function readCommandState(): ReadCommandState {
  try {
    const stored = read()
    return { pending: stored ? intent(stored) : null, error: null, busy: activeOperation !== null }
  } catch {
    return { pending: null, error: '无法核对浏览器中的阅读凭据。请保留原记录并重新读取，尚未发送新请求。', busy: activeOperation !== null }
  }
}
export function subscribeReadCommands(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
export function beginReadCommand(): () => void {
  if (activeOperation !== null) throw new Error('另一笔阅读标记正在核对，请等待原操作完成。')
  const token = Symbol('read-command')
  activeOperation = token
  changed()
  return () => {
    if (activeOperation === token) { activeOperation = null; changed() }
  }
}
export function persistReadCommand(value: RevisionReadRequest): void {
  try {
    if (!validIdentifier(value.id) || !validIdentifier(value.revision)
      || value.owner !== productOwner || typeof value.read !== 'boolean') throw new Error('invalid_read_intent')
    const existing = read()
    if (existing) {
      if (!sameIntent(intent(existing), value)) throw new Error('unconfirmed_read_conflict')
      return
    }
    const payload: Payload = { revision: value.revision, read: value.read, owner: value.owner }
    const record: StoredRead = { id: value.id, signature: JSON.stringify(payload), payload, createdAt: new Date().toISOString() }
    sessionStorage.setItem(key, JSON.stringify(record))
    const saved = read()
    if (!saved || !sameIntent(intent(saved), value)) throw new Error('read_persistence_unconfirmed')
  } catch {
    throw new ReadCommandJournalError('无法保存本次阅读凭据，尚未发送请求。请先重新读取浏览器凭据。')
  } finally { changed() }
}
export function assertPendingReadCommand(value: RevisionReadRequest): void {
  try {
    const stored = read()
    if (!stored || !sameIntent(intent(stored), value)) throw new Error('read_intent_changed')
  } catch {
    throw new ReadCommandJournalError('原阅读凭据已变化或无法核对，尚未发送新请求。请保留原记录。')
  }
}
export function acknowledgeReadCommand(value: RevisionReadRequest): void {
  assertPendingReadCommand(value)
  const original = storedRaw()
  if (original === null) throw new ReadCommandJournalError('原阅读凭据无法核对，尚未清除请求。')
  try {
    // Keep a durable recovery copy before deleting the main record. A later
    // read failure cannot leave a reload without the original frozen tuple.
    sessionStorage.setItem(cleanupKey, original)
    if (sessionStorage.getItem(cleanupKey) !== original) throw new Error('read_cleanup_backup_unconfirmed')
    sessionStorage.removeItem(key)
    if (sessionStorage.getItem(key) !== null) throw new Error('read_cleanup_unconfirmed')
    // Native removeItem returning is the last mutation. Do not introduce a
    // fallible read after deleting the last recovery copy. A no-op removal
    // conservatively leaves a recoverable command for the next read.
    sessionStorage.removeItem(cleanupKey)
  } catch {
    // Restore only readable absence; never overwrite a different journal.
    try {
      if (sessionStorage.getItem(key) === null && sessionStorage.getItem(cleanupKey) === null) sessionStorage.setItem(cleanupKey, original)
    } catch { /* Inaccessible storage stays blocked and unconfirmed. */ }
    throw new ReadCommandJournalError('原回执已核对，但浏览器凭据清理尚未确认。请再次核对原请求。')
  } finally { changed() }
}
