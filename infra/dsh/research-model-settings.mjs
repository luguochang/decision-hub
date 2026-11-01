// Narrow startup selection for the existing private codexai route.
// Local registration is not proof of remote account/model availability.
import { createHash, randomUUID } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const yaml = require('yaml')
if (require('yaml/package.json').version !== '2.9.0') throw new Error('research_model_yaml_version_changed')
const sha = raw => createHash('sha256').update(raw).digest('hex')
const validModel = model => typeof model === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/.test(model)
const routePath = ['llm-pi-ai', 'providers', 'codexai']

export function prepareResearchModelSettings(raw, model) {
  if (!validModel(model)) throw new Error('research_model_id_invalid')
  if (model === 'gpt-5.5') throw new Error('research_model_retired')
  if (Buffer.byteLength(raw) > 65_536) throw new Error('research_model_settings_oversize')
  const document = yaml.parseDocument(raw, { uniqueKeys: true })
  if (document.errors.length) throw new Error('research_model_settings_invalid')
  const before = document.toJS()
  const profile = before?.['llm-pi-ai']?.providers?.codexai
  if (!profile || profile.api !== 'openai-responses' || typeof profile.baseURL !== 'string'
    || !Array.isArray(profile.models) || profile.models.some(entry => !entry || typeof entry.id !== 'string')
    || new Set(profile.models.map(entry => entry.id)).size !== profile.models.length) {
    throw new Error('research_model_existing_route_invalid')
  }
  let selected = profile.models.find(entry => entry.id === model)
  if (!selected) {
    if (model !== 'gpt-6.1-sol') throw new Error('research_model_not_registered')
    const retired = profile.models.find(entry => entry.id === 'gpt-5.5')
    // These are existing deployment limits, not a claim about the model's or
    // account route's maximum capability. Task budgets stay separate.
    const maxTokens = retired?.maxTokens ?? 8192
    const contextWindow = retired?.contextWindow ?? 128000
    if (!Number.isSafeInteger(maxTokens) || maxTokens <= 0 || maxTokens > 128_000) {
      throw new Error('research_model_existing_output_cap_invalid')
    }
    if (!Number.isSafeInteger(contextWindow) || contextWindow <= 0) throw new Error('research_model_existing_context_limit_invalid')
    selected = { id: model, name: 'GPT-6.1 Sol', contextWindow, maxTokens,
      input: ['text', 'image'], reasoningEfforts: {
        low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: 'max',
      } }
  }
  const models = profile.models.filter(entry => entry.id !== 'gpt-5.5')
  if (!models.some(entry => entry.id === model)) models.push(selected)
  document.setIn([...routePath, 'models'], models)
  if (model === 'gpt-6.1-sol' && !['low', 'medium', 'high', 'xhigh', 'max'].includes(profile.reasoning)) {
    document.setIn([...routePath, 'reasoning'], 'medium')
  }
  document.setIn(['agent-default-model', 'provider'], 'codexai')
  document.setIn(['agent-default-model', 'model'], model)
  if (model === 'gpt-6.1-sol' && Object.hasOwn(before['agent-default-model'] ?? {}, 'reasoningEffort')
    && !['low', 'medium', 'high', 'xhigh', 'max'].includes(before['agent-default-model'].reasoningEffort)) {
    document.setIn(['agent-default-model', 'reasoningEffort'], 'medium')
  }
  const after = document.toJS()
  // Refuse any accidental change beyond the model catalog/default/compatible
  // reasoning selection; route, credentials, retry policy and unrelated
  // settings retain their existing semantic value.
  const allowed = structuredClone(after)
  allowed['llm-pi-ai'].providers.codexai.models = before['llm-pi-ai'].providers.codexai.models
  if (Object.hasOwn(before['llm-pi-ai'].providers.codexai, 'reasoning')) {
    allowed['llm-pi-ai'].providers.codexai.reasoning = before['llm-pi-ai'].providers.codexai.reasoning
  } else delete allowed['llm-pi-ai'].providers.codexai.reasoning
  if (Object.hasOwn(before, 'agent-default-model')) allowed['agent-default-model'] = before['agent-default-model']
  else delete allowed['agent-default-model']
  if (JSON.stringify(allowed) !== JSON.stringify(before)) throw new Error('research_model_unrelated_settings_changed')
  const text = JSON.stringify(after) === JSON.stringify(before) ? raw : document.toString()
  return { text, sourceSha256: sha(raw), safe: { schema_version: 'research-model-selection.v1',
    provider: 'codexai', model, registered_locally: true,
    remote_availability: 'not_tested', changed: text !== raw } }
}

export function prepareResearchModelEnv(raw, model) {
  if (!validModel(model) || model === 'gpt-5.5') throw new Error('research_model_id_invalid')
  if (Buffer.byteLength(raw) > 65_536) throw new Error('research_model_env_oversize')
  const keys = []
  const text = raw.replace(/^(\s*(DECISION_HUB_MODEL|DECISION_HUB_SEARCH_MODEL)\s*=)([^\r\n]*)/gm,
    (line, prefix, key, value) => {
      const selected = value.trim()
      if (!/^(?:gpt-5\.5|"gpt-5\.5"|'gpt-5\.5')$/.test(selected)) return line
      keys.push(key)
      const quote = selected.startsWith('"') ? '"' : selected.startsWith("'") ? "'" : ''
      return prefix + quote + model + quote
    })
  return { text, sourceSha256: sha(raw), safe: { schema_version: 'research-model-env-selection.v1',
    model, changed: text !== raw, changed_keys: keys, credential_values_printed: false } }
}

export function commitPreparedResearchModelSettings(path, prepared) {
  const source = resolve(path)
  if (lstatSync(source).isSymbolicLink() || !lstatSync(source).isFile()) throw new Error('research_model_settings_not_regular')
  if (sha(readFileSync(source)) !== prepared.sourceSha256) throw new Error('research_model_settings_changed')
  if (!prepared.safe.changed) return prepared.safe
  const temporary = resolve(dirname(source), `.research-model-${randomUUID()}.tmp`)
  try {
    writeFileSync(temporary, prepared.text, { encoding: 'utf8', flag: 'wx', mode: lstatSync(source).mode & 0o777 })
    if (lstatSync(source).isSymbolicLink() || sha(readFileSync(source)) !== prepared.sourceSha256) {
      throw new Error('research_model_settings_changed')
    }
    renameSync(temporary, source)
  } finally { if (existsSync(temporary)) unlinkSync(temporary) }
  return prepared.safe
}

export function selectResearchModelSettings(path, model, apply = false) {
  if (lstatSync(path).isSymbolicLink() || !lstatSync(path).isFile()) throw new Error('research_model_settings_not_regular')
  const prepared = prepareResearchModelSettings(readFileSync(path, 'utf8'), model)
  return apply ? commitPreparedResearchModelSettings(path, prepared) : prepared.safe
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2)
    if ((args.length !== 4 && args.length !== 5 && args.length !== 6 && args.length !== 7)
      || args[0] !== '--settings' || args[2] !== '--model') throw new Error('research_model_arguments_invalid')
    const withEnv = args[4] === '--env'
    const apply = args[withEnv ? 6 : 4] === '--apply'
    if (args.length !== 4 + (withEnv ? 2 : 0) + (apply ? 1 : 0)) throw new Error('research_model_arguments_invalid')
    const settingsPath = resolve(args[1])
    if (lstatSync(settingsPath).isSymbolicLink() || !lstatSync(settingsPath).isFile()) throw new Error('research_model_settings_not_regular')
    const settings = prepareResearchModelSettings(readFileSync(settingsPath, 'utf8'), args[3])
    let envPath, env
    if (withEnv) {
      envPath = resolve(args[5])
      if (dirname(envPath) !== dirname(settingsPath) || envPath === settingsPath
        || lstatSync(envPath).isSymbolicLink() || !lstatSync(envPath).isFile()) throw new Error('research_model_env_not_regular')
      env = prepareResearchModelEnv(readFileSync(envPath, 'utf8'), args[3])
    }
    if (apply) {
      commitPreparedResearchModelSettings(settingsPath, settings)
      if (env) commitPreparedResearchModelSettings(envPath, env)
    }
    console.log(JSON.stringify({ ...settings.safe, configuration_limits: {
      context_window: yaml.parse(settings.text)['llm-pi-ai'].providers.codexai.models.find(entry => entry.id === args[3]).contextWindow,
      per_request_max_tokens: yaml.parse(settings.text)['llm-pi-ai'].providers.codexai.models.find(entry => entry.id === args[3]).maxTokens,
      scope: 'configured_limits_not_certified_remote_capability',
    }, ...(env ? { env: env.safe } : {}) }))
  } catch (error) {
    const code = error instanceof Error && /^research_model_[a-z_]+$/.test(error.message)
      ? error.message : 'research_model_selection_failed'
    console.error(JSON.stringify({ schema_version: 'research-model-selection.v1', status: 'failed', code }))
    process.exitCode = 1
  }
}
