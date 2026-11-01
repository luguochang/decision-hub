// Same samples as canonical/Python. These are shapes, not producer attestations.
import cases from '../fixtures/research_binding_contract_cases.json'
import * as generated from '../../packages/contracts_ts/src/generated/r2.ts'
import type {
  ResearchFactoryScopeSource, ResearchTaskReceiptV2, ResearchPackDefinitionV2,
} from '../../packages/contracts_ts/src/generated/r2.ts'

type IsAny<T> = 0 extends (1 & T) ? true : false
const sourceMustNotBeAny: IsAny<ResearchFactoryScopeSource> = false
const requestMustNotBeAny: IsAny<ResearchTaskReceiptV2['request']> = false
const evidenceMustNotBeAny: IsAny<ResearchPackDefinitionV2['evidence_policy']> = false

const results = cases.map((item) => {
  const name = item.definition.replace(/_([a-z0-9])/g, (_, letter: string) => letter.toUpperCase()) + 'Schema'
  const schema = generated[name as keyof typeof generated]
  if (!schema || typeof schema !== 'object' || !('safeParse' in schema)) throw new Error(`missing generated ${name}`)
  const accepted = schema.safeParse(item.value).success
  return { name: item.name, accepted, expected: item.expected }
})
console.log(JSON.stringify({ sourceMustNotBeAny, requestMustNotBeAny, evidenceMustNotBeAny, count: results.length, results }, null, 2))
if (results.some((item) => item.accepted !== item.expected)) throw new Error('binding cases differ from canonical/Python')
