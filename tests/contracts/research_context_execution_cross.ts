import cases from '../fixtures/research_context_execution_cases.json'
import * as generated from '../../packages/contracts_ts/src/generated/r2'
import type {
  ResearchExecutionMethodReceiptV3,
  ResearchExecutionRequestReceiptV3,
  ResearchContextSelectionExecutionLinkV2,
} from '../../packages/contracts_ts/src/index'

type IsAny<T> = 0 extends (1 & T) ? true : false
const binding: IsAny<ResearchExecutionMethodReceiptV3['definition']['context_selection_binding']> = false
const actual: IsAny<ResearchExecutionRequestReceiptV3['request']> = false
const row: IsAny<ResearchContextSelectionExecutionLinkV2['selection_row_identity']> = false
void [binding, actual, row]
for (const sample of cases) {
  const parts = sample.definition.split('_')
  const schemaName = parts[0] + parts.slice(1).map(part => part[0].toUpperCase() + part.slice(1)).join('') + 'Schema'
  const schema = generated[schemaName as keyof typeof generated]
  if (!schema || typeof schema !== 'object' || !('safeParse' in schema)) {
    throw new Error(`Missing new schema ${schemaName}`)
  }
  const accepted = schema.safeParse(sample.value).success
  if (accepted !== sample.expected) throw new Error(`${sample.name}: accepted=${accepted}`)
}
console.log(JSON.stringify({ cases: cases.length, matched: true, scope: 'shape and pure time only' }))
