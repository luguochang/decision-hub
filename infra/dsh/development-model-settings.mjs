// Development metadata only. Source-declared context is not a certified remote maximum.
import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
const require = createRequire(import.meta.url)
const yaml = require('yaml')
if (require('yaml/package.json').version !== '2.9.0') throw new Error('development_capacity_yaml_version_changed')
const sha = raw => createHash('sha256').update(raw).digest('hex')
const fail = code => { throw new Error(`development_capacity_${code}`) }
function normalized(value) {
  if (Array.isArray(value)) return value.map(normalized)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, normalized(value[key])]))
  return value
}
function parse(raw) {
  if (Buffer.byteLength(raw) > 65536) fail('settings_oversize')
  const document = yaml.parseDocument(raw, { uniqueKeys: true })
  if (document.errors.length) fail('settings_invalid')
  try { return document.toJS({ maxAliasCount: 0 }) } catch { fail('settings_invalid') }
}
export function declaredDevelopmentCapacity(raw, identity) {
  if (identity.provider !== 'codexai' || identity.api !== 'openai-responses') fail('route_mismatch')
  const profile = parse(raw)?.['llm-pi-ai']?.providers?.[identity.provider]
  const expectedHash = identity.baseURLSha256 ?? sha(identity.baseURL ?? '')
  if (!/^[a-f0-9]{64}$/.test(expectedHash) || !profile || profile.api !== identity.api ||
    typeof profile.baseURL !== 'string' || sha(profile.baseURL) !== expectedHash || !Array.isArray(profile.models)) fail('route_mismatch')
  const models = profile.models.filter(entry => entry?.id === identity.model)
  if (models.length !== 1) fail('model_not_unique')
  const contextWindow = models[0].contextWindow
  // The development template reserves 8192 output tokens; retain positive input room.
  if (!Number.isSafeInteger(contextWindow) || contextWindow <= 8192) fail('declaration_invalid')
  return { contextWindow, provenance: 'source_declared_not_verified_remote_maximum' }
}
export function prepareDevelopmentCapacity(sourceRaw, targetRaw, identity) {
  const declared = declaredDevelopmentCapacity(sourceRaw, identity)
  // Only the launcher's JSON template is migratable. Unknown hand-edited YAML is read-only.
  let target
  try { target = JSON.parse(targetRaw) } catch { fail('target_not_generated_template') }
  const profile = target?.['llm-pi-ai']?.providers?.codexai
  const model = profile?.models?.[0]
  const current = model?.contextWindow
  const expected = { 'llm-pi-ai': { providers: { codexai: {
    displayName: 'Development model', api: 'openai-responses', baseURL: profile?.baseURL,
    apiKeyEnv: 'OPENAI_API_KEY', reasoning: 'medium', models: [{ id: identity.model, name: identity.model,
      contextWindow: current, maxTokens: 8192, input: ['text', 'image'], reasoningEfforts: {
        low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: 'max' } }],
  } } }, 'agent-default-model': { provider: 'codexai', model: identity.model } }
  if ((current !== 128000 && current !== declared.contextWindow) ||
    JSON.stringify(normalized(target)) !== JSON.stringify(normalized(expected))) fail('target_not_generated_template')
  declaredDevelopmentCapacity(targetRaw, identity)
  const scalar = /("contextWindow"\s*:\s*)(0|[1-9]\d*)(?=\s*[,}])/g
  const occurrences = [...targetRaw.matchAll(scalar)]
  if (occurrences.length !== 1) fail('target_not_generated_template')
  const text = targetRaw.replace(scalar, (_, prefix) => prefix + declared.contextWindow)
  const after = JSON.parse(text)
  target['llm-pi-ai'].providers.codexai.models[0].contextWindow = declared.contextWindow
  if (JSON.stringify(after) !== JSON.stringify(target)) fail('target_scalar_change_invalid')
  return { text, sourceSha256: sha(sourceRaw), targetSha256: sha(targetRaw), ...declared, changed: text !== targetRaw }
}
export function readDevelopmentPlainFile(path) {
  const full = resolve(path)
  let cursor = full
  for (;;) {
    if (lstatSync(cursor).isSymbolicLink()) fail('path_linked')
    if (dirname(cursor) === cursor) break
    cursor = dirname(cursor)
  }
  if (!lstatSync(full).isFile()) fail('path_not_regular')
  return readFileSync(full, 'utf8')
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2)
    if (args.length === 1 && args[0] === '--prepare') {
      const request = JSON.parse(readFileSync(0, 'utf8'))
      if (resolve(request.source).toLowerCase() === resolve(request.target).toLowerCase()) fail('source_target_same')
      const from = readDevelopmentPlainFile(request.source)
      const target = readDevelopmentPlainFile(request.target)
      if (sha(from) !== request.sourceSha256?.toLowerCase() || sha(target) !== request.targetSha256?.toLowerCase()) fail('preimage_changed')
      const settings = parse(target)
      const profile = settings?.['llm-pi-ai']?.providers?.codexai
      const model = settings?.['agent-default-model']?.model
      const prepared = prepareDevelopmentCapacity(from, target, { provider: 'codexai', api: 'openai-responses', baseURLSha256: sha(profile?.baseURL ?? ''), model })
      const output = resolve(request.output)
      if (dirname(output) !== dirname(resolve(request.target)) || !/^\.capacity-[a-f0-9]{32}\.tmp$/.test(basename(output))) fail('output_path_invalid')
      readDevelopmentPlainFile(output)
      writeFileSync(output, prepared.text, 'utf8')
      const { text, ...safe } = prepared
      console.log(JSON.stringify({ ...safe, outputSha256: sha(text) }))
    } else {
    if (args.length !== 6 || args[0] !== '--source' || args[2] !== '--model' || args[4] !== '--base-url-sha256') fail('arguments_invalid')
    console.log(JSON.stringify(declaredDevelopmentCapacity(readDevelopmentPlainFile(args[1]), {
      provider: 'codexai', api: 'openai-responses', model: args[3], baseURLSha256: args[5],
    })))
    }
  } catch (error) {
    console.error(JSON.stringify({ status: 'failed', code: /^development_capacity_[a-z_]+$/.test(error.message) ? error.message : 'development_capacity_lookup_failed' }))
    process.exitCode = 1
  }
}
