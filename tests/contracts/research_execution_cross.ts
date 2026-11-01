import cases from '../fixtures/research_execution_contract_cases.json'
import * as generated from '../../packages/contracts_ts/src/generated/r2'
import type {
  ResearchExecutionMethodReceiptV2,
  ResearchExecutionRequestReceiptV2,
} from '../../packages/contracts_ts/src/index'

type IsAny<T> = 0 extends (1 & T) ? true : false
const methodDefinitionIsAny: IsAny<ResearchExecutionMethodReceiptV2['definition']> = false
const bundleTaskIsAny: IsAny<ResearchExecutionMethodReceiptV2['definition']['bundle']['task']> = false
const actualRequestIsAny: IsAny<ResearchExecutionRequestReceiptV2['request']> = false
const policyBindingIsAny: IsAny<ResearchExecutionMethodReceiptV2['definition']['policy_binding']> = false
void [methodDefinitionIsAny, bundleTaskIsAny, actualRequestIsAny, policyBindingIsAny]

for (const sample of cases) {
  const parts = sample.definition.split('_')
  const schemaName = parts[0] + parts.slice(1).map(part => part[0].toUpperCase() + part.slice(1)).join('') + 'Schema'
  const schema = generated[schemaName as keyof typeof generated]
  if (!schema || typeof schema !== 'object' || !('safeParse' in schema)) {
    throw new Error(`Missing generated schema ${schemaName}`)
  }
  const accepted = schema.safeParse(sample.value).success
  if (accepted !== sample.expected) throw new Error(`${sample.name}: Zod accepted=${accepted}, expected=${sample.expected}`)
}
console.log(JSON.stringify({ cases: cases.length, allMatched: true, scope: 'shape only; no producer/executor/DB/Host' }))
