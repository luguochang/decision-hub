import cases from '../fixtures/research_context_selection_contract_cases.json'
import {
  researchContextSelectionReceiptV1Schema,
  researchContextSelectionExecutionLinkV1Schema,
} from '../../packages/contracts_ts/src/generated/r2'
import type {
  ResearchContextSelectionReceiptV1,
  ResearchContextSelectionExecutionLinkV1,
} from '../../packages/contracts_ts/src/index'
import {
  contextSelectionTimeIssueCodes, contextPayloadUtf8Size, parseContextMicroseconds,
} from '../../packages/contracts_ts/src/contextSelectionSemantics'

type IsAny<T> = 0 extends (1 & T) ? true : false
const selectionAny: IsAny<ResearchContextSelectionReceiptV1> = false
const fullRequestAny: IsAny<ResearchContextSelectionReceiptV1['projected_request']> = false
const witnessInputAny: IsAny<ResearchContextSelectionReceiptV1['considered_inputs'][number]['input']> = false
const rowAny: IsAny<ResearchContextSelectionExecutionLinkV1['execution_row_identity']> = false
void [selectionAny, fullRequestAny, witnessInputAny, rowAny]

const schemas = {
  research_context_selection_receipt_v1: researchContextSelectionReceiptV1Schema,
  research_context_selection_execution_link_v1: researchContextSelectionExecutionLinkV1Schema,
}

for (const sample of cases) {
  const raw = JSON.parse(JSON.stringify(sample.value)) as Record<string, unknown>
  if ('expand_considered_count' in sample) {
    const first = (raw.considered_inputs as Array<Record<string, unknown>>)[0]
    raw.considered_inputs = Array.from({ length: sample.expand_considered_count as number }, (_, index) => ({
      ...first, input: { ...first.input as Record<string, unknown>, evidence_id: `declared-count-${index}` },
    }))
  }
  const schema = schemas[sample.definition as keyof typeof schemas]
  const accepted = schema.safeParse(raw).success
  const expected = sample.expected_shape && sample.expected_time_semantics
  if (accepted !== expected) throw new Error(`${sample.name}: Zod accepted=${accepted}, expected=${expected}`)
  if (sample.expected_shape) {
    const codes = contextSelectionTimeIssueCodes(raw)
    if (JSON.stringify(codes) !== JSON.stringify(sample.expected_issue_codes)) throw new Error(`${sample.name}: mismatched semantic codes ${codes}`)
  }
}
if (parseContextMicroseconds('2026-10-02T08:00:00.000002+08:00') - parseContextMicroseconds('2026-10-02T00:00:00.000001Z') !== 1n) throw new Error('microsecond precision lost')
const nonFinite = JSON.parse(JSON.stringify(cases[0].value)) as Record<string, unknown>
const finiteRequest = nonFinite.projected_request as Record<string, unknown>
const budget = finiteRequest.execution_budget as Record<string, unknown>
budget.max_estimated_cost_usd = Infinity
if (researchContextSelectionReceiptV1Schema.safeParse(nonFinite).success) throw new Error('nonfinite declaration accepted')
if (!contextSelectionTimeIssueCodes(nonFinite).includes('context_selection_non_finite_number')) throw new Error('nonfinite semantic guard not reached')
const serialized = '{"text":"🧪\\u0000"}', size = contextPayloadUtf8Size(serialized)
if (contextPayloadUtf8Size(serialized, size) !== size) throw new Error('exact UTF8 boundary mismatch')
for (const [text, limit] of [[serialized, size - 1], ['\ud800', undefined]] as const) {
  let rejected = false
  try { contextPayloadUtf8Size(text, limit) } catch { rejected = true }
  if (!rejected) throw new Error('byte cap or surrogate replacement accepted')
}
console.log(JSON.stringify({ cases: cases.length, matched: true, utf8SampleBytes: size,
  scope: 'shape + pure time declarations; same serialized bytes utility; no producer/SQL/clock/source/permission proof' }))
