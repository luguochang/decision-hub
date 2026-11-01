import { createHash } from 'node:crypto'
import type { ResearchBaselineDelivery } from './baseline.js'
import { HubClientError } from './hub-client.js'
import { providerCanonicalJson } from './provider-input.js'

const invalid = () => new HubClientError('research_baseline_context_invalid', false)
type Body = Record<string, unknown>
const bodyHash = (wire: string) => createHash('sha256').update(wire, 'utf8').digest('hex')

function bodies(value: unknown, key: 'fact_id' | 'evidence_id'): Map<string, Body> {
  if (!Array.isArray(value)) throw invalid()
  const result = new Map<string, Body>()
  for (const item of value) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)
      || typeof item[key] !== 'string' || item[key].length === 0 || result.has(item[key])) throw invalid()
    // Validate complete lossless values before indexing. Even two identical
    // objects with one ID are ambiguous material, not a deduplication license.
    providerCanonicalJson(item)
    result.set(item[key], item)
  }
  return result
}

/** Pure model projection only. The caller must independently authenticate the
 * complete delivery. References never replace original server authority. */
export function projectBaselineDelivery(delivery: ResearchBaselineDelivery, facts: unknown, evidence: unknown) {
  const factBodies = bodies(facts, 'fact_id'), evidenceBodies = bodies(evidence, 'evidence_id')
  const reference = (body: Body, key: 'fact_id' | 'evidence_id', originals: Map<string, Body>) => {
    const id = body[key]
    if (typeof id !== 'string') throw invalid()
    const original = originals.get(id), wire = providerCanonicalJson(body)
    if (original === undefined || providerCanonicalJson(original) !== wire) throw invalid()
    return { [key]: id, body_hash: createHash('sha256').update(wire, 'utf8').digest('hex') }
  }
  const { schema_version, results, ...metadata } = delivery
  if (schema_version !== 'research-baseline-delivery.v1') throw invalid()
  return {
    ...metadata,
    schema_version: 'research-baseline-delivery-projection.v1' as const,
    source_schema_version: schema_version,
    results: results.map(result => {
      const { facts: resultFacts, evidence_candidates: resultEvidence, ...fields } = result
      return { ...fields,
        fact_refs: resultFacts === null ? null : resultFacts.map(fact => reference(fact, 'fact_id', factBodies)),
        evidence_refs: resultEvidence.map(item => reference(item, 'evidence_id', evidenceBodies)),
      }
    }),
  }
}

/** Store only repeated, sufficiently large complete attributes once. Nothing
 * outside attributes is pooled, and no full object is deferred to another read. */
export function projectFactAttributes(facts: unknown) {
  const originals = [...bodies(facts, 'fact_id').values()]
  const candidates = new Map<string, { body: unknown; wire: string; count: number }>()
  for (const fact of originals) {
    if (Object.hasOwn(fact, 'attributes_ref')) throw invalid()
    if (!Object.hasOwn(fact, 'attributes')) continue
    const wire = providerCanonicalJson(fact.attributes)
    if (Buffer.byteLength(wire, 'utf8') < 1024) continue
    const hash = bodyHash(wire), previous = candidates.get(hash)
    if (previous !== undefined && previous.wire !== wire) throw invalid()
    candidates.set(hash, { body: fact.attributes, wire, count: (previous?.count ?? 0) + 1 })
  }
  const shared = [...candidates.entries()].filter(([, item]) => item.count >= 2)
    .sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
    .map(([hash, item]) => ({ body_hash: hash, body: item.body }))
  const included = new Set(shared.map(item => item.body_hash))
  const projected = originals.map(fact => {
    if (!Object.hasOwn(fact, 'attributes')) return fact
    const hash = bodyHash(providerCanonicalJson(fact.attributes))
    if (!included.has(hash)) return fact
    const { attributes: _attributes, ...fields } = fact
    return { ...fields, attributes_ref: { body_hash: hash } }
  })
  return { facts: projected, shared_fact_attributes: shared }
}

/** Decode model-visible material without granting authority. The caller still
 * compares complete facts and baseline against the independent server originals. */
export function restoreFactAttributes(facts: unknown, pool: unknown): Body[] {
  const projected = [...bodies(facts, 'fact_id').values()]
  if (!Array.isArray(pool)) throw invalid()
  const shared = new Map<string, unknown>()
  for (const item of pool) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)
      || Object.keys(item).sort().join(',') !== 'body,body_hash'
      || typeof item.body_hash !== 'string' || !/^[a-f0-9]{64}$/.test(item.body_hash)
      || shared.has(item.body_hash) || bodyHash(providerCanonicalJson(item.body)) !== item.body_hash) throw invalid()
    shared.set(item.body_hash, item.body)
  }
  const originals = projected.map(fact => {
    if (!Object.hasOwn(fact, 'attributes_ref')) return fact
    const reference = fact.attributes_ref
    if (Object.hasOwn(fact, 'attributes') || reference === null || typeof reference !== 'object'
      || Array.isArray(reference) || Object.keys(reference).join(',') !== 'body_hash'
      || !('body_hash' in reference) || typeof reference.body_hash !== 'string'
      || !shared.has(reference.body_hash)) throw invalid()
    const { attributes_ref: _reference, ...fields } = fact
    return { ...fields, attributes: shared.get(reference.body_hash) }
  })
  // Enforce threshold, count, exact sorting, no unused entries, and the unique
  // deterministic representation. Do not accept an alternate client pool layout.
  const expected = projectFactAttributes(originals)
  if (providerCanonicalJson(expected.facts) !== providerCanonicalJson(facts)
    || providerCanonicalJson(expected.shared_fact_attributes) !== providerCanonicalJson(pool)) throw invalid()
  return originals
}
