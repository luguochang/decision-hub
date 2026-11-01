import cases from '../fixtures/research_context_selection_v3_cases.json'
import { researchContextSelectionReceiptV3Schema } from '../../packages/contracts_ts/src/generated/r2'
import type { ResearchContextSelectionReceiptV3, ResearchContextGraphPriorRefV3 } from '../../packages/contracts_ts/src/index'

type IsAny<T> = 0 extends (1 & T) ? true : false
const topAny: IsAny<ResearchContextSelectionReceiptV3> = false
const inputAny: IsAny<ResearchContextSelectionReceiptV3['considered_inputs'][number]['input']> = false
const priorAny: IsAny<ResearchContextGraphPriorRefV3['row']> = false
const kind: ResearchContextGraphPriorRefV3['kind'] = 'graph_round_admission'
const ranking: ResearchContextSelectionReceiptV3['selector_identity']['ranking_rules_ref'] = 'research-context.relevance-authority-recency.v2'
const hash: ResearchContextSelectionReceiptV3['selection_payload_hash_kind'] = 'canonical_context_selection.v3'
void [topAny, inputAny, priorAny, kind, ranking, hash]

for (const sample of cases) {
  const raw = JSON.parse(JSON.stringify(sample.value)) as Record<string, unknown>
  if ('expand_considered_count' in sample) {
    const first = (raw.considered_inputs as Array<Record<string, unknown>>)[0]
    raw.considered_inputs = Array.from({ length: sample.expand_considered_count as number }, (_, index) => ({
      ...first, input: { ...first.input as Record<string, unknown>, evidence_id: `declared-count-${index}` },
    }))
  }
  const before = JSON.stringify(raw)
  const accepted = researchContextSelectionReceiptV3Schema.safeParse(raw).success
  const expected = sample.expected_shape && sample.expected_time_semantics
  if (accepted !== expected) throw new Error(`${sample.name}: accepted=${accepted}, expected=${expected}`)
  if (JSON.stringify(raw) !== before) throw new Error(`${sample.name}: changed original declaration`)
}
console.log(JSON.stringify({ cases: cases.length, matched: true, scope: 'V3 shape/time/ref relations only' }))
