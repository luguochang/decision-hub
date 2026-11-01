import { foldSurface } from '@deepseek-ai/dsh-session/surface'
import { isDeepStrictEqual } from 'node:util'

/** Native SDK metadata only: model text containing these fields is never provenance. */
const SNAPSHOT_PREFIX = 'Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\n\n'
const SECTION_NAMES = new Set(['sandbox:policy', 'approval:policy', 'subagent:delegation'])
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
}
function exactText(content: unknown, expected: string): boolean {
  if (!Array.isArray(content) || content.length !== 1) return false
  const block = record(content[0])
  return block !== null && exactKeys(block, ['type', 'text']) && block.type === 'text' && block.text === expected
}
function officialSnapshot(data: Record<string, unknown>): boolean {
  if (!exactKeys(data, ['content', 'source', 'role', 'id']) || data.role !== 'user'
    || typeof data.id !== 'string' || !data.id.trim()) return false
  const source = record(data.source)
  if (!source || !exactKeys(source, ['kind', 'plugin', 'form', 'sections']) || source.kind !== 'plugin'
    || source.plugin !== '@deepseek-ai/dsh-system-prompt' || source.form !== 'snapshot'
    || !Array.isArray(source.sections) || source.sections.length === 0) return false
  const names = new Set<string>(), texts: string[] = []
  for (const value of source.sections) {
    const section = record(value)
    if (!section || !exactKeys(section, ['name', 'text']) || typeof section.name !== 'string'
      || !SECTION_NAMES.has(section.name) || names.has(section.name) || typeof section.text !== 'string' || section.text.length === 0) return false
    names.add(section.name)
    texts.push(section.text)
  }
  const body = texts.join('\n\n')
  return body.length > 0 && exactText(data.content, SNAPSHOT_PREFIX + body)
}

/** Only a complete native baseline is eligible; file digests are metadata, not signatures. */
function officialInstructions(data: Record<string, unknown>): boolean {
  if (!exactKeys(data, ['content', 'source', 'role', 'id']) || data.role !== 'user'
    || typeof data.id !== 'string' || !data.id.trim()) return false
  const source = record(data.source)
  if (!source || !exactKeys(source, ['kind', 'form', 'baseline', 'baselineIdentity', 'changes'])
    || source.kind !== 'agent-instructions' || source.form !== 'instructions' || source.baseline !== true
    || typeof source.baselineIdentity !== 'string' || !source.baselineIdentity.trim()
    || !Array.isArray(source.changes)) return false
  for (const value of source.changes) {
    const change = record(value)
    if (!change || !exactKeys(change, ['action', 'scope', 'path', 'digest']) || change.action !== 'set'
      || typeof change.scope !== 'string' || !change.scope.trim() || typeof change.path !== 'string' || !change.path.trim()
      || typeof change.digest !== 'string' || !/^[a-f0-9]{40}$/.test(change.digest)) return false
  }
  const block = Array.isArray(data.content) ? record(data.content[0]) : null
  return block !== null && typeof block.text === 'string' && block.text.length > 0 && exactText(data.content, block.text)
}
export function parentBaseline(events: readonly unknown[], reviewerCallSeq: number | undefined): Record<string, unknown> | null {
  if (!Number.isSafeInteger(reviewerCallSeq) || reviewerCallSeq! < 0) return null
  const candidates: { seq: number; data: Record<string, unknown> }[] = []
  for (const event of events) {
    const row = record(event), data = record(row?.data), message = record(data?.message)
    if (row?.type !== 'user/message' || (record(data?.source)?.kind !== 'agent-instructions'
      && record(message?.source)?.kind !== 'agent-instructions')) continue
    if (!Number.isSafeInteger(row.seq)) return null
    if ((row.seq as number) >= reviewerCallSeq!) continue
    if (!data || !officialInstructions(data)) return null
    candidates.push({ seq: row.seq as number, data })
  }
  if (candidates.length === 0) return null
  const first = candidates[0]!.data
  if (candidates.some(candidate => !isDeepStrictEqual(candidate.data.source, first.source)
    || !isDeepStrictEqual(candidate.data.content, first.content))) return null
  // Preserve the established single-baseline path; only repeated histories need a surface proof.
  if (candidates.length === 1) return first
  const prefix = events.filter(event => (record(event)?.seq as number) < reviewerCallSeq!)
  const cutoff = record(events[reviewerCallSeq!])
  if (prefix.length !== reviewerCallSeq || cutoff?.seq !== reviewerCallSeq || cutoff.type !== 'tool/call') return null
  try {
    const surface = foldSurface(prefix as Parameters<typeof foldSurface>[0])
    const active = candidates.filter(candidate => surface.nodes.includes(candidate.seq))
    if (active.length !== 1 || candidates.some(candidate => candidate !== active[0]
      && !surface.replacements.some(replacement => replacement.shadowedSeqs.includes(candidate.seq)))) return null
    return active[0]!.data
  } catch { return null }
}

/** Unique exact product prompt with optional authenticated SDK frames.
 * Parent events are the independently host-inspected originals, cut at the original
 * reviewer call (also on finalization). Retain/hash all child frames unchanged.
 */
export function hasExactReviewerPrompt(events: readonly unknown[], expectedPrompt: string,
  parentEvents: readonly unknown[] = [], reviewerCallSeq?: number): boolean {
  let productIndex = -1, snapshotIndex = -1, instructionIndex = -1
  const assistantIndex = events.findIndex(event => record(event)?.type === 'assistant/message')
  for (const [index, event] of events.entries()) {
    const row = record(event)
    if (row?.type !== 'user/message') continue
    const data = record(row.data)
    if (!data) return false
    const source = record(data.source)
    if (source?.kind === 'agent-instructions') {
      const expected = parentBaseline(parentEvents, reviewerCallSeq)
      if (instructionIndex !== -1 || !officialInstructions(data) || !expected
        || !isDeepStrictEqual(data.source, expected.source) || !isDeepStrictEqual(data.content, expected.content)) return false
      instructionIndex = index
      continue
    }
    if (source?.kind === 'plugin') {
      if (snapshotIndex !== -1 || !officialSnapshot(data)) return false
      snapshotIndex = index
      continue
    }
    const message = record(data.message) ?? data
    if (message.source !== undefined && data.source !== undefined && !isDeepStrictEqual(message.source, data.source)) return false
    const productSource = message.source ?? data.source
    if (productIndex !== -1 || (productSource !== undefined && record(productSource)?.kind !== 'user')) return false
    if (!exactText(message.content, expectedPrompt)) return false
    productIndex = index
  }
  return productIndex !== -1 && (snapshotIndex === -1
    || (snapshotIndex > productIndex && assistantIndex > snapshotIndex))
    && (instructionIndex === -1 || (instructionIndex > productIndex && assistantIndex > instructionIndex
      && (snapshotIndex === -1 || instructionIndex < snapshotIndex)))
}
