// Product-owned DSH preload. Other processes and unmatched fetches are untouched.
import { createHash, randomUUID } from 'node:crypto'
import { AsyncLocalStorage } from 'node:async_hooks'
import { appendFileSync, existsSync, lstatSync, readFileSync, statSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'

const SAFE_CODES = new Set(['UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT',
  'UND_ERR_BODY_TIMEOUT', 'UND_ERR_SOCKET', 'ENOTFOUND', 'EAI_AGAIN',
  'ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ERR_TLS_CERT_ALTNAME_INVALID',
  'CERT_HAS_EXPIRED', 'SELF_SIGNED_CERT_IN_CHAIN', 'DEPTH_ZERO_SELF_SIGNED_CERT',
  'DSH_PROVIDER_REDIRECT_REFUSED'])
const SAFE_TYPES = new Set(['Error', 'TypeError', 'AbortError', 'TimeoutError',
  'ConnectTimeoutError', 'HeadersTimeoutError', 'BodyTimeoutError', 'SocketError'])
const PROVIDER_STAGES = new Set(['receipt_started', 'receipt_completed', 'delegate_started', 'first_sdk_chunk',
  'stream_finished', 'stream_failed'])
const PROVIDER_STAGE_REQUIRED_FIELDS = ['schema_version', 'at', 'stage', 'run_hash', 'dispatch_id',
  'input_hash', 'generation', 'execution_session_hash']
const PROVIDER_STAGE_BYTE_FIELDS = ['messages_bytes', 'system_bytes', 'tools_bytes', 'input_bytes', 'finish_bytes']
const PROVIDER_STAGE_FIELDS = new Set([...PROVIDER_STAGE_REQUIRED_FIELDS, ...PROVIDER_STAGE_BYTE_FIELDS])
export const PROVIDER_STAGE_PUBLISHER_SYMBOL = Symbol.for('decision-hub.provider-stage-diagnostic.publisher.v1')
export const PROVIDER_DISPATCH_CONTEXT_RUNNER_SYMBOL = Symbol.for('decision-hub.provider-dispatch-context.runner.v1')
const PROVIDER_DISPATCH_FIELDS = new Set(['run_hash', 'dispatch_id', 'input_hash', 'generation', 'execution_session_hash'])
const providerDispatchStorage = new AsyncLocalStorage()
const hash = value => createHash('sha256').update(value).digest('hex')
const loopback = hostname => ['localhost', '127.0.0.1', '[::1]'].includes(hostname.toLowerCase())

export function safeTransportCause(error) {
  const chain = []
  let current = error
  for (let depth = 0; current && typeof current === 'object' && depth < 4; depth++) {
    const type = SAFE_TYPES.has(current.name) ? current.name : 'unknown'
    const code = SAFE_CODES.has(current.code) ? current.code : null
    chain.push({ type, code })
    current = current.cause
  }
  return chain
}

export function providerRoute(profile) {
  // DSH settings use baseURL. pi-ai's derived Model.baseUrl is a different DTO.
  if (!profile || profile.api !== 'openai-responses' || typeof profile.baseURL !== 'string'
    || 'baseUrl' in profile) {
    throw new Error('dsh_provider_transport_profile_invalid')
  }
  const base = new URL(profile.baseURL)
  if (base.protocol !== 'https:' || loopback(base.hostname) || base.username || base.password || base.search || base.hash) {
    throw new Error('dsh_provider_transport_profile_invalid')
  }
  return { origin: base.origin, pathname: `${base.pathname.replace(/\/$/, '')}/responses`,
    route_hash: hash(`${base.origin}${base.pathname.replace(/\/$/, '')}/responses`) }
}

export function validateProxy(raw) {
  if (!raw) return null
  const proxy = new URL(raw)
  if (!['http:', 'https:'].includes(proxy.protocol) || proxy.username || proxy.password
    || proxy.search || proxy.hash || !['', '/'].includes(proxy.pathname)) {
    throw new Error('dsh_provider_transport_proxy_invalid')
  }
  return proxy.href
}

export function windowsStaticProxy(server, bypass, hostname) {
  if (!server) return null
  const exclusions = (bypass ?? '').split(';').map(value => value.trim()).filter(Boolean)
  if (exclusions.some(value => value === '<local>' ? !hostname.includes('.')
    : new RegExp(`^${value.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*')}$`, 'i').test(hostname))) return null
  const parts = server.split(';')
  const value = server.includes('=')
    ? parts.find(part => /^https=/i.test(part.trim()))?.trim().slice(6)
    : server.trim()
  if (!value) return null // A protocol-specific HTTP-only proxy leaves HTTPS direct.
  return validateProxy(value.includes('://') ? value : `http://${value}`)
}

export function createProviderFetch(route, originalFetch, dispatcher, diagnostic, proxySource = 'direct') {
  return async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input)
    if (loopback(url.hostname) || url.origin !== route.origin || url.pathname !== route.pathname) {
      return originalFetch(input, init)
    }
    const started = performance.now()
    const transportRequestId = randomUUID()
    const dispatchContext = providerDispatchStorage.getStore()
    const report = detail => {
      try { diagnostic({ schema_version: 'model-transport-diagnostic.v1', at: new Date().toISOString(),
        transport_request_id: transportRequestId, route_hash: route.route_hash, proxy_source: proxySource,
        elapsed_ms: Math.round(performance.now() - started), ...(dispatchContext ?? {}), ...detail }) } catch { /* Diagnostics never erase the request result. */ }
    }
    // These phase markers contain no URL, headers, body, prompt or response
    // bytes. They distinguish time spent waiting for response headers from a
    // Provider body/SDK stall while preserving the original fetch semantics.
    report({ stage: 'request_started' })
    try {
      const response = await originalFetch(input, { ...init, redirect: 'error',
        ...(dispatcher ? { dispatcher } : {}) })
      report({ stage: 'headers_received', result: 'http', status: response.status })
      if (response.status >= 300 && response.status < 400) {
        const error = new Error('dsh_provider_redirect_refused')
        error.code = 'DSH_PROVIDER_REDIRECT_REFUSED'
        throw error
      }
      return response
    } catch (error) {
      report({ stage: 'request_failed', result: 'error', cause: safeTransportCause(error) })
      throw error
    }
  }
}

export function boundedDiagnostic(path) {
  let count = 0
  return value => {
    if (++count > 1000 || !path) return
    if (existsSync(path) && (lstatSync(path).isSymbolicLink() || statSync(path).size > 1_048_576)) return
    appendFileSync(path, JSON.stringify(value) + '\n', { encoding: 'utf8', mode: 0o600 })
  }
}

export function createProviderStagePublisher(diagnostic) {
  const publisher = value => {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype
      || Object.keys(value).some(key => !PROVIDER_STAGE_FIELDS.has(key))
      || PROVIDER_STAGE_REQUIRED_FIELDS.some(key => !Object.hasOwn(value, key))
      || value.schema_version !== 'dsh-provider-stage-diagnostic.v1'
      || typeof value.at !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value.at)
      || !Number.isFinite(Date.parse(value.at))
      || new Date(Date.parse(value.at)).toISOString() !== value.at
      || !PROVIDER_STAGES.has(value.stage)
      || !/^[a-f0-9]{64}$/.test(value.run_hash)
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.dispatch_id)
      || !/^[a-f0-9]{64}$/.test(value.input_hash)
      || !Number.isSafeInteger(value.generation) || value.generation < 1
      || !/^[a-f0-9]{64}$/.test(value.execution_session_hash)
      || PROVIDER_STAGE_BYTE_FIELDS.some(key => Object.hasOwn(value, key)
        && (!Number.isSafeInteger(value[key]) || value[key] < 0))) {
      throw new Error('dsh_provider_stage_diagnostic_invalid')
    }
    const safe = Object.fromEntries(Object.entries(value).filter(([key]) => PROVIDER_STAGE_FIELDS.has(key)))
    diagnostic(Object.freeze(safe))
  }
  return Object.freeze(publisher)
}

function providerDispatchContext(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype
    || Object.keys(value).length !== PROVIDER_DISPATCH_FIELDS.size
    || Object.keys(value).some(key => !PROVIDER_DISPATCH_FIELDS.has(key))
    || !/^[a-f0-9]{64}$/.test(value.run_hash)
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.dispatch_id)
    || !/^[a-f0-9]{64}$/.test(value.input_hash)
    || !Number.isSafeInteger(value.generation) || value.generation < 1
    || !/^[a-f0-9]{64}$/.test(value.execution_session_hash)) {
    throw new Error('dsh_provider_dispatch_context_invalid')
  }
  return Object.freeze(Object.fromEntries(Object.entries(value)))
}

function createProviderDispatchRunner() {
  return Object.freeze((context, operation) => {
    if (typeof operation !== 'function') throw new Error('dsh_provider_dispatch_operation_invalid')
    return providerDispatchStorage.run(providerDispatchContext(context), operation)
  })
}

function installProviderDiagnosticGlobals(diagnostic) {
  if (Object.getOwnPropertyDescriptor(globalThis, PROVIDER_STAGE_PUBLISHER_SYMBOL)
    || Object.getOwnPropertyDescriptor(globalThis, PROVIDER_DISPATCH_CONTEXT_RUNNER_SYMBOL)) {
    throw new Error('dsh_provider_diagnostic_global_conflict')
  }
  const publisher = createProviderStagePublisher(diagnostic)
  Object.defineProperty(globalThis, PROVIDER_STAGE_PUBLISHER_SYMBOL, {
    value: publisher, configurable: false, enumerable: false, writable: false,
  })
  Object.defineProperty(globalThis, PROVIDER_DISPATCH_CONTEXT_RUNNER_SYMBOL, {
    value: createProviderDispatchRunner(), configurable: false, enumerable: false, writable: false,
  })
}

export function bootstrapProviderTransport(env = process.env) {
  const require = createRequire(import.meta.url)
  const major = Number(process.versions.node.split('.')[0])
  if (major !== 24 || Number(process.versions.node.split('.')[1]) < 18) {
    throw new Error('dsh_provider_transport_node_unsupported')
  }
  const settingsPath = resolve(env.DSH_HOME ?? '', 'settings.yaml')
  const raw = readFileSync(settingsPath, 'utf8')
  if (Buffer.byteLength(raw) > 65_536) throw new Error('dsh_provider_transport_settings_oversize')
  const { parseDocument } = require('yaml')
  if (require('yaml/package.json').version !== '2.9.0') throw new Error('dsh_provider_transport_yaml_changed')
  const document = parseDocument(raw, { uniqueKeys: true })
  if (document.errors.length) throw new Error('dsh_provider_transport_settings_invalid')
  const profile = document.toJS()?.['llm-pi-ai']?.providers?.codexai
  const route = providerRoute(profile)
  const choices = [
    ['model_env', env.DECISION_HUB_MODEL_HTTP_PROXY],
    ['public_env', env.DECISION_HUB_PUBLIC_HTTP_PROXY],
  ]
  const windowsProxy = !choices.some(([, value]) => value) && env.DECISION_HUB_MODEL_OS_PROXY_ENABLE === '1'
    ? windowsStaticProxy(env.DECISION_HUB_MODEL_OS_PROXY_SERVER, env.DECISION_HUB_MODEL_OS_PROXY_BYPASS,
      new URL(route.origin).hostname) : null
  const [source, rawProxy] = choices.find(([, value]) => value)
    ?? (windowsProxy ? ['windows_static', windowsProxy] : ['direct', null])
  const proxy = validateProxy(rawProxy)
  const diagnostic = boundedDiagnostic(env.DECISION_HUB_MODEL_TRANSPORT_DIAGNOSTICS)
  let dispatcher
  let dependency = null
  if (proxy) {
    const upstream = env.DECISION_HUB_MODEL_TRANSPORT_UPSTREAM_ROOT
    if (!upstream) throw new Error('dsh_provider_transport_dependency_missing')
    const dependencyRoot = resolve(upstream, 'node_modules/.pnpm/undici@7.28.0/node_modules/undici')
    const upstreamLock = readFileSync(resolve(upstream, 'pnpm-lock.yaml'))
    if (upstreamLock.byteLength > 4_194_304) throw new Error('dsh_provider_transport_dependency_lock_oversize')
    const declaredIntegrity = 'sha512-cRZYrTDwWznlnRiPjggAGxZXanty6M8RV1ff8Wm4LWXBp7/IG8v5DnOm74DtUBp9OONpK75YlPnIjQqX0dBDtA=='
    if (!upstreamLock.toString('utf8').includes(`  undici@7.28.0:\n    resolution: {integrity: ${declaredIntegrity}}`)
      && !upstreamLock.toString('utf8').includes(`  undici@7.28.0:\r\n    resolution: {integrity: ${declaredIntegrity}}`)) {
      throw new Error('dsh_provider_transport_dependency_lock_changed')
    }
    if (require(resolve(dependencyRoot, 'package.json')).version !== '7.28.0') {
      throw new Error('dsh_provider_transport_dependency_changed')
    }
    const entry = require.resolve(dependencyRoot)
    const { ProxyAgent } = require(entry)
    dependency = { version: '7.28.0', declared_integrity: declaredIntegrity,
      actual_lock_sha256: hash(upstreamLock), actual_entry_sha256: hash(readFileSync(entry)),
      verification_scope: 'lock_declaration_and_loaded_entry_not_full_package_tarball' }
    dispatcher = new ProxyAgent({ uri: proxy, requestTls: { rejectUnauthorized: true },
      proxyTls: { rejectUnauthorized: true } })
  }
  installProviderDiagnosticGlobals(diagnostic)
  diagnostic({ schema_version: 'model-transport-policy.v1', at: new Date().toISOString(),
    node: process.versions.node, yaml_version: '2.9.0', provider: 'codexai', api: profile.api,
    route_hash: route.route_hash, proxy_source: source, proxy_configured: proxy !== null,
    profile_timeout_ms: profile.timeoutMs ?? null, sdk_default_timeout_ms: 600_000,
    windows_proxy_read: env.DECISION_HUB_MODEL_OS_PROXY_READ ?? 'not_requested',
    dependency, redirect: 'error', tls_verification: true, connectivity: 'not_tested' })
  globalThis.fetch = createProviderFetch(route, globalThis.fetch, dispatcher, diagnostic, source)
  return { route_hash: route.route_hash, proxy_source: source }
}

if (process.env.DECISION_HUB_MODEL_TRANSPORT_ENABLE === '1') {
  try { bootstrapProviderTransport() }
  catch {
    boundedDiagnostic(process.env.DECISION_HUB_MODEL_TRANSPORT_DIAGNOSTICS)({
      schema_version: 'model-transport-policy.v1', at: new Date().toISOString(),
      status: 'transport_config_failed', connectivity: 'not_tested',
    })
    throw new Error('transport_config_failed')
  }
}
