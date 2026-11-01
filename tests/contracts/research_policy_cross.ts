// Real generated Zod validators consume the same explicit Python/JSON cases.
import cases from '../fixtures/research_policy_contract_cases.json'
import {
  researchKernelSafeguardsSchema,
  researchPolicyRuleSchema,
  researchTaskScopeSchema,
  type ResearchPolicyRule,
} from '../../packages/contracts_ts/src/generated/r2.ts'

type IsAny<T> = 0 extends (1 & T) ? true : false
const ruleMustNotBeAny: IsAny<ResearchPolicyRule> = false
const schemas = {
  research_kernel_safeguards: researchKernelSafeguardsSchema,
  research_task_scope: researchTaskScopeSchema,
  research_policy_rule: researchPolicyRuleSchema,
}
const results = cases.map((item) => {
  const schema = schemas[item.definition as keyof typeof schemas]
  const accepted = schema.safeParse(item.value).success
  if (accepted !== item.expected) throw new Error(`policy case mismatch: ${item.name}`)
  return { name: item.name, accepted, expected: item.expected }
})
console.log(JSON.stringify({ ruleMustNotBeAny, count: results.length, results }, null, 2))
