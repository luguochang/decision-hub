import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { jsonSchemaToZod } from 'json-schema-to-zod'
import YAML from 'yaml'

const toolDir = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(toolDir, '..', '..')
const output = process.argv[2] ?? path.join(root, 'packages/contracts_ts/src/generated/r2.ts')

const sources = [
  ['contracts/schemas/dsh_host_bridge.schema.yaml', null],
  ['contracts/schemas/agentic_research.schema.yaml', null],
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

const blocks = []
for (const [relativePath, selected] of sources) {
  const sourcePath = path.join(root, relativePath)
  const schema = loadSchema(sourcePath)
  for (const key of Object.keys(schema.$defs ?? {}).sort()) {
    if (selected && !selected.has(key)) continue
    const [schemaName, typeName] = names[key] ?? [`${camel(key)}Schema`, pascal(key)]
    blocks.push(jsonSchemaToZod(resolveRefs(schema.$defs[key], schema, sourcePath), {
      module: 'esm',
      name: schemaName,
      type: typeName,
      noImport: true,
      zodVersion: 3,
    }).trim())
  }
}

fs.mkdirSync(path.dirname(output), { recursive: true })
fs.writeFileSync(
  output,
  `// Generated from contracts/schemas/*.schema.yaml. Do not edit.\nimport { z } from 'zod'\n\n${blocks.join('\n\n')}\n`,
)
