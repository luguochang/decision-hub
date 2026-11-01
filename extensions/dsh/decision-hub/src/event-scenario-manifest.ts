import {
  eventScenarioPreparationManifestV1Schema, eventScenarioActionPlanV1Schema,
  eventScenarioActionPlanDraftV1Schema, eventScenarioAuthoredOriginalV1Schema,
} from '@decision-hub/contracts-ts'
import { bodyHash, canonicalJson } from './perpetual-synthesis-protocol.js'

const MARKER = '$e'
const OBJECT = '$o'
const REF = '$r'
const DICT = '$d'
const HASH = '$h'
const PREFIX = 'event-scenario-manifest:'
const invalid = (reason: string) => new Error('decision_hub_event_scenario_manifest_' + reason)

/** Exact local content graph decoding only. No financial calculation or source authority. */
export function unpackEventScenarioManifest(raw: unknown) {
  const manifest = eventScenarioPreparationManifestV1Schema.parse(raw)
  const originals = new Map<string, unknown>(), used = new Set<string>(), active = new Set<string>()
  if (manifest.ref_prefixes.length > 256
    || new Set(manifest.ref_prefixes).size !== manifest.ref_prefixes.length
    || canonicalJson(manifest.ref_prefixes) !== canonicalJson([...manifest.ref_prefixes].sort())) throw invalid('prefixes')
  const shapeKeys = manifest.object_shapes.map(shape => canonicalJson(shape))
  const compareShapes = (left: string[], right: string[]) => {
    for (let index = 0; index < Math.min(left.length, right.length); index++) {
      if (left[index] !== right[index]) return left[index]! < right[index]! ? -1 : 1
    }
    return left.length - right.length
  }
  if (manifest.object_shapes.length > 1024 || new Set(shapeKeys).size !== shapeKeys.length
    || manifest.object_shapes.some(shape => new Set(shape).size !== shape.length
      || canonicalJson(shape) !== canonicalJson([...shape].sort()))
    || canonicalJson(manifest.object_shapes) !== canonicalJson([...manifest.object_shapes].sort(compareShapes))) throw invalid('shapes')
  if (manifest.hashes.length > 4096 || new Set(manifest.hashes).size !== manifest.hashes.length
    || manifest.hashes.some(hash => !/^[a-f0-9]{64}$/.test(hash))
    || canonicalJson(manifest.hashes) !== canonicalJson([...manifest.hashes].sort())) throw invalid('hashes')
  const decodeHash = (value: unknown): string => {
    if (typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)) return value
    if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value < manifest.hashes.length) return manifest.hashes[value]!
    throw invalid('hash_index')
  }
  const references = manifest.originals.map(row => row.hash)
  if (canonicalJson(references) !== canonicalJson([...references].sort())) throw invalid('order')
  for (const row of manifest.originals) {
    const body: unknown = row.body
    const digest = bodyHash(body)
    if (row.hash !== digest) throw invalid('original_identity')
    if (originals.has(PREFIX + row.hash)) throw invalid('duplicate')
    originals.set(PREFIX + row.hash, body)
  }
  const decodeRef = (reference: { ref: string; hash: string }): unknown => {
    if (!reference || canonicalJson(Object.keys(reference).sort()) !== canonicalJson(['hash', 'ref'])
      || reference.ref !== PREFIX + reference.hash || !/^[a-f0-9]{64}$/.test(reference.hash)
      || !originals.has(reference.ref)) throw invalid('external_or_missing_ref')
    if (active.has(reference.ref)) throw invalid('cycle')
    active.add(reference.ref); used.add(reference.ref)
    try { return decode(originals.get(reference.ref)) } finally { active.delete(reference.ref) }
  }
  const decode = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(decode)
    if (value === null || typeof value !== 'object') return value
    const row = value as Record<string, unknown>
    if (Object.hasOwn(row, HASH)) {
      if (Object.keys(row).length !== 1 || typeof row[HASH] !== 'number') throw invalid('hash_marker')
      return decodeHash(row[HASH])
    }
    if (Object.hasOwn(row, DICT)) {
      const dictionary = row[DICT]
      if (Object.keys(row).length !== 1 || !Array.isArray(dictionary) || dictionary.length !== 2
        || !Number.isSafeInteger(dictionary[0]) || dictionary[0] < 0 || dictionary[0] >= manifest.object_shapes.length
        || !Array.isArray(dictionary[1])) throw invalid('dict_marker')
      const keys = manifest.object_shapes[dictionary[0]]!, values = dictionary[1]
      if (keys.length !== values.length) throw invalid('dict_arity')
      return Object.fromEntries(keys.map((key, index) => [key, decode(values[index])]))
    }
    if (Object.hasOwn(row, REF)) {
      const reference = row[REF]
      if (Object.keys(row).length !== 1 || !Array.isArray(reference)) throw invalid('ref_marker')
      if (reference.length === 2 && typeof reference[0] === 'string') {
        const hash = decodeHash(reference[1])
        return { ref: reference[0] + hash, hash }
      }
      if (reference.length !== 3 || !Number.isSafeInteger(reference[0]) || reference[0] < 0
        || reference[0] >= manifest.ref_prefixes.length || typeof reference[1] !== 'string') throw invalid('ref_marker')
      return { ref: manifest.ref_prefixes[reference[0]]! + reference[1], hash: decodeHash(reference[2]) }
    }
    if (Object.hasOwn(row, OBJECT)) {
      const overlay = row[OBJECT]
      if (Object.keys(row).length !== 1 || !Array.isArray(overlay) || overlay.length !== 2
        || typeof overlay[0] !== 'string' || !/^[a-f0-9]{64}$/.test(overlay[0])
        || overlay[1] === null || typeof overlay[1] !== 'object' || Array.isArray(overlay[1])) throw invalid('object_marker')
      const base = decodeRef({ ref: PREFIX + overlay[0], hash: overlay[0] })
      const overrides = decode(overlay[1])
      if (base === null || typeof base !== 'object' || Array.isArray(base)
        || overrides === null || typeof overrides !== 'object' || Array.isArray(overrides)
        || Object.keys(overrides).some(key => Object.hasOwn(base, key))) throw invalid('object_collision')
      return { ...base, ...overrides }
    }
    if (Object.hasOwn(row, MARKER)) {
      const digest = row[MARKER]
      if (Object.keys(row).length !== 1 || typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) throw invalid('marker')
      return decodeRef({ ref: PREFIX + digest, hash: digest })
    }
    return Object.fromEntries(Object.entries(row).map(([key, child]) => [key, decode(child)]))
  }
  const expanded = decodeRef(manifest.root_ref)
  if (used.size !== originals.size) throw invalid('orphan')
  if (bodyHash(expanded) !== manifest.expanded_hash) throw invalid('expanded_identity')
  // All terminal bodies retain exact V1 typed meaning; factoring changes no economics.
  if (expanded === null || typeof expanded !== 'object' || Array.isArray(expanded)
    || canonicalJson(Object.keys(expanded).sort()) !== canonicalJson(['declared_originals', 'event_plan', 'normalized_event_plan_draft'])) throw invalid('material_shape')
  const material = expanded as Record<string, unknown>
  eventScenarioActionPlanV1Schema.parse(material.event_plan)
  eventScenarioActionPlanDraftV1Schema.parse(material.normalized_event_plan_draft)
  if (!Array.isArray(material.declared_originals)) throw invalid('material_originals')
  material.declared_originals.forEach(row => eventScenarioAuthoredOriginalV1Schema.parse(row))
  return expanded as {
    event_plan: ReturnType<typeof eventScenarioActionPlanV1Schema.parse>
    normalized_event_plan_draft: ReturnType<typeof eventScenarioActionPlanDraftV1Schema.parse>
    declared_originals: ReturnType<typeof eventScenarioAuthoredOriginalV1Schema.parse>[]
  }
}
