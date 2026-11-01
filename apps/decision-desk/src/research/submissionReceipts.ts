import { z } from 'zod'
import { researchRequestSchema, researchRunQueuedSchema } from '../api/schemas'
import type { api, ObservationReceipt } from '../api/client'
import { acknowledgeRequest, listPendingRequests, pendingRequestWithId, type PendingRequest } from './pendingRequests'

export type SubmissionKind = 'research-submit' | 'observation-submit' | 'library-research'
export type SubmissionReceipt = ObservationReceipt | Awaited<ReturnType<typeof api.submitResearch>>
export type ReceivedSubmission = {
  schema_version: 'received-submission.v1'
  kind: SubmissionKind
  request: PendingRequest
  origin_hash: string
  receipt: SubmissionReceipt
  received_at: string
}
export type SubmissionOutcome = { record: ReceivedSubmission; persisted: boolean; cleanupPending: boolean }
export const submissionJournalEvent = 'decision-hub:submission-journal'
export const submissionRefusalCodes = new Set(['research_submission_legacy_binding_unavailable', 'research_submission_intent_conflict', 'research_submission_receipt_corrupt'])
const scope = JSON.stringify([import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin', import.meta.env.VITE_OWNER_ID?.trim() || 'owner'])
const storageKey = (kind: SubmissionKind) => `decision-hub.received-submissions.v1:${scope}:${kind}`
// A verified response remains reachable in this tab if browser storage fails.
// It is not a guarantee after reload or closing the tab.
const volatileReceipts = new Map<string, ReceivedSubmission>()
export type SubmissionConflict = { record: ReceivedSubmission; explanation: string; previous?: unknown }
const conflicts = new Map<string, SubmissionConflict>()
class SubmissionJournalConflict extends Error {
  constructor(message: string, readonly previous?: unknown) { super(message) }
}
const bindingMessage = '接收回执与本标签页已有请求绑定不一致，原凭据与已保存回执未清理。请展开原始记录核对。'
const textPayload = z.object({ text: z.string() }).strict()
const legacyReceipt = z.object({ event_id: z.string().min(1), run_id: z.string().min(1), status_url: z.string().min(1) }).passthrough()

function checkedPayload(kind: SubmissionKind, payload: unknown): void {
  if (kind === 'library-research') researchRequestSchema.parse(payload)
  else textPayload.parse(payload)
}
function checkedRecord(value: unknown, kind: SubmissionKind): ReceivedSubmission {
  if (!value || typeof value !== 'object') throw new Error('invalid_received_submission')
  const record = value as ReceivedSubmission
  if (record.schema_version !== 'received-submission.v1' || record.kind !== kind || !record.request ||
    typeof record.request.id !== 'string' || !record.request.id || typeof record.request.createdAt !== 'string' ||
    JSON.stringify(record.request.payload) !== record.request.signature || typeof record.origin_hash !== 'string' ||
    typeof record.received_at !== 'string') throw new Error('invalid_received_submission')
  checkedPayload(kind, record.request.payload)
  const receipt = kind === 'observation-submit' ? legacyReceipt.parse(record.receipt) : researchRunQueuedSchema.parse(record.receipt)
  if (!receipt.run_id.trim() || !receipt.event_id.trim()) throw new Error('invalid_submission_identity')
  if (receipt.status_url !== `/v1/runs/${receipt.run_id}`) throw new Error('invalid_submission_status_url')
  return record
}
export function pendingSubmissions(kind: SubmissionKind): PendingRequest[] {
  const records = listPendingRequests(kind)
  for (const record of records) checkedPayload(kind, record.payload)
  return records
}
export function restoredSubmissionText(kind: 'research-submit' | 'observation-submit'): string | undefined {
  try { return (pendingSubmissions(kind).at(-1)?.payload as { text: string } | undefined)?.text } catch { return undefined }
}
export function freezeSubmission(kind: SubmissionKind, payload: unknown): PendingRequest {
  checkedPayload(kind, payload)
  const signature = JSON.stringify(payload)
  const existing = pendingSubmissions(kind).find(record => record.signature === signature)
  if (existing) return existing
  // Capture an independent copy: future form edits cannot alter the submitted intent.
  return pendingRequestWithId(kind, JSON.parse(signature), `${kind}:${crypto.randomUUID()}`)
}
function storedSubmissions(kind: SubmissionKind): ReceivedSubmission[] {
  let stored: ReceivedSubmission[] = []
  const value = sessionStorage.getItem(storageKey(kind))
  if (value) {
    try {
      const rows: unknown = JSON.parse(value)
      if (!Array.isArray(rows)) throw new Error('invalid_received_submissions')
      stored = rows.map(record => checkedRecord(record, kind))
      if (new Set(stored.map(record => record.request.id)).size !== stored.length) throw new Error('duplicate_received_submission')
    } catch { throw new SubmissionJournalConflict('接收回执与本标签页已有记录无法一致核对，原记录未覆盖或清理。请展开原始记录核对。', value) }
  }
  return stored
}
export function receivedSubmissions(kind: SubmissionKind): SubmissionOutcome[] {
  const stored = storedSubmissions(kind)
  const pending = pendingSubmissions(kind)
  const remaining = new Set(pending.map(record => record.id))
  for (const [id, record] of volatileReceipts) {
    if (record.kind !== kind) continue
    // Clearing session storage must not resurrect receipts from an earlier session.
    if (!remaining.has(id) && !stored.some(saved => saved.request.id === id)) volatileReceipts.delete(id)
  }
  const all = new Map(stored.map(record => [record.request.id, record]))
  for (const [id, record] of volatileReceipts) {
    if (record.kind !== kind) continue
    const durable = all.get(id)
    if (durable && (JSON.stringify(durable.request) !== JSON.stringify(record.request) || JSON.stringify(durable.receipt) !== JSON.stringify(record.receipt))) {
      conflicts.set(id, { record, explanation: bindingMessage, previous: durable })
      continue
    }
    all.set(id, record)
  }
  return [...all.values()].flatMap(record => {
    const original = pending.find(saved => saved.id === record.request.id)
    if (original && JSON.stringify(original) !== JSON.stringify(record.request)) {
      conflicts.set(record.request.id, { record, explanation: bindingMessage, previous: original })
      return []
    }
    if (conflicts.has(record.request.id)) return []
    return [{ record, persisted: stored.some(saved => JSON.stringify(saved) === JSON.stringify(record)), cleanupPending: remaining.has(record.request.id) }]
  })
}
export function volatileSubmissions(kind: SubmissionKind): SubmissionOutcome[] {
  return [...volatileReceipts.values()].filter(record => record.kind === kind && !conflicts.has(record.request.id)).map(record => ({ record, persisted: false, cleanupPending: true }))
}
export function submissionConflicts(kind: SubmissionKind): SubmissionConflict[] {
  try {
    const pendingIds = new Set(pendingSubmissions(kind).map(record => record.id))
    const storedIds = new Set(storedSubmissions(kind).map(record => record.request.id))
    for (const [id, conflict] of conflicts) if (conflict.record.kind === kind && !pendingIds.has(id) && !storedIds.has(id)) conflicts.delete(id)
  } catch { /* Keep conflict audit evidence when local records cannot be read. */ }
  return [...conflicts.values()].filter(conflict => conflict.record.kind === kind)
}
export function rememberSubmission(kind: SubmissionKind, request: PendingRequest, originHash: string, receipt: SubmissionReceipt): SubmissionOutcome {
  const record = checkedRecord({ schema_version: 'received-submission.v1', kind, request, origin_hash: originHash, receipt, received_at: new Date().toISOString() }, kind)
  const cached = volatileReceipts.get(request.id)
  if (cached && (JSON.stringify(cached.request) !== JSON.stringify(request) || JSON.stringify(cached.receipt) !== JSON.stringify(receipt))) {
    conflicts.set(request.id, { record, explanation: bindingMessage, previous: cached })
    throw new SubmissionJournalConflict(bindingMessage, cached)
  }
  let persisted = false
  try {
    // Verify an existing durable binding before any volatile replacement,
    // including after a reload when the old pending key was already cleared.
    const stored = storedSubmissions(kind)
    const existing = stored.find(saved => saved.request.id === request.id)
    if (existing && (JSON.stringify(existing.request) !== JSON.stringify(request) || JSON.stringify(existing.receipt) !== JSON.stringify(receipt))) {
      throw new SubmissionJournalConflict(bindingMessage, existing)
    }
    const original = pendingSubmissions(kind).find(saved => saved.id === request.id)
    if (existing && !original) {
      // A matching durable receipt survives acknowledgment and reload. Keep
      // the first receipt's timestamp and origin instead of treating it as drift.
      volatileReceipts.set(request.id, existing)
      return { record: existing, persisted: true, cleanupPending: false }
    }
    if (!original || JSON.stringify(original) !== JSON.stringify(request)) throw new SubmissionJournalConflict(bindingMessage, original)
    if (existing) {
      volatileReceipts.set(request.id, existing)
      acknowledgeRequest(kind, request.id)
      let cleanupPending = true
      try { cleanupPending = pendingSubmissions(kind).some(saved => saved.id === request.id) } catch { /* Keep cleanup uncertainty. */ }
      return { record: existing, persisted: true, cleanupPending }
    }
    volatileReceipts.set(request.id, record)
    const previous = stored.filter(saved => saved.request.id !== request.id)
    sessionStorage.setItem(storageKey(kind), JSON.stringify([...previous, record]))
    const saved: unknown = JSON.parse(sessionStorage.getItem(storageKey(kind)) ?? 'null')
    if (!Array.isArray(saved) || !saved.some(item => JSON.stringify(item) === JSON.stringify(record))) throw new Error('received_submission_not_saved')
    persisted = true
  } catch (error) {
    if (error instanceof SubmissionJournalConflict) {
      conflicts.set(request.id, { record, explanation: error.message, previous: error.previous })
      throw error
    }
    // An unreadable store is unknown, not evidence of a matching local binding.
    // Preserve only the response associated with the independently frozen dispatch.
    volatileReceipts.set(request.id, record)
  }
  let cleanupPending = true
  if (persisted) {
    try {
      const original = pendingSubmissions(kind).find(saved => saved.id === request.id)
      if (!original || JSON.stringify(original) !== JSON.stringify(request)) throw new Error('submission_identity_changed')
      acknowledgeRequest(kind, request.id)
      cleanupPending = pendingSubmissions(kind).some(saved => saved.id === request.id)
    } catch { cleanupPending = true }
  }
  return { record, persisted, cleanupPending }
}
