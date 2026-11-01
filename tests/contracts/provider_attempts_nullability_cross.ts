import cases from '../fixtures/provider_attempts_nullability_cases.json'
import {
  errorProvenanceSchema,
  researchCapabilityResultSchema,
} from '../../packages/contracts_ts/src/generated/r2'
import type {
  ErrorProvenance,
  ResearchCapabilityResult,
} from '../../packages/contracts_ts/src/generated/r2'

const errorAllowsNull: null extends ErrorProvenance['provider_attempts'] ? true : false = false
const resultAllowsNull: null extends ResearchCapabilityResult['provider_attempts'] ? true : false = false
void [errorAllowsNull, resultAllowsNull]

const schemas = {
  error_provenance: errorProvenanceSchema,
  research_capability_result: researchCapabilityResultSchema,
}
const observations = cases.map((sample) => {
  const raw = JSON.parse(JSON.stringify(sample.value)) as Record<string, unknown>
  const schema = schemas[sample.definition as keyof typeof schemas]
  const parsed = schema.safeParse(raw)
  if (parsed.success && (sample.name.endsWith('-omitted') || sample.name.endsWith('-empty'))) {
    if (JSON.stringify(parsed.data.provider_attempts) !== '[]') throw new Error(`${sample.name}: default changed`)
  }
  if (parsed.success && sample.name.endsWith('-component-null')) {
    if (JSON.stringify(parsed.data.provider_attempts) !== JSON.stringify(raw.provider_attempts)) {
      throw new Error(`${sample.name}: legitimate unknown component fields changed`)
    }
  }
  return { name: sample.name, expected_valid: sample.expected_valid, zod_valid: parsed.success }
})
console.log(JSON.stringify({ cases: observations.length, observations,
  scope: 'two optional nonnullable arrays only; no historical API/query read claim' }))
const mismatches = observations.filter((item) => item.expected_valid !== item.zod_valid)
if (mismatches.length) throw new Error(`contract mismatch: ${JSON.stringify(mismatches)}`)
