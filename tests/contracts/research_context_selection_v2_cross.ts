import cases from '../fixtures/research_context_selection_v2_cases.json'
import { researchContextSelectionReceiptV2Schema } from '../../packages/contracts_ts/src/generated/r2'
import type {
  ResearchContextSelectionReceiptV2, ResearchContextSelectorIdentityV2,
} from '../../packages/contracts_ts/src/index'
import { contextSelectionTimeIssueCodes } from '../../packages/contracts_ts/src/contextSelectionSemantics'

type IsAny<T> = 0 extends (1 & T) ? true : false
const topAny: IsAny<ResearchContextSelectionReceiptV2> = false
const requestAny: IsAny<ResearchContextSelectionReceiptV2['projected_request']> = false
const inputAny: IsAny<ResearchContextSelectionReceiptV2['considered_inputs'][number]['input']> = false
const selectorAny: IsAny<ResearchContextSelectorIdentityV2> = false
const ranking: ResearchContextSelectorIdentityV2['ranking_rules_ref'] = 'research-context.relevance-authority-recency.v2'
const hashKind: ResearchContextSelectionReceiptV2['selection_payload_hash_kind'] = 'canonical_context_selection.v2'
void [topAny, requestAny, inputAny, selectorAny, ranking, hashKind]

for (const sample of cases) {
  const raw = JSON.parse(JSON.stringify(sample.value)) as Record<string, unknown>
  if ('expand_considered_count' in sample) {
    const first = (raw.considered_inputs as Array<Record<string, unknown>>)[0]
    raw.considered_inputs = Array.from({ length: sample.expand_considered_count as number }, (_, index) => ({
      ...first, input: { ...first.input as Record<string, unknown>, evidence_id: `declared-count-${index}` },
    }))
  }
  const before = JSON.stringify(raw)
  const accepted = researchContextSelectionReceiptV2Schema.safeParse(raw).success
  const expected = sample.expected_shape && sample.expected_time_semantics
  if (accepted !== expected) throw new Error(`${sample.name}: accepted=${accepted}, expected=${expected}`)
  if (sample.expected_shape) {
    // Only this pure formula receives V1, never the canonical body or hash namespace.
    const codes = contextSelectionTimeIssueCodes({ ...raw, schema_version: 'research-context-selection.v1' })
    if (JSON.stringify(codes) !== JSON.stringify(sample.expected_issue_codes)) throw new Error(`${sample.name}: mismatched time codes ${codes}`)
  }
  if (JSON.stringify(raw) !== before) throw new Error(`${sample.name}: raw declaration mutated`)
}
const positive = cases.find(sample => sample.name === 'live-clock-before-pit')!
const arbitraryHash = JSON.parse(JSON.stringify(positive.value)) as Record<string, unknown>
arbitraryHash.selection_payload_hash = 'f'.repeat(64)
if (!researchContextSelectionReceiptV2Schema.safeParse(arbitraryHash).success) throw new Error('shape cannot claim self-hash authority')
console.log(JSON.stringify({ cases: cases.length, matched: true, scope: 'V2 strict shape + same pure integer time declarations; no selector/clock/SQL/hash provenance or execution proof' }))
