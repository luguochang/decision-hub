import samples from '../fixtures/research_execution_unicode_cases.json'
import seeds from '../fixtures/research_execution_contract_cases.json'
import * as generated from '../../packages/contracts_ts/src/generated/r2.ts'
import type { ResearchExecutionPromptV2 } from '../../packages/contracts_ts/src/index.ts'

type IsAny<T> = 0 extends (1 & T) ? true : false
const promptMustNotBeAny: IsAny<ResearchExecutionPromptV2['prompt']> = false
void promptMustNotBeAny

const results = samples.map(sample => {
  const seed = seeds.find(item => item.name === sample.seed)
  if (!seed) throw new Error(`missing canonical seed: ${sample.seed}`)
  const value = structuredClone(seed.value) as unknown as Record<string, unknown>
  let cursor = value
  for (const key of sample.field_path.slice(0, -1)) {
    cursor = cursor[key] as Record<string, unknown>
  }
  const text = sample.character.repeat(sample.repeat)
  cursor[sample.field_path[sample.field_path.length - 1]] = text
  const schemaName = sample.definition.replace(/_([a-z0-9])/g,
    (_, letter: string) => letter.toUpperCase()) + 'Schema'
  const schema = generated[schemaName as keyof typeof generated]
  if (!schema || typeof schema !== 'object' || !('safeParse' in schema)) {
    throw new Error(`missing generated schema ${schemaName}`)
  }
  const accepted = schema.safeParse(value).success
  return { name: sample.name, codePoints: sample.repeat, utf16Units: text.length,
    accepted, expected: sample.expected }
})
console.log(JSON.stringify({ sameInputs: results.length, results, scope: 'shape only' }, null, 2))
if (results.some(item => item.accepted !== item.expected)) {
  throw new Error('New execution Unicode length differs from canonical/Python')
}
