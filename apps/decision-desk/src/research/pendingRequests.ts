// Keep unconfirmed commands in this browser tab until the API acknowledges them.
// No command is replayed automatically; local-only material still needs UI consent.
const scope = [import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin', import.meta.env.VITE_OWNER_ID?.trim() || 'owner']
const prefix = `decision-hub.pending.v1:${JSON.stringify(scope)}:`
export type PendingKind = 'research-submit' | 'research-command' | 'library-research' | 'observation-submit' | 'document-recovery' | 'claim-review' | 'report-feedback' | 'evolution-decision' | 'document-upload'
export type PendingRequest = { id: string; signature: string; payload: unknown; createdAt: string }

function read(kind: PendingKind): PendingRequest[] {
  const value = sessionStorage.getItem(prefix + kind)
  if (!value) return []
  const rows: unknown = JSON.parse(value)
  if (!Array.isArray(rows) || rows.some(row => !row || typeof row.id !== 'string' || typeof row.signature !== 'string' || typeof row.createdAt !== 'string')) throw new Error('invalid_pending_requests')
  return rows as PendingRequest[]
}

export function listPendingRequests(kind: PendingKind): PendingRequest[] {
  try {
    const records = read(kind)
    if (records.some(record => JSON.stringify(record.payload) !== record.signature)) throw new Error('invalid_pending_payload')
    return records
  } catch {
    throw new Error('无法读取尚未确认的提交凭据。请保留当前页面并检查浏览器会话存储，尚未发送新请求。')
  }
}

export function pendingRequest(kind: PendingKind, payload: unknown): PendingRequest {
  try {
    const rows = read(kind)
    const signature = JSON.stringify(payload)
    const existing = rows.find(row => row.signature === signature)
    if (existing) return existing
    const record = { id: `${kind}:${crypto.randomUUID()}`, signature, payload, createdAt: new Date().toISOString() }
    sessionStorage.setItem(prefix + kind, JSON.stringify([...rows, record]))
    return record
  } catch {
    throw new Error('无法保存本次提交凭据，尚未发送请求。请检查浏览器会话存储后再试。')
  }
}

export function findPendingRequest(kind: PendingKind, payload: unknown): PendingRequest | undefined {
  try { return read(kind).find(row => row.signature === JSON.stringify(payload)) } catch { return undefined }
}

// Some receipts contain their request identity inside the frozen intent. Persist
// that same identity before dispatch, rather than assigning a second local key.
export function pendingRequestWithId(kind: PendingKind, payload: unknown, id: string): PendingRequest {
  try {
    if (!id.trim()) throw new Error('invalid_pending_identity')
    const rows = listPendingRequests(kind)
    const signature = JSON.stringify(payload)
    const existing = rows.find(row => row.id === id)
    if (existing) {
      if (existing.signature !== signature) throw new Error('pending_identity_reused')
      return existing
    }
    const record = { id, signature, payload, createdAt: new Date().toISOString() }
    sessionStorage.setItem(prefix + kind, JSON.stringify([...rows, record]))
    return record
  } catch {
    throw new Error('无法保存本次提交凭据，尚未发送请求。请检查浏览器会话存储后再试。')
  }
}

function pendingText(kind: PendingKind): string | undefined {
  try {
    const payload = read(kind).at(-1)?.payload
    return payload && typeof payload === 'object' && 'text' in payload && typeof payload.text === 'string' ? payload.text : undefined
  } catch { return undefined }
}

export function pendingResearchText(): string | undefined { return pendingText('research-submit') }
export function pendingObservationText(): string | undefined { return pendingText('observation-submit') }

export function acknowledgeRequest(kind: PendingKind, id: string): void {
  try {
    const remaining = read(kind).filter(row => row.id !== id)
    if (remaining.length) sessionStorage.setItem(prefix + kind, JSON.stringify(remaining))
    else sessionStorage.removeItem(prefix + kind)
  } catch {
    // Keeping an acknowledged key is conservative: a retry returns its existing receipt.
  }
}
