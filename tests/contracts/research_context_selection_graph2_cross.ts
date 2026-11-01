import cases from '../fixtures/research_context_selection_graph2_cases.json'
import { researchContextSelectionReceiptV3Schema } from '../../packages/contracts_ts/src/generated/r2'
import type { ResearchContextGraphPriorRefV3 } from '../../packages/contracts_ts/src/index'

const legacy: ResearchContextGraphPriorRefV3['row']['schema_version'] = 'research-graph-round-admission.v1'
const current: ResearchContextGraphPriorRefV3['row']['schema_version'] = 'research-graph-round-admission.v2'
void [legacy, current]

for (const sample of cases) {
  const original = JSON.stringify(sample.value)
  const accepted = researchContextSelectionReceiptV3Schema.safeParse(sample.value).success
  const expected = sample.expected_shape && sample.expected_time_semantics
  if (accepted !== expected) throw new Error(`${sample.name}: accepted=${accepted}, expected=${expected}`)
  if (JSON.stringify(sample.value) !== original) throw new Error(`${sample.name}: changed declaration`)
}
console.log(JSON.stringify({ cases: cases.length, matched: true, scope: 'explicit Graph1/2 declaration only' }))
