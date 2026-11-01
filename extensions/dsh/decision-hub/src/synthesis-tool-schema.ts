import { z } from 'zod'
import { parameterSchemaSpecToJsonSchema, type ParameterSchemaSpec, type ValueSchemaSpec } from '@deepseek-ai/dsh-tools'
import { createHash } from 'node:crypto'
import { perpetualNativeToolSchemas, researchDecisionSynthesisV1Schema, researchSynthesisCandidateSchema } from '@decision-hub/contracts-ts'

/** Project field names and types from the actual generated contract into DSH's
 * model-visible schema. Zod remains authoritative for ranges, dates, unique
 * lists, and the complete contract; this projection never repairs a claim. */
function project(schema: z.ZodTypeAny, decision = false): ValueSchemaSpec {
  if (schema instanceof z.ZodEffects) return project(schema.innerType(), decision)
  if (schema instanceof z.ZodOptional) return project(schema.unwrap(), decision)
  if (schema instanceof z.ZodObject) {
    if (schema._def.unknownKeys !== 'strict') throw new Error('synthesis_schema_projection_open_object')
    const properties: ParameterSchemaSpec = {}
    for (const [name, field] of Object.entries(schema.shape) as [string, z.ZodTypeAny][]) {
      properties[name] = { ...project(field, decision), ...(!field.isOptional() ? { required: true as const } : {}) }
    }
    return { type: 'object', properties, additionalProperties: false }
  }
  if (schema instanceof z.ZodArray) {
    const bounds = [schema._def.minLength && `Minimum items: ${schema._def.minLength.value}.`,
      schema._def.maxLength && `Maximum items: ${schema._def.maxLength.value}.`].filter(Boolean).join(' ')
    return { type: 'array', items: project(schema.element, decision), ...(bounds ? { description: bounds } : {}) }
  }
  if (schema instanceof z.ZodUnion) {
    const branches = schema.options.map((item: z.ZodTypeAny) => project(item, decision)) as [ValueSchemaSpec, ValueSchemaSpec, ...ValueSchemaSpec[]]
    return { oneOf: branches }
  }
  if (schema instanceof z.ZodEnum) return { type: 'string', enum: schema.options }
  if (schema instanceof z.ZodLiteral && typeof schema.value === 'string') return { type: 'string', const: schema.value }
  if (schema instanceof z.ZodLiteral && typeof schema.value === 'number') return { type: 'number', const: schema.value } as ValueSchemaSpec
  if (schema instanceof z.ZodLiteral && typeof schema.value === 'boolean') return { type: 'boolean', const: schema.value } as ValueSchemaSpec
  if (schema instanceof z.ZodBoolean) return { type: 'boolean' }
  if (schema instanceof z.ZodNull) return { type: 'null' }
  if (schema instanceof z.ZodString) {
    const checks = schema._def.checks.map(check => check.kind === 'min' ? `Minimum length: ${check.value}.`
      : check.kind === 'datetime' ? 'RFC3339 datetime with timezone required.'
        : decision && check.kind === 'regex' ? `Must match regex: ${check.regex.source}.`
          : decision && check.kind === 'max' ? `Maximum length: ${check.value}.` : '').filter(Boolean).join(' ')
    return { type: 'string', ...(checks ? { description: checks } : {}) }
  }
  if (schema instanceof z.ZodNumber) {
    const checks = schema._def.checks.map(check => check.kind === 'min' ? `Minimum: ${check.value}.`
      : check.kind === 'max' ? `Maximum: ${check.value}.` : '').filter(Boolean).join(' ')
    return { type: 'number', ...(checks ? { description: checks } : {}) }
  }
  throw new Error(`synthesis_schema_projection_unsupported:${schema._def.typeName}`)
}

export const synthesisCandidateToolSchema = project(researchSynthesisCandidateSchema)

/** Intern repeated generated shapes into local JSON-schema definitions. This
 * preserves the bounded AST depth while avoiding exponential prompt copies.
 * It is a visible schema, never a substitute for the canonical Zod validator. */
export function decisionSynthesisToolParameters(legacy: ReturnType<typeof parameterSchemaSpecToJsonSchema>): ReturnType<typeof parameterSchemaSpecToJsonSchema> {
  const projected = parameterSchemaSpecToJsonSchema({ candidate: { ...project(researchDecisionSynthesisV1Schema, true), required: true } })
  // These are cross-field semantic rules in the validator, not a different
  // wire contract. Decorate the actual model schema before interning shared
  // condition shapes so the three positions cannot accidentally share enums.
  type ProjectedNode = { properties?: Record<string, ProjectedNode>; items?: ProjectedNode; oneOf?: ProjectedNode[]; enum?: readonly string[]; description?: string }
  const wrapper = projected.properties.candidate as ProjectedNode
  const actions = wrapper.properties?.action_intents?.items?.oneOf
  const actionList = wrapper.properties?.action_intents
  const mechanism = wrapper.properties?.mechanism_reviews?.items
  if (!actions?.length || !mechanism?.properties) throw new Error('synthesis_schema_projection_decision_shape_invalid')
  const reviewTimeGuidance = 'Before the first qualified freeze, next_review_at must be after the actual synthesis submission time and no later than the fixed window expiry; a timestamp that was future at evidence collection may become due during corrections. Do not advance as_of to hide stale inputs. After the single review, resubmit the exact frozen bytes; a due next_review_at alone is not expiry and must not be rewritten.'
  if (actionList) actionList.description = `${actionList.description ?? ''} For EVERY declared research_candidate_v1.horizons horizon, provide separate flat and long branches for the scope (normally six for 30m/24h/72h). Never delete Action branches to bypass an error. Missing evidence remains an explicit branch with its actual gap, not a fabricated fact.`.trim()
  for (const action of actions) {
    const asOf = action.properties?.as_of, reference = action.properties?.mechanism_review_ref
    if (!asOf || !reference) throw new Error('synthesis_schema_projection_action_binding_missing')
    asOf.description = `${asOf.description ?? ''} Actual information cutoff; ALL actions and mechanisms in the wrapper share this same instant. This is not an individual price observed_at or the acquisition/execution deadline. All supporting inputs must already be available.`.trim()
    reference.description = `${reference.description ?? ''} Exact review_id of a supplied mechanism with the same scope_id, horizon and cutoff=action.as_of. Never use none or delete a branch to bypass a consistency error.`.trim()
    const nextReview = action.properties?.next_review_at
    if (!nextReview) throw new Error('synthesis_schema_projection_next_review_missing')
    nextReview.description = `${nextReview.description ?? ''} ${reviewTimeGuidance}`.trim()
    for (const [name, allowed] of [['entry', ['entry']], ['exit', ['exit']], ['invalidation', ['invalidation']]] as const) {
      const purpose = action.properties?.[name]?.items?.properties?.purpose
      if (!purpose) throw new Error('synthesis_schema_projection_condition_shape_invalid')
      purpose.enum = [...allowed]
      purpose.description = `${name} condition purpose must be ${allowed.join(' or ')}. The current frozen Action catalog does not admit risk_upgrade as a purpose; describe that risk in original_text and use purpose exit.`
    }
  }
  const horizonReview = wrapper.properties?.research_candidate_v1?.properties?.horizons?.items?.properties?.next_review_at
  if (!horizonReview) throw new Error('synthesis_schema_projection_horizon_review_missing')
  horizonReview.description = `${horizonReview.description ?? ''} ${reviewTimeGuidance}`.trim()
  const cutoff = mechanism.properties.cutoff
  if (!cutoff) throw new Error('synthesis_schema_projection_mechanism_cutoff_missing')
  cutoff.description = `${cutoff.description ?? ''} Actual information cutoff shared across ALL action.as_of and mechanism.cutoff fields in the wrapper. Market observed_at remains a fact field and deadlines are scheduling limits, not evidence availability.`.trim()
  const evidenceGuidance = 'Exact admitted Evidence IDs from the frozen input or a successful research result, never requirement IDs, metric names, source names, node/edge/review IDs or ClaimSignal IDs. Examples of INVALID IDs: derivatives_crowding, market_regime_context, real_yield_10y. An absent metric is an unresolved question, not evidence; use an empty array when no admitted evidence exists and describe the missing observation in mechanism.unresolved. Do not replace missing refs with a convenient unrelated Evidence ID.'
  const decorateEvidence = (field: ProjectedNode | undefined) => {
    if (!field) throw new Error('synthesis_schema_projection_evidence_shape_invalid')
    field.description = `${field.description ?? ''} ${evidenceGuidance}`.trim()
  }
  for (const action of actions) decorateEvidence(action.properties?.evidence_refs)
  const edges = mechanism.properties.edges?.items?.properties
  decorateEvidence(edges?.support_refs)
  decorateEvidence(edges?.counter_refs)
  decorateEvidence(mechanism.properties.priced_in_refs)
  decorateEvidence(mechanism.properties.alternative_refs)
  const nodeRefs = mechanism.properties.nodes?.items?.properties?.refs
  if (!nodeRefs) throw new Error('synthesis_schema_projection_node_refs_missing')
  nodeRefs.description = `${nodeRefs.description ?? ''} Cite exact admitted Evidence IDs or exact ClaimSignal IDs supplied by this request; a ClaimSignal remains an attributed opinion. Requirement/metric names are not references. Put missing observations in unresolved.`.trim()
  const unresolved = mechanism.properties.unresolved
  if (!unresolved) throw new Error('synthesis_schema_projection_unresolved_missing')
  unresolved.description = `${unresolved.description ?? ''} Describe missing observations and untested alternatives here, including requirement/metric names when useful. These are questions, not authenticated evidence; never copy them into support_refs, counter_refs, alternative_refs or priced_in_refs.`.trim()
  const alternatives = mechanism.properties.alternative_refs
  if (!alternatives) throw new Error('synthesis_schema_projection_mechanism_shape_invalid')
  alternatives.description = `${alternatives.description ?? ''} Admitted independent Evidence IDs supporting alternative explanations; not causal-link IDs, node IDs, edge IDs, review IDs or ClaimSignal IDs. Use an empty array when no admitted evidence supports an alternative and describe the gap in unresolved.`.trim()
  const definitions: Record<string, unknown> = {}
  const visit = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(visit)
    if (value === null || typeof value !== 'object') return value
    const row = Object.fromEntries(Object.entries(value).map(([key, child]) => [key, visit(child)]))
    const serialized = JSON.stringify(row)
    if (serialized.length < 200 || !['type', 'oneOf', 'anyOf', 'allOf'].some(key => key in row)) return row
    const key = `synthesis_${createHash('sha256').update(serialized).digest('hex').slice(0, 20)}`
    if (definitions[key] !== undefined && JSON.stringify(definitions[key]) !== serialized) throw new Error('synthesis_schema_intern_collision')
    definitions[key] = row
    return { $ref: `#/$defs/${key}` }
  }
  const candidate = visit(projected.properties.candidate)
  const original = legacy.properties.candidate
  return { ...legacy, properties: { candidate: { oneOf: [original, candidate],
    description: 'Use exactly the protocol selected by the frozen request.output_schema_ref. Legacy result ref requires the old candidate; research_signal.schema.yaml#/$defs/research_decision_synthesis_v1 requires the COMPLETE new wrapper. No model-selected fallback is accepted.' } },
    $defs: definitions } as ReturnType<typeof parameterSchemaSpecToJsonSchema>
}

export { project as projectCanonicalToolValueSchema }

function schemaObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function schemaChildren(row: Record<string, unknown>, visit: (value: unknown) => unknown): Record<string, unknown> {
  return Object.fromEntries(Object.entries(row).map(([key, value]) => {
    if ((key === 'properties' || key === '$defs') && schemaObject(value)) {
      return [key, Object.fromEntries(Object.entries(value).map(([name, child]) => [name, visit(child)]))]
    }
    if (['items', 'additionalProperties', 'if', 'then', 'else', 'not'].includes(key)) {
      return [key, Array.isArray(value) ? value.map(visit) : visit(value)]
    }
    if (['oneOf', 'anyOf', 'allOf'].includes(key) && Array.isArray(value)) return [key, value.map(visit)]
    return [key, value]
  }))
}

function expandSynthesisReferences(schema: Record<string, unknown>): Record<string, unknown> {
  const definitions = schemaObject(schema.$defs) ? schema.$defs : {}
  const expand = (value: unknown, active = new Set<string>()): unknown => {
    if (!schemaObject(value)) return value
    if (typeof value.$ref === 'string') {
      const name = value.$ref.slice('#/$defs/'.length)
      if (!value.$ref.startsWith('#/$defs/')
        || !Object.hasOwn(definitions, name) || active.has(name)) throw new Error('synthesis_schema_local_ref_invalid')
      const target = expand(definitions[name], new Set([...active, name]))
      if (!schemaObject(target)) throw new Error('synthesis_schema_local_ref_invalid')
      const { $ref: _reference, $defs: _directory, ...siblings } = value
      const extra = schemaChildren(siblings, child => expand(child, active))
      if (!Object.keys(extra).length) return target
      return Object.keys(extra).some(key => Object.hasOwn(target, key)) ? { allOf: [target, extra] } : { ...target, ...extra }
    }
    const { $defs: _directory, ...fields } = value
    return schemaChildren(fields, child => expand(child, active))
  }
  const result = expand(schema)
  if (!schemaObject(result)) throw new Error('synthesis_schema_projection_decision_shape_invalid')
  return result
}

function packSynthesisDefinitions(schema: Record<string, unknown>, definitions: Record<string, unknown>): Record<string, unknown> {
  let live: Record<string, unknown> = {}
  const mark = (value: unknown): unknown => {
    if (!schemaObject(value)) return value
    if (typeof value.$ref === 'string') {
      const name = value.$ref.slice('#/$defs/'.length)
      if (!Object.hasOwn(definitions, name)) throw new Error('synthesis_schema_local_ref_invalid')
      if (!Object.hasOwn(live, name)) { live[name] = definitions[name]; mark(definitions[name]) }
    } else schemaChildren(value, mark)
    return value
  }
  mark(schema)
  let body = schema
  while (true) {
    const counts = new Map<string, number>()
    const count = (value: unknown): unknown => {
      if (!schemaObject(value)) return value
      if (typeof value.$ref === 'string') counts.set(value.$ref, (counts.get(value.$ref) ?? 0) + 1)
      else schemaChildren(value, count)
      return value
    }
    count(body); Object.values(live).forEach(count)
    const once = new Set(Object.keys(live).filter(name => counts.get(`#/$defs/${name}`) === 1))
    if (!once.size) break
    const inline = (value: unknown): unknown => {
      if (!schemaObject(value)) return value
      if (typeof value.$ref === 'string' && once.has(value.$ref.slice('#/$defs/'.length))) {
        return inline(live[value.$ref.slice('#/$defs/'.length)])
      }
      return schemaChildren(value, inline)
    }
    const next = inline(body)
    if (!schemaObject(next)) throw new Error('synthesis_schema_projection_decision_shape_invalid')
    body = next
    live = Object.fromEntries(Object.entries(live).filter(([name]) => !once.has(name)).map(([name, value]) => [name, inline(value)]))
  }
  const names = new Map(Object.keys(live).sort().map((name, index) => [name, index.toString(16)]))
  const rename = (value: unknown): unknown => {
    if (!schemaObject(value)) return value
    const row = schemaChildren(value, rename)
    if (typeof row.$ref === 'string') row.$ref = `#/$defs/${names.get(row.$ref.slice('#/$defs/'.length))!}`
    return row
  }
  const result = rename(body)
  if (!schemaObject(result)) throw new Error('synthesis_schema_projection_decision_shape_invalid')
  return { ...result, $defs: Object.fromEntries(Object.entries(live).map(([name, value]) => [names.get(name)!, rename(value)])) }
}

/** Share schema nodes, keeping property dictionaries and literal data opaque. */
export function compactSharedSynthesisSchema(schema: Record<string, unknown>): Record<string, unknown> {
  const definitions: Record<string, unknown> = {}
  const visit = (value: unknown, reference = true): unknown => {
    if (!schemaObject(value)) return value
    const row = schemaChildren(value, child => visit(child)), serialized = JSON.stringify(row)
    if (!reference || serialized.length < 50) return row
    const key = createHash('sha256').update(serialized).digest('hex').slice(0, 20)
    if (definitions[key] !== undefined && JSON.stringify(definitions[key]) !== serialized) throw new Error('synthesis_schema_intern_collision')
    definitions[key] = row
    return { $ref: `#/$defs/${key}` }
  }
  const result = visit(expandSynthesisReferences(schema), false)
  if (!schemaObject(result)) throw new Error('synthesis_schema_projection_decision_shape_invalid')
  return packSynthesisDefinitions(result, definitions)
}

export function perpetualSynthesisToolParameters(existing: ReturnType<typeof parameterSchemaSpecToJsonSchema>): ReturnType<typeof parameterSchemaSpecToJsonSchema> {
  const archived={...existing,properties:{...existing.properties,candidate:{oneOf:[existing.properties.candidate,perpetualNativeToolSchemas.candidate]},review_request:perpetualNativeToolSchemas.review_request}}
  const event = perpetualNativeToolSchemas.$defs.draft_event_probability_v1
  const definitions = { ...perpetualNativeToolSchemas.$defs, draft_event_probability_v1: { ...event, properties: { ...event.properties,
    outcome_given_occurrence: { ...event.properties.outcome_given_occurrence,
      description: 'outcome_given_occurrence.conditioning_ref must equal event_probability.event_definition_ref, including kind=unavailable with probabilities=null. Use the same local_id or exact existing reference. Unknown probabilities stay null. This separate event-distribution identity does not condition ordinary market direction: keep market_event_conditioned=false when appropriate.',
    },
  } } }
  return compactSharedSynthesisSchema({type:'object',oneOf:[archived,perpetualNativeToolSchemas.prepare,perpetualNativeToolSchemas.finalize],
    description:'Use the frozen request protocol. decision_v2: prepare one authored draft, independent foreground reviewer, then finalize by the returned prepared_call_id. Event plans use perpetual-synthesis-draft.v2 with primary_draft, typed event_plan_draft and event_plan_originals; the server derives their exact plan/comparison refs from the authenticated source call before review. Use local:event: identities only for declared local bodies; server economic refs remain exact. Never put financial plans in assumption text, hash model content or copy a generated packet. Existing full candidate forms are archived compatibility only.',
    $defs:{...((existing as any).$defs??{}),...definitions}}) as unknown as ReturnType<typeof parameterSchemaSpecToJsonSchema>
}
