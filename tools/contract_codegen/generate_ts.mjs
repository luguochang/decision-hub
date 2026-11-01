import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { jsonSchemaToZod, parseSchema } from 'json-schema-to-zod'
import YAML from 'yaml'

const toolDir = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(toolDir, '..', '..')
const output = process.argv[2] ?? path.join(root, 'packages/contracts_ts/src/generated/r2.ts')

const sources = [
  ['contracts/schemas/research_learning.schema.yaml', null],
  ['contracts/schemas/research_skill.schema.yaml', null],
  ['contracts/schemas/action_execution_v1.schema.yaml', null],
  ['contracts/schemas/research_signal.schema.yaml', null],
  ['contracts/schemas/decision_product_v1.schema.yaml', null],
  ['contracts/schemas/decision_product_v2.schema.yaml', null],
  ['contracts/schemas/history_product_status_v2.schema.yaml', null],
  ['contracts/schemas/provider_input.schema.yaml', null],
  ['contracts/schemas/independent_review.schema.yaml', null],
  ['contracts/schemas/market_risk_view.schema.yaml', null],
  ['contracts/schemas/risk_research_request.schema.yaml', null],
  ['contracts/schemas/notification_envelope.schema.yaml', null],
  ['contracts/schemas/harness_comparison.schema.yaml', null],
  ['contracts/schemas/research_execution_binding.schema.yaml', null],
  ['contracts/schemas/research_binding.schema.yaml', null],
  ['contracts/schemas/research_policy.schema.yaml', null],
  ['contracts/schemas/research_market.schema.yaml', null],
  ['contracts/schemas/event_asset.schema.yaml', null],
  ['contracts/schemas/product_delivery.schema.yaml', null],
  ['contracts/schemas/study_effect_tracking.schema.yaml', null],
  ['contracts/schemas/source_history.schema.yaml', null],
  ['contracts/schemas/historical_data.schema.yaml', null],
  ['contracts/schemas/analytical_outlook.schema.yaml', null],
  ['contracts/schemas/study.schema.yaml', null],
  ['contracts/schemas/dsh_host_bridge.schema.yaml', null],
  ['contracts/schemas/agentic_research.schema.yaml', null],
  ['contracts/schemas/source_acquisition.schema.yaml', null],
  ['contracts/schemas/research_library.schema.yaml', null],
  ['contracts/schemas/evolution_job.schema.yaml', null],
  ['contracts/schemas/workbench_assets.schema.yaml', null],
  ['contracts/schemas/research_fact.schema.yaml', null],
  ['contracts/schemas/run_inspector.schema.yaml', new Set([
    'timeline_item',
    'evidence_lineage',
    'version_lineage',
    'orchestration_lineage',
  ])],
  ['contracts/schemas/research_product_view.schema.yaml', null],
  ['contracts/schemas/research_context_selection.schema.yaml', null],
  ['contracts/schemas/research_context_selection_v2.schema.yaml', null],
  ['contracts/schemas/research_context_selection_v3.schema.yaml', null],
  ['contracts/schemas/research_context_execution.schema.yaml', null],
  ['contracts/schemas/read_command_receipt.schema.yaml', null],
  ['contracts/schemas/revision_feedback_evaluation.schema.yaml', null],
]

const names = {
  evaluation_dataset_manifest: ['evaluationDatasetSchema', 'EvaluationDataset'],
  experiment_manifest: ['experimentSchema', 'Experiment'],
  promotion_decision_create: ['promotionDecisionCommandSchema', 'PromotionDecisionCommand'],
  timeline_item: ['runTimelineItemSchema', 'RunTimelineItem'],
}

function words(value) {
  return value.split('_').filter(Boolean)
}

function camel(value) {
  const parts = words(value)
  return parts[0] + parts.slice(1).map((part) => part[0].toUpperCase() + part.slice(1)).join('')
}

function pascal(value) {
  return words(value).map((part) => part[0].toUpperCase() + part.slice(1)).join('')
}

const schemaCache = new Map()

function loadSchema(schemaPath) {
  const normalized = path.resolve(schemaPath)
  if (!schemaCache.has(normalized)) {
    schemaCache.set(normalized, YAML.parse(fs.readFileSync(normalized, 'utf8')))
  }
  return schemaCache.get(normalized)
}

function resolveRefs(value, rootSchema, sourcePath, seen = new Set()) {
  if (Array.isArray(value)) {
    return value.map((item) => resolveRefs(item, rootSchema, sourcePath, seen))
  }
  if (!value || typeof value !== 'object') return value
  if (typeof value.$ref === 'string' && value.$ref.startsWith('#/$defs/')) {
    const key = value.$ref.slice('#/$defs/'.length)
    const identity = `${sourcePath}#${key}`
    if (seen.has(identity)) throw new Error(`recursive schema is not supported by R2 codegen: ${key}`)
    const target = rootSchema.$defs?.[key]
    if (!target) throw new Error(`unresolved local schema reference: ${value.$ref}`)
    return resolveRefs(target, rootSchema, sourcePath, new Set([...seen, identity]))
  }
  if (typeof value.$ref === 'string' && value.$ref.includes('#/$defs/')) {
    const [relativePath, fragment] = value.$ref.split('#/$defs/')
    const targetPath = path.resolve(path.dirname(sourcePath), relativePath)
    const targetSchema = loadSchema(targetPath)
    const identity = `${targetPath}#${fragment}`
    if (seen.has(identity)) {
      throw new Error(`recursive schema is not supported by R2 codegen: ${identity}`)
    }
    const target = targetSchema.$defs?.[fragment]
    if (!target) throw new Error(`unresolved external schema reference: ${value.$ref}`)
    return resolveRefs(target, targetSchema, targetPath, new Set([...seen, identity]))
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, resolveRefs(item, rootSchema, sourcePath, seen)]),
  )
}

// For new policy rules, required distinct literal discriminators prove that
// at most one branch can match. A typed union therefore preserves oneOf's
// exact-one semantics; leave other schemas and overlapping oneOf untouched.
function typedPolicyUnions(value) {
  if (Array.isArray(value)) return value.map(typedPolicyUnions)
  if (!value || typeof value !== 'object') return value
  const result = Object.fromEntries(Object.entries(value).map(
    ([key, item]) => [key, typedPolicyUnions(item)],
  ))
  if (Array.isArray(result.oneOf)) {
    const ids = result.oneOf.map((branch) => branch?.properties?.rule_id?.const)
    if (ids.every((id) => typeof id === 'string') && new Set(ids).size === ids.length &&
        result.oneOf.every((branch) => branch.required?.includes('rule_id'))) {
      result.anyOf = result.oneOf
      delete result.oneOf
    }
  }
  return result
}

// New binding selection is an object plus closed if/then alternatives. Zod's
// converter prioritizes "type: object" and otherwise drops those constraints.
// Derive complete, disjoint branches from the canonical alternatives rather
// than hand-writing a second source-selection contract.
function closedBindingConditionals(value) {
  if (Array.isArray(value)) return value.map(closedBindingConditionals)
  if (!value || typeof value !== 'object') return value
  const result = Object.fromEntries(Object.entries(value).map(
    ([key, item]) => [key, closedBindingConditionals(item)],
  ))
  if (result.type === 'object' && Array.isArray(result.allOf)) {
    const selector = result.properties?.schema_version?.const === 'research-factory-study-source.v2' ||
      result.properties?.schema_version?.const === 'research-task-scope.v2' ? 'instrument' : 'selected_from'
    const choices = result.properties?.[selector]?.enum
    const alternatives = result.allOf
    const ids = alternatives.map((item) => item?.if?.properties?.[selector]?.const)
    if (!Array.isArray(choices) || !result.required?.includes(selector) ||
        choices.length !== ids.length || new Set(ids).size !== ids.length ||
        !choices.every((item) => ids.includes(item)) ||
        !alternatives.every((item) => item.then?.properties && !item.else &&
          Object.keys(item.if).join() === 'properties' &&
          Object.keys(item.if.properties).join() === selector &&
          Object.keys(item.then).join() === 'properties')) {
      throw new Error('unsupported binding conditional; refusing to drop constraints')
    }
    const { allOf, ...base } = result
    return { anyOf: alternatives.map((item, index) => ({
      ...base,
      properties: { ...base.properties, ...item.then.properties,
        [selector]: { type: 'string', const: ids[index] } },
    })) }
  }
  return result
}

// The converter omits object-key/count constraints. This override is enabled
// only for the new binding document, deriving each bound from its canonical
// schema (including nested records), rather than relaxing the Python mirror.
function boundedBindingObjects(schema, refs) {
  if (schema.type !== 'object' ||
      (schema.minProperties === undefined && schema.maxProperties === undefined &&
       schema.propertyNames === undefined)) return undefined
  const { minProperties, maxProperties, propertyNames, ...base } = schema
  const expression = parseSchema(base, refs)
  const checks = []
  if (minProperties !== undefined) checks.push(`keys.length < ${JSON.stringify(minProperties)}`)
  if (maxProperties !== undefined) checks.push(`keys.length > ${JSON.stringify(maxProperties)}`)
  const countCheck = checks.length ?
    `if (${checks.join(' || ')}) ctx.addIssue({ code: 'custom', message: 'Object property count outside canonical bounds' });` : ''
  const nameCheck = propertyNames ?
    `for (const key of keys) if (!(${parseSchema(propertyNames, refs)}).safeParse(key).success) ctx.addIssue({ code: 'custom', path: [key], message: 'Object key outside canonical bounds' });` : ''
  return `${expression}.superRefine((value, ctx) => { const keys = Object.keys(value); ${countCheck} ${nameCheck} })`
}

// JSON Schema string lengths count Unicode code points, whereas Zod 3's
// min/max use UTF-16 units. Specialize only new execution declarations,
// including resolved dependency strings; do not alter old validator blocks.
function boundedExecutionStrings(schema, refs) {
  const stringType = schema.type === 'string' ||
    (Array.isArray(schema.type) && schema.type.includes('string'))
  if (!stringType || (schema.minLength === undefined && schema.maxLength === undefined)) {
    return undefined
  }
  const { minLength, maxLength, ...base } = schema
  const expression = parseSchema(base, refs)
  const maximum = maxLength === undefined ? '' :
    `if (length > ${JSON.stringify(maxLength)}) { ctx.addIssue({ code: 'custom', message: 'String code-point length exceeds canonical maximum' }); return; }`
  const minimum = minLength === undefined ? '' :
    `if (length < ${JSON.stringify(minLength)}) ctx.addIssue({ code: 'custom', message: 'String code-point length below canonical minimum' });`
  const minimumOnlyStop = maxLength === undefined && minLength !== undefined ?
    `if (length >= ${JSON.stringify(minLength)}) break;` : ''
  // Native string iteration yields code points without materializing an array.
  // A maximum rejects at max+1; a minimum-only check stops once satisfied.
  return `${expression}.superRefine((value, ctx) => { if (typeof value !== 'string') return; const iterator = value[Symbol.iterator](); let length = 0; while (!iterator.next().done) { length += 1; ${maximum} ${minimumOnlyStop} } ${minimum} })`
}

// Only the new time declaration document uses these complete disjoint modes.
function closedContextConditionals(value) {
  if (Array.isArray(value)) return value.map(closedContextConditionals)
  if (!value || typeof value !== 'object') return value
  const result = Object.fromEntries(Object.entries(value).map(
    ([key, item]) => [key, closedContextConditionals(item)],
  ))
  if (result.type !== 'object' || !Array.isArray(result.allOf)) return result
  const alternatives = result.allOf
  const selector = Object.keys(alternatives[0]?.if?.properties ?? {}).join()
  const choices = result.properties?.[selector]?.enum
  const ids = alternatives.map(item => item?.if?.properties?.[selector]?.const)
  if (!['execution_mode', 'source_kind'].includes(selector) || !Array.isArray(choices) ||
      !result.required?.includes(selector) || choices.length !== ids.length ||
      new Set(ids).size !== ids.length || !choices.every(item => ids.includes(item)) ||
      !alternatives.every(item => item.then?.properties && !item.else &&
        Object.keys(item.if).join() === 'properties' &&
        Object.keys(item.if.properties).join() === selector &&
        Object.keys(item.then).join() === 'properties')) {
    throw new Error('unsupported context conditional; refusing to drop constraints')
  }
  const { allOf, ...base } = result
  return { anyOf: alternatives.map((item, index) => ({
    ...base, properties: { ...base.properties, ...item.then.properties,
      [selector]: { type: 'string', const: ids[index] } },
  })) }
}

function contextDateStrings(schema, refs) {
  if (schema.format !== 'date-time') return undefined
  const { format, ...base } = schema
  const expression = boundedExecutionStrings(base, refs) ?? parseSchema(base, refs)
  return `${expression}.superRefine((value, ctx) => { if (typeof value === 'string' && !contextTimestampShapeValid(value)) ctx.addIssue({ code: 'custom', message: 'context_selection_invalid_timestamp' }); })`
}

// Only V3's top declaration adds graph/attempt relationships to the unchanged
// closed live/replay alternatives. Refuse an altered canonical ABI, then keep
// these additional checks in the appended V3 top superRefine below.
function contextV3TopConditionals(value, key) {
  if (key !== 'research_context_selection_receipt_v3') return value
  const extras = value.allOf?.slice(2)
  const expected = [
    { if: { properties: { graph_round: { const: 1 } } },
      then: { properties: { decision_basis_ref: { type: 'null' } } },
      else: { properties: { decision_basis_ref: value.properties.decision_basis_ref.anyOf.find(item => item.type === 'object') } } },
    { if: { properties: { attempt_basis: { const: 'initial_round' } } },
      then: { properties: { graph_round: { const: 1 }, attempt_no: { const: 1 }, parent_selection_id: { type: 'null' } } } },
    { if: { properties: { attempt_basis: { const: 'next_round' } } },
      then: { properties: { graph_round: { minimum: 2 }, attempt_no: { const: 1 }, parent_selection_id: { type: 'null' } } } },
    { if: { properties: { attempt_basis: { const: 'explicit_reselection' } } },
      then: { properties: { attempt_no: { minimum: 2 }, parent_selection_id: { type: 'string' } } } },
  ]
  if (JSON.stringify(extras) !== JSON.stringify(expected)) {
    throw new Error('unsupported V3 graph/attempt conditional; refusing to drop constraints')
  }
  return { ...value, allOf: value.allOf.slice(0, 2) }
}

const contextV3Semantics = `
import { parseContextMicroseconds } from '../contextSelectionSemantics'

function contextSelectionV3IssueCodes(value: Record<string, unknown>): string[] {
  const issues = contextSelectionTimeIssueCodes({ ...value, schema_version: 'research-context-selection.v1' })
  const issue = (code: string) => { if (!issues.includes(code)) issues.push(code) }
  const prior = value.decision_basis_ref
  const round = value.graph_round
  if (round === 1) {
    if (prior !== null) issue('context_selection_initial_prior_unexpected')
  } else if (prior === null || typeof prior !== 'object' || Array.isArray(prior)) {
    issue('context_selection_graph_prior_required')
  } else {
    const reference = prior as Record<string, unknown>
    const rawRow = reference.row
    const row = rawRow !== null && typeof rawRow === 'object' && !Array.isArray(rawRow)
      ? rawRow as Record<string, unknown> : undefined
    if (reference.kind !== 'graph_round_admission' || reference.run_id !== value.run_id ||
        typeof round !== 'number' || reference.graph_round !== round - 1 || !row ||
        row.role !== 'research_graph_round:' + (round - 1) ||
        (row.schema_version !== 'research-graph-round-admission.v1' &&
         row.schema_version !== 'research-graph-round-admission.v2')) {
      issue('context_selection_graph_prior_identity_mismatch')
    } else {
      try {
        if (parseContextMicroseconds(row.recorded_at) > parseContextMicroseconds(value.recorded_at))
          issue('context_selection_graph_prior_after_selection')
      } catch { issue('context_selection_invalid_timestamp') }
    }
  }
  if ((value.attempt_basis === 'initial_round' &&
       (round !== 1 || value.attempt_no !== 1 || value.parent_selection_id !== null)) ||
      (value.attempt_basis === 'next_round' &&
       (typeof round !== 'number' || round < 2 || value.attempt_no !== 1 || value.parent_selection_id !== null)) ||
      (value.attempt_basis === 'explicit_reselection' &&
       (typeof value.attempt_no !== 'number' || value.attempt_no < 2 || typeof value.parent_selection_id !== 'string')))
    issue('context_selection_attempt_binding_invalid')
  return issues
}
`

const blocks = []
const contextBlocks = []
const contextV2Blocks = []
const contextV3Blocks = []
// This new leaf uses object-plus-oneOf restrictions. Derive full branches
// from canonical fields; only disjoint required discriminators permit union.
function closedFeedbackAlternatives(value) {
  if (Array.isArray(value)) return value.map(closedFeedbackAlternatives)
  if (!value || typeof value !== 'object') return value
  const result = Object.fromEntries(Object.entries(value).map(
    ([key, item]) => [key, closedFeedbackAlternatives(item)],
  ))
  if (result.type !== 'object' || !Array.isArray(result.oneOf)) return result
  const alternatives = result.oneOf
  const choices = constraint => constraint && Object.hasOwn(constraint, 'const')
    ? [constraint.const] : constraint?.enum
  for (let left = 0; left < alternatives.length; left += 1) {
    for (let right = left + 1; right < alternatives.length; right += 1) {
      const disjoint = (result.required ?? []).some(key => {
        const a = choices(alternatives[left]?.properties?.[key])
        const b = choices(alternatives[right]?.properties?.[key])
        return Array.isArray(a) && Array.isArray(b) && !a.some(item => b.includes(item))
      })
      if (!disjoint) throw new Error('feedback alternatives lack disjoint required identities')
    }
  }
  if (!alternatives.every(branch => Object.keys(branch).join() === 'properties')) {
    throw new Error('unsupported feedback alternative; refusing to drop constraints')
  }
  const { oneOf, ...base } = result
  return { anyOf: alternatives.map(branch => ({
    ...base,
    properties: Object.fromEntries(Object.entries(base.properties).map(([key, original]) => {
      const constraint = branch.properties[key]
      if (!constraint) return [key, original]
      // Array length refinements retain the original type/items. Other
      // overlays intersect the complete original field instead of replacing it.
      return [key, original.type === 'array'
        ? { ...original, ...constraint } : { allOf: [original, constraint] }]
    })),
  })) }
}

function feedbackScalarConstraints(schema, refs) {
  if (schema.format === 'date-time') {
    const { format, ...base } = schema
    const expression = boundedExecutionStrings(base, refs) ?? parseSchema(base, refs)
    return `${expression}.superRefine((value, ctx) => { if (typeof value === 'string' && !revisionFeedbackTimestampValid(value)) ctx.addIssue({ code: 'custom', message: 'revision_feedback_evaluation_invalid_timestamp' }); })`
  }
  if (schema.type === 'number' || (Array.isArray(schema.type) && schema.type.includes('number'))) {
    const expression = parseSchema(schema, { ...refs, parserOverride: undefined })
    return `${expression}.superRefine((value, ctx) => { if (typeof value === 'number' && !Number.isFinite(value)) ctx.addIssue({ code: 'custom', message: 'revision_feedback_evaluation_non_finite_number' }); })`
  }
  return boundedExecutionStrings(schema, refs)
}

const feedbackSemantics = `// Feedback declarations do not authenticate stored SQL or payload hashes.
function revisionFeedbackTimestampValid(value: string): boolean {
  const match = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\\.[0-9]+)?(Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$/.exec(value)
  if (!match || match[0] !== value) return false
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number)
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  return year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1] && hour <= 23 && minute <= 59 && second <= 59
}
function revisionFeedbackRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? Object.fromEntries(Object.entries(value)) : {}
}
function revisionFeedbackBindingIssues(value: unknown): string[] {
  const binding = revisionFeedbackRecord(value), issues: string[] = []
  if (binding.evaluation_version !== 'research-value.revision-feedback.v1:' + binding.basis_hash) issues.push('revision_feedback_evaluation_version_basis_mismatch')
  const validation = Array.isArray(binding.validation_issues) ? binding.validation_issues : []
  if (new Set(validation).size !== validation.length) issues.push('revision_feedback_evaluation_duplicate_validation_issue')
  const witnesses = Array.isArray(binding.source_witnesses) ? binding.source_witnesses : []
  const identities = witnesses.map(item => { const row = revisionFeedbackRecord(item); return JSON.stringify([row.source_table, row.primary_key]); })
  if (new Set(identities).size !== identities.length) issues.push('revision_feedback_evaluation_duplicate_source_identity')
  return issues
}
function revisionFeedbackEvaluationIssues(raw: unknown): string[] {
  const value = revisionFeedbackRecord(raw), feedback = revisionFeedbackRecord(value.feedback), issues: string[] = []
  if (feedback.created_by !== value.owner_id) issues.push('revision_feedback_evaluation_owner_mismatch')
  if (feedback.target_type !== 'report_revision' || feedback.target_id !== value.revision_id) issues.push('revision_feedback_evaluation_revision_mismatch')
  if (value.binding === null) return issues
  const binding = revisionFeedbackRecord(value.binding), result = revisionFeedbackRecord(value.value)
  issues.push(...revisionFeedbackBindingIssues(binding))
  const validation = Array.isArray(binding.validation_issues) ? binding.validation_issues : []
  if (value.status === 'unavailable') {
    if (!validation.includes(value.reason)) issues.push('revision_feedback_evaluation_reason_missing_from_issues')
  } else {
    if (validation.length !== 0) issues.push('revision_feedback_evaluation_available_has_issues')
    if (result.run_id !== binding.source_run_id) issues.push('revision_feedback_evaluation_run_mismatch')
    if (result.artifact_id !== binding.artifact_id) issues.push('revision_feedback_evaluation_artifact_mismatch')
    if (result.evaluation_version !== binding.evaluation_version) issues.push('revision_feedback_evaluation_value_version_mismatch')
  }
  return issues
}`

const isContextSource = value => value.endsWith('/research_context_selection.schema.yaml') ||
  value.endsWith('/research_context_selection_v2.schema.yaml') ||
  value.endsWith('/research_context_selection_v3.schema.yaml') ||
  value.endsWith('/research_context_execution.schema.yaml')
const contextExecutionBlocks = []
const readCommandBlocks = []
const feedbackBlocks = []

// Action V1 has closed selectors. Materialize each canonical if/then branch
// so the generic converter cannot silently discard position/probability rules.
function decisionConditionals(value) {
  if (Array.isArray(value)) return value.map(decisionConditionals)
  if (!value || typeof value !== 'object') return value
  const result = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, decisionConditionals(item)]))
  if (result.type !== 'object' || !Array.isArray(result.allOf)) return result
  const selectors = result.allOf.map(item => Object.keys(item.if?.properties ?? {}))
  if (!selectors.every(keys => keys.length === 1 && keys[0] === selectors[0][0]))
    throw new Error('decision conditional selector changed')
  const selector = selectors[0][0], choices = result.properties?.[selector]?.enum
  if (!Array.isArray(choices) || !result.required.includes(selector)) throw new Error('decision selector must be required enum')
  const { allOf, ...base } = result
  return { anyOf: choices.map(choice => {
    const properties = { ...base.properties, [selector]: { const: choice } }
    for (const rule of allOf) {
      const test = rule.if.properties[selector]
      if ((Object.hasOwn(test, 'const') ? [test.const] : test.enum).includes(choice)) {
        for (const [key, constraint] of Object.entries(rule.then.properties)) {
          if (Object.hasOwn(constraint, 'const') || constraint.type === 'null') properties[key] = constraint
          else {
            properties[key] = { ...properties[key], ...constraint }
            if (constraint.type) { delete properties[key].anyOf; delete properties[key].oneOf }
          }
        }
      }
    }
    return { ...base, properties }
  }) }
}

function decisionScalarConstraints(schema, refs) {
  if (schema['x-execution-time'] === true) {
    return `z.string().datetime({ offset: true }).regex(new RegExp(${JSON.stringify(schema.pattern)}))`
  }
  if (schema['x-decimal-precision'] === 50) {
    const expression = parseSchema(schema, { ...refs, parserOverride: undefined })
    return `${expression}.refine(value => value.replace(/^-?0[.]0*|^-/, '').replace(/[.]/g, '').length <= 50, { message: 'execution_decimal_precision_exceeded' })`
  }

  if (schema.format === 'date-time') return 'z.string().datetime({ offset: true })'
  if (schema.type === 'number' || (Array.isArray(schema.type) && schema.type.includes('number'))) {
    const expression = parseSchema(schema, { ...refs, parserOverride: undefined })
    return `${expression}.superRefine((value, ctx) => { if (typeof value === 'number' && !Number.isFinite(value)) ctx.addIssue({ code: 'custom', message: 'decision_non_finite_number' }); })`
  }
  return boundedExecutionStrings(schema, refs)
}

// Only shared/settings V2 uses these scalar refinements.
function decisionV2Shapes(value) {
  if (Array.isArray(value)) return value.map(decisionV2Shapes)
  if (!value || typeof value !== 'object') return value
  const result = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, decisionV2Shapes(item)]))
  if (result.title === 'ProbabilityEstimateV1') {
    if (result.allOf?.length !== 3) throw new Error('comparison probability alternatives changed')
    // Mixed kind/nullable selectors stay typed; the shared V2 relation checks
    // all three canonical conditions after parsing the complete object.
    delete result.allOf
  }
  return result
}

function decisionV2ScalarConstraints(schema, refs) {
  const boundedObject = boundedBindingObjects(schema, refs)
  if (boundedObject !== undefined) return boundedObject
  if (schema.title === 'ComparisonProbabilityValueV1') {
    if (schema.allOf?.length !== 2 || schema.allOf[0].type !== 'string') throw new Error('comparison probability scalar changed')
    const merged = Object.assign({}, ...schema.allOf)
    return boundedExecutionStrings(merged, refs) ?? parseSchema(merged, refs)
  }
  if (schema.format === 'date-time') {
    const { format, ...base } = schema
    const expression = boundedExecutionStrings(base, refs) ?? parseSchema(base, refs)
    return `${expression}.superRefine((value, ctx) => { if (typeof value === 'string' && !decisionV2UtcTimestampValid(value)) ctx.addIssue({ code: 'custom', message: 'decision_v2_invalid_utc' }); })`
  }
  if (schema.type === 'array' && schema.uniqueItems === true) {
    // Resolved V2 unique arrays contain only decimal or side strings.
    if (schema.items?.type !== 'string') throw new Error('decision V2 unique item type changed')
    const { uniqueItems, ...base } = schema
    const expression = parseSchema(base, refs)
    return `${expression}.refine(value => new Set(value).size === value.length, { message: 'decision_v2_duplicate_item' })`
  }
  return boundedExecutionStrings(schema, refs)
}

// This relation is additive and recursively applies to request/pack embedding.
function executionBudgetRelations(schema, refs) {
  if (schema['x-closing-reserve-less-than-total'] !== true) return undefined
  const expression = parseSchema(schema, { ...refs, parserOverride: undefined })
  return `${expression}.refine(value => value.closing_reserve_seconds < value.total_deadline_seconds, { message: 'research_closing_reserve_invalid' })`
}

function skillGenerationShape(value, key) {
  if (!['research_skill_generation_binding_v1', 'research_skill_generation_binding_v2'].includes(key)) return value
  const fields = ['trial_receipt','trial_protocol','trial_parent','trial_target','trial_pointer_version','trial_run_ordinal','financial_qualified']
  const expected = [{ if: { properties: { selection_origin: { const: 'trial_registered' } } }, then: { required: fields }, else: { not: { anyOf: fields.map(name => ({ required: [name] })) } } }]
  if (JSON.stringify(value.allOf) !== JSON.stringify(expected)) throw new Error('skill generation conditional changed')
  const { allOf, ...base } = value
  return { anyOf: ['baseline_registered','trial_registered'].map(origin => ({
    ...base,
    properties: Object.fromEntries(Object.entries(base.properties).filter(([name]) => origin === 'trial_registered' || !fields.includes(name)).map(([name, item]) => [name, name === 'selection_origin' ? { const: origin } : item])),
    required: origin === 'trial_registered' ? [...base.required, ...fields] : base.required,
  })) }
}

for (const [relativePath, selected] of sources) {
  const sourcePath = path.join(root, relativePath)
  const schema = loadSchema(sourcePath)
  for (const key of Object.keys(schema.$defs ?? {}).sort()) {
    if (selected && !selected.has(key)) continue
    const [schemaName, typeName] = names[key] ?? [`${camel(key)}Schema`, pascal(key)]
    const resolved = skillGenerationShape(resolveRefs(schema.$defs[key], schema, sourcePath), key)
    // The history relations are enforced explicitly below. The generic library
    // turns JSON-schema if/then/else into z.any intersections, losing the type.
    const historyShapes = node => {
      if (Array.isArray(node)) return node.map(historyShapes)
      if (!node || typeof node !== 'object') return node
      const result = Object.fromEntries(Object.entries(node).map(([name, value]) => [name, historyShapes(value)]))
      if (Array.isArray(node.allOf)) {
        const constraints = node.allOf.filter(value => !value.if).map(historyShapes)
        if (constraints.length) result.allOf = constraints
        else delete result.allOf
      }
      return result
    }
    const zodInput = relativePath.endsWith('/decision_product_v2.schema.yaml')
      ? decisionConditionals(decisionV2Shapes(resolved))
      : (relativePath.endsWith('/decision_product_v1.schema.yaml') || relativePath.endsWith('/research_signal.schema.yaml') || relativePath.endsWith('/research_skill.schema.yaml') || relativePath.endsWith('/research_learning.schema.yaml') || relativePath.endsWith('/action_execution_v1.schema.yaml'))
      ? decisionConditionals(resolved)
      : relativePath.endsWith('/history_product_status_v2.schema.yaml')
      ? historyShapes(resolved)
      : relativePath.endsWith('/research_policy.schema.yaml')
      ? typedPolicyUnions(key === 'research_task_scope_v2' ? closedBindingConditionals(resolved) : resolved)
      : relativePath.endsWith('/research_binding.schema.yaml')
        ? typedPolicyUnions(closedBindingConditionals(resolved))
        : relativePath.endsWith('/revision_feedback_evaluation.schema.yaml')
          ? closedFeedbackAlternatives(resolved)
        : isContextSource(relativePath)
          ? closedContextConditionals(relativePath.endsWith('/research_context_selection_v3.schema.yaml')
            ? contextV3TopConditionals(resolved, key) : resolved) : resolved
    const options = {
      module: 'esm',
      name: schemaName,
      type: typeName,
      noImport: true,
      zodVersion: 3,
      ...(relativePath.endsWith('/decision_product_v2.schema.yaml')
        ? { parserOverride: decisionV2ScalarConstraints }
        : (relativePath.endsWith('/decision_product_v1.schema.yaml') || relativePath.endsWith('/research_signal.schema.yaml') || relativePath.endsWith('/research_skill.schema.yaml') || relativePath.endsWith('/research_learning.schema.yaml') || relativePath.endsWith('/action_execution_v1.schema.yaml'))
        ? { parserOverride: decisionScalarConstraints }
        : relativePath.endsWith('/research_execution_binding.schema.yaml')
        ? { parserOverride: (node, refs) =>
          boundedExecutionStrings(node, refs) ?? boundedBindingObjects(node, refs) }
        : isContextSource(relativePath)
          ? { parserOverride: (node, refs) => contextDateStrings(node, refs) ??
            boundedExecutionStrings(node, refs) ?? boundedBindingObjects(node, refs) }
        : relativePath.endsWith('/read_command_receipt.schema.yaml')
          ? { parserOverride: boundedExecutionStrings }
        : relativePath.endsWith('/revision_feedback_evaluation.schema.yaml')
          ? { parserOverride: feedbackScalarConstraints }
        : relativePath.endsWith('/research_binding.schema.yaml')
          ? { parserOverride: boundedBindingObjects } : {}),
    }
    const originalParser = options.parserOverride
    options.parserOverride = (node, refs) => executionBudgetRelations(node, refs) ?? originalParser?.(node, refs)
    const generated = jsonSchemaToZod(zodInput, options).trim()
    // These initializers only construct independent Zod schemas. Wrap the
    // complete expression so bundlers can omit unused validators, including
    // their nested constructors, without weakening any imported validator.
    // oneOf emits a multiline superRefine. Match the entire initializer up to
    // its generated type declaration, rather than wrapping only its first line.
    const declaration = /^(export const \w+ = )([\s\S]+?)(?=\nexport type )/m
    if (!declaration.test(generated)) throw new Error(`unexpected schema declaration: ${schemaName}`)
    let expression = generated
    if (relativePath.endsWith('/decision_product_v2.schema.yaml')) {
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { for (const code of decisionProductV2Issues(value)) ctx.addIssue({ code: 'custom', message: code }); })")
    }
    if (key.startsWith('research_method_') || ['research_skill_generation_binding_v1', 'research_skill_generation_binding_v2'].includes(key)) {
      expression = expression.replace(declaration,
        `$1($2).superRefine((value, ctx) => { for (const code of researchMethodIssueCodes('${key}', value)) ctx.addIssue({ code: 'custom', message: code }); })`)
    }
    if (relativePath.endsWith('/action_execution_v1.schema.yaml')) {
      expression = expression.replace(declaration,
        `$1($2).superRefine((value, ctx) => { for (const code of actionExecutionIssueCodes('${key}', value)) ctx.addIssue({ code: 'custom', message: code }); })`)
    }

    if (relativePath.endsWith('/history_product_status_v2.schema.yaml') && key === 'history_forecast_status_v2') {
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { if ((value.state === 'available_unqualified') !== (value.probabilities !== null)) ctx.addIssue({ code: 'custom', message: 'history_forecast_probability_state_invalid' }); })")
    }
    if (relativePath.endsWith('/history_product_status_v2.schema.yaml') && key === 'history_product_status_v2') {
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { if ((value.forecast.state === 'available_unqualified') !== (value.forecast.probabilities !== null)) ctx.addIssue({ code: 'custom', message: 'history_forecast_probability_state_invalid' }); })")
    }
    if (relativePath.endsWith('/history_product_status_v2.schema.yaml') && key === 'history_asset_refresh_reply_v2') {
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { const ok = value.outcome === 'committed_verified' ? value.committed === true && value.forecast_id !== null && value.cutoff_at !== null && value.frequencies.map(x => x.threshold).join(',') === '0.02,0.05' : value.frequencies.length === 0 && (value.outcome === 'committed_readback_unconfirmed' ? value.committed === true : value.committed === null); if (!ok) ctx.addIssue({ code: 'custom', message: 'history_refresh_outcome_identity_or_probability_invalid' }); })")
    }
    if (relativePath.endsWith('/revision_feedback_evaluation.schema.yaml') &&
        ['revision_feedback_evaluation', 'revision_feedback_evaluation_binding'].includes(key)) {
      const relations = key === 'revision_feedback_evaluation'
        ? 'revisionFeedbackEvaluationIssues' : 'revisionFeedbackBindingIssues'
      expression = expression.replace(declaration,
        `$1($2).superRefine((value, ctx) => { for (const code of ${relations}(value)) ctx.addIssue({ code: 'custom', message: code }); })`)
    }
    if (relativePath.endsWith('/research_context_selection.schema.yaml') &&
        ['research_context_selection_receipt_v1', 'research_context_selection_execution_link_v1'].includes(key)) {
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { for (const code of contextSelectionTimeIssueCodes(value)) ctx.addIssue({ code: 'custom', message: code }); })")
    }
    if (relativePath.endsWith('/research_context_selection_v2.schema.yaml') &&
        key === 'research_context_selection_receipt_v2') {
      // Only the pure time formula sees V1; the real schema/body/hash remain V2.
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { for (const code of contextSelectionTimeIssueCodes({ ...value, schema_version: 'research-context-selection.v1' })) ctx.addIssue({ code: 'custom', message: code }); })")
    }
    if (relativePath.endsWith('/research_context_selection_v3.schema.yaml') &&
        key === 'research_context_selection_receipt_v3') {
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { for (const code of contextSelectionV3IssueCodes(value)) ctx.addIssue({ code: 'custom', message: code }); })")
    }
    if (relativePath.endsWith('/research_context_execution.schema.yaml') &&
        key === 'research_context_selection_execution_link_v2') {
      expression = expression.replace(declaration,
        "$1($2).superRefine((value, ctx) => { for (const code of contextSelectionTimeIssueCodes({ ...value, schema_version: 'research-context-selection-execution-link.v1' })) ctx.addIssue({ code: 'custom', message: code }); })")
    }
    const complete = expression.replace(declaration, '$1/* @__PURE__ */ (() => ($2))()')
    if (relativePath.endsWith('/revision_feedback_evaluation.schema.yaml')) feedbackBlocks.push(complete)
    else if (relativePath.endsWith('/read_command_receipt.schema.yaml')) readCommandBlocks.push(complete)
    else if (relativePath.endsWith('/research_context_execution.schema.yaml')) contextExecutionBlocks.push(complete)
    else if (relativePath.endsWith('/research_context_selection_v3.schema.yaml')) contextV3Blocks.push(complete)
    else if (relativePath.endsWith('/research_context_selection_v2.schema.yaml')) contextV2Blocks.push(complete)
    else if (relativePath.endsWith('/research_context_selection.schema.yaml')) contextBlocks.push(complete)
    else blocks.push(complete)
  }
}

// Native V2 tools need the exact small reference closure, not a recursively
// expanded Zod projection of the same repeated candidate/probability bodies.
const nativeV2Source = loadSchema(path.join(root, 'contracts/schemas/decision_product_v2.schema.yaml'))
const nativeV2Defs = {}
const includeNativeV2 = key => {
  if (Object.hasOwn(nativeV2Defs, key)) return
  const definition = nativeV2Source.$defs[key]
  if (!definition) throw new Error(`native V2 unresolved definition: ${key}`)
  nativeV2Defs[key] = definition
  const visit = node => {
    if (Array.isArray(node)) return node.forEach(visit)
    if (!node || typeof node !== 'object') return
    if (typeof node.$ref === 'string') {
      if (!node.$ref.startsWith('#/$defs/')) throw new Error('native V2 external tool ref unsupported')
      includeNativeV2(node.$ref.slice('#/$defs/'.length))
    }
    Object.values(node).forEach(visit)
  }
  visit(definition)
}
includeNativeV2('research_decision_synthesis_v2')
includeNativeV2('action_review_request_v2')
includeNativeV2('perpetual_prepare_arguments_v1')
includeNativeV2('perpetual_prepare_arguments_v2')
includeNativeV2('perpetual_finalize_arguments_v1')
blocks.push(`export const perpetualNativeToolSchemas = ${JSON.stringify({
  $defs: Object.fromEntries(Object.entries(nativeV2Defs).sort(([a], [b]) => a.localeCompare(b))),
  candidate: { $ref: '#/$defs/research_decision_synthesis_v2' },
  review_request: { $ref: '#/$defs/action_review_request_v2' },
  prepare: { oneOf: [{ $ref: '#/$defs/perpetual_prepare_arguments_v1' }, { $ref: '#/$defs/perpetual_prepare_arguments_v2' }] },
  finalize: { $ref: '#/$defs/perpetual_finalize_arguments_v1' },
})} as const`)

fs.mkdirSync(path.dirname(output), { recursive: true })
fs.writeFileSync(
  output,
  `// Generated from contracts/schemas/*.schema.yaml. Do not edit.\nimport { z } from 'zod'\nimport { actionExecutionIssueCodes } from '../actionExecutionSemantics'\nimport { researchMethodIssueCodes } from '../researchMethodSemantics'\nimport { decisionProductV2Issues, decisionV2UtcTimestampValid } from '../decisionProductV2Semantics'\n\n${blocks.join('\n\n')}\n` +
    (contextBlocks.length ? `\nimport { contextSelectionTimeIssueCodes, contextTimestampShapeValid } from '../contextSelectionSemantics'\n\n${contextBlocks.join('\n\n')}\n` : '') +
    (contextV2Blocks.length ? `\n${contextV2Blocks.join('\n\n')}\n` : '') +
    (contextV3Blocks.length ? `\n${contextV3Semantics}\n${contextV3Blocks.join('\n\n')}\n` : '') +
    (contextExecutionBlocks.length ? `\n${contextExecutionBlocks.join('\n\n')}\n` : '') +
    (readCommandBlocks.length ? `\n${readCommandBlocks.join('\n\n')}\n` : '') +
    (feedbackBlocks.length ? `\n${feedbackSemantics}\n\n${feedbackBlocks.join('\n\n')}\n` : ''),
)
