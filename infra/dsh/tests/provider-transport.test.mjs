import { strict as assert } from 'node:assert'
import { test } from 'node:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { bootstrapProviderTransport, createProviderFetch, PROVIDER_DISPATCH_CONTEXT_RUNNER_SYMBOL,
  PROVIDER_STAGE_PUBLISHER_SYMBOL, providerRoute, safeTransportCause,
  validateProxy, windowsStaticProxy } from '../provider-transport.mjs'

const route = providerRoute({ api: 'openai-responses', baseURL: 'https://model.test/v1' })

test('raw DSH profile uses canonical baseURL and refuses the derived Model DTO alias', () => {
  const profile = { displayName: 'fixture', apiKeyEnv: 'FIXTURE_API_KEY', api: 'openai-responses',
    baseURL: 'https://model.test/v1', reasoning: 'high', retryPolicy: {}, models: [] }
  assert.deepEqual(providerRoute(profile), route)
  assert.throws(() => providerRoute({ api: profile.api }), /profile_invalid/)
  assert.throws(() => providerRoute({ api: profile.api, baseUrl: profile.baseURL }), /profile_invalid/)
  assert.throws(() => providerRoute({ ...profile, baseUrl: 'https://other.test/v1' }), /profile_invalid/)
})

test('only exact provider origin and Responses pathname receives proxy; caller signal survives', async () => {
  const calls = [], records = [], dispatcher = {}, signal = new AbortController().signal
  const wrapped = createProviderFetch(route, async (...args) => { calls.push(args); return new Response('{}') },
    dispatcher, value => records.push(value), 'windows_static')
  await wrapped('https://model.test/v1/responses', { signal, method: 'POST', body: 'private prompt' })
  assert.equal(calls[0][1].dispatcher, dispatcher)
  assert.equal(calls[0][1].signal, signal)
  assert.equal(calls[0][1].redirect, 'error')
  for (const url of ['https://other.test/v1/responses', 'http://127.0.0.1:8047/v1/responses',
    'https://model.test/v1/models', 'https://model.test:8443/v1/responses']) {
    const init = { method: 'GET' }
    await wrapped(url, init)
    assert.equal(calls.at(-1)[1], init)
  }
  assert.equal(records.length, 2)
  assert.deepEqual(records.map(record => record.stage), ['request_started', 'headers_received'])
  assert.match(records[0].transport_request_id, /^[0-9a-f-]{36}$/)
  assert.equal(records[1].transport_request_id, records[0].transport_request_id)
  assert.equal(records[0].proxy_source, 'windows_static')
  assert.equal(records[1].status, 200)
  assert.ok(!JSON.stringify(records).includes('private prompt'))
})

test('provider redirects fail and never become another origin request', async () => {
  const calls = [], logs = []
  const wrapped = createProviderFetch(route, async (...args) => { calls.push(args); return new Response(null,
    { status: 302, headers: { location: 'https://other.test/?token=private' } }) }, {}, x => logs.push(x))
  await assert.rejects(() => wrapped('https://model.test/v1/responses'), { code: 'DSH_PROVIDER_REDIRECT_REFUSED' })
  assert.equal(calls.length, 1)
  assert.equal(calls[0][1].redirect, 'error')
  assert.ok(!JSON.stringify(logs).includes('private'))
})

test('original nested failure is preserved to SDK but diagnostics copy only allowed cause codes', async () => {
  const error = new TypeError('fetch https://model.test?key=private', { cause:
    Object.assign(new Error('token=private'), { code: 'UND_ERR_CONNECT_TIMEOUT', cause:
      Object.assign(new Error('prompt=private'), { code: 'SECRET_PRIVATE' }) }) })
  const logs = []
  const wrapped = createProviderFetch(route, async () => { throw error }, {}, x => logs.push(x))
  await assert.rejects(() => wrapped('https://model.test/v1/responses'), e => e === error)
  assert.equal(logs[0].stage, 'request_started')
  assert.equal(logs[1].stage, 'request_failed')
  assert.equal(logs[1].transport_request_id, logs[0].transport_request_id)
  assert.equal(logs[1].cause[1].code, 'UND_ERR_CONNECT_TIMEOUT')
  assert.equal(logs[1].cause[2].code, null)
  assert.ok(!JSON.stringify(logs).includes('private'))
  assert.equal(safeTransportCause('private').length, 0)
})

test('direct policy adds no dispatcher; diagnostic writer failure never erases result', async () => {
  let init
  const wrapped = createProviderFetch(route, async (_input, options) => { init = options; return new Response('{}') },
    null, () => { throw new Error('fixture storage failure') })
  assert.equal((await wrapped('https://model.test/v1/responses')).status, 200)
  assert.equal(init.dispatcher, undefined)
  assert.equal(init.redirect, 'error')
})

test('fixed profile refuses HTTP, loopback, credentials, query and unsupported provider API', () => {
  for (const baseURL of ['http://model.test/v1', 'https://127.0.0.1/v1',
    'https://user:secret@model.test/v1', 'https://model.test/v1?key=private']) {
    assert.throws(() => providerRoute({ api: 'openai-responses', baseURL }), /profile_invalid/)
  }
  assert.throws(() => providerRoute({ api: 'anthropic-messages', baseURL: 'https://model.test' }), /profile_invalid/)
  assert.throws(() => validateProxy('http://user:secret@127.0.0.1:7892'), /proxy_invalid/)
})

test('Windows static HTTPS rules, bypass and HTTP-only configurations are honored', () => {
  assert.equal(windowsStaticProxy('http=127.0.0.1:7891;https=127.0.0.1:7892', '', 'model.test'), 'http://127.0.0.1:7892/')
  assert.equal(windowsStaticProxy('http=127.0.0.1:7891', '', 'model.test'), null)
  assert.equal(windowsStaticProxy('127.0.0.1:7892', '*.test;<local>', 'model.test'), null)
  assert.equal(windowsStaticProxy('127.0.0.1:7892', '<local>', 'intranet'), null)
  assert.equal(windowsStaticProxy('127.0.0.1:7892', '<local>', 'model.example'), 'http://127.0.0.1:7892/')
})

test('actual bootstrap shares one immutable safe sink across lifecycle and concurrent dispatch transport', async () => {
  const directory = mkdtempSync(resolve(tmpdir(), 'dsh-transport36-'))
  const originalFetch = globalThis.fetch
  let calls = 0
  const pending = new Map()
  globalThis.fetch = async (_input, init) => {
    calls++
    await new Promise(resolve => pending.set(init?.body, resolve))
    if (init?.body === 'private-failed-body') {
      throw Object.assign(new Error('private transport failure'), { code: 'ECONNRESET' })
    }
    return new Response('{}')
  }
  try {
    writeFileSync(resolve(directory, 'settings.yaml'), 'llm-pi-ai:\n  providers:\n    codexai:\n      api: openai-responses\n      baseURL: https://model.test/v1\n')
    const diagnostic = resolve(directory, 'diagnostic.jsonl')
    const upstream = process.env.DSH_TEST_UPSTREAM_ROOT
      ?? resolve(import.meta.dirname, '../../../.cache/dsh-upstream/source')
    const result = bootstrapProviderTransport({ DSH_HOME: directory,
      DECISION_HUB_MODEL_TRANSPORT_UPSTREAM_ROOT: upstream,
      DECISION_HUB_MODEL_HTTP_PROXY: 'http://127.0.0.1:7892',
      DECISION_HUB_MODEL_OS_PROXY_ENABLE: '1', DECISION_HUB_MODEL_OS_PROXY_SERVER: 'invalid:bad',
      DECISION_HUB_MODEL_TRANSPORT_DIAGNOSTICS: diagnostic })
    assert.equal(result.proxy_source, 'model_env') // Explicit config does not evaluate invalid OS fallback.
    assert.equal(calls, 0)
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, PROVIDER_STAGE_PUBLISHER_SYMBOL)
    assert.equal(descriptor?.configurable, false)
    assert.equal(descriptor?.writable, false)
    const publisher = descriptor?.value
    assert.equal(typeof publisher, 'function')
    const runnerDescriptor = Object.getOwnPropertyDescriptor(globalThis, PROVIDER_DISPATCH_CONTEXT_RUNNER_SYMBOL)
    assert.equal(runnerDescriptor?.configurable, false)
    assert.equal(runnerDescriptor?.writable, false)
    const runner = runnerDescriptor?.value
    assert.equal(typeof runner, 'function')
    const lifecycleIdentity = { schema_version: 'dsh-provider-stage-diagnostic.v1', at: new Date().toISOString(),
      run_hash: 'a'.repeat(64),
      dispatch_id: '11111111-1111-4111-8111-111111111111', input_hash: 'b'.repeat(64), generation: 1,
      execution_session_hash: 'c'.repeat(64) }
    const lifecycle = { ...lifecycleIdentity, stage: 'receipt_started',
      messages_bytes: 11, system_bytes: 12, tools_bytes: 13, input_bytes: 42 }
    publisher(lifecycle)
    for (const stage of ['receipt_completed', 'delegate_started', 'first_sdk_chunk', 'stream_finished', 'stream_failed']) {
      publisher({ ...lifecycleIdentity, stage, ...(stage === 'receipt_completed' ? { input_bytes: 42 } : {}),
        ...(stage.startsWith('stream_') ? { finish_bytes: 21 } : {}) })
    }
    const secondContext = { run_hash: 'd'.repeat(64), dispatch_id: '22222222-2222-4222-8222-222222222222',
      input_hash: 'e'.repeat(64), generation: 2, execution_session_hash: 'f'.repeat(64) }
    const failedContext = { run_hash: '1'.repeat(64), dispatch_id: '33333333-3333-4333-8333-333333333333',
      input_hash: '2'.repeat(64), generation: 3, execution_session_hash: '3'.repeat(64) }
    const firstContext = Object.fromEntries(Object.entries(lifecycleIdentity)
      .filter(([key]) => ['run_hash', 'dispatch_id', 'input_hash', 'generation', 'execution_session_hash'].includes(key)))
    const first = runner(firstContext, () => globalThis.fetch('https://model.test/v1/responses',
      { method: 'POST', body: 'private-first-body' }))
    const second = runner(secondContext, () => globalThis.fetch('https://model.test/v1/responses',
      { method: 'POST', body: 'private-second-body' }))
    const failed = runner(failedContext, () => globalThis.fetch('https://model.test/v1/responses',
      { method: 'POST', body: 'private-failed-body' }))
    pending.get('private-second-body')(); await second
    pending.get('private-failed-body')(); await assert.rejects(() => failed)
    pending.get('private-first-body')(); await first
    const records = readFileSync(diagnostic, 'utf8').trim().split(/\r?\n/).map(line => JSON.parse(line))
    const policy = records[0]
    assert.equal(policy.connectivity, 'not_tested')
    assert.equal(policy.sdk_default_timeout_ms, 600000)
    assert.equal(policy.dependency.version, '7.28.0')
    assert.equal(policy.dependency.verification_scope, 'lock_declaration_and_loaded_entry_not_full_package_tarball')
    assert.match(policy.dependency.actual_entry_sha256, /^[a-f0-9]{64}$/)
    assert.deepEqual(records.slice(1).map(record => record.stage), [
      'receipt_started', 'receipt_completed', 'delegate_started', 'first_sdk_chunk', 'stream_finished', 'stream_failed',
      'request_started', 'request_started', 'request_started', 'headers_received', 'request_failed', 'headers_received'])
    assert.deepEqual(records[1], lifecycle)
    for (const context of [firstContext, secondContext, failedContext]) {
      const linked = records.filter(record => record.schema_version === 'model-transport-diagnostic.v1'
        && record.dispatch_id === context.dispatch_id)
      assert.deepEqual(linked.map(record => record.stage), context === failedContext
        ? ['request_started', 'request_failed'] : ['request_started', 'headers_received'])
      for (const record of linked) {
        for (const [key, value] of Object.entries(context)) assert.equal(record[key], value)
      }
    }
    assert.equal(calls, 3)
    const beforeInvalid = readFileSync(diagnostic, 'utf8')
    assert.throws(() => publisher({ ...lifecycle, prompt: 'private prompt' }), /stage_diagnostic_invalid/)
    assert.throws(() => publisher({ ...lifecycle, input_bytes: '42' }), /stage_diagnostic_invalid/)
    assert.equal(readFileSync(diagnostic, 'utf8'), beforeInvalid)
    assert.ok(!readFileSync(diagnostic, 'utf8').includes('private prompt'))
    assert.ok(!readFileSync(diagnostic, 'utf8').includes('private-first-body'))
    assert.ok(!readFileSync(diagnostic, 'utf8').includes('private-second-body'))
    assert.ok(!readFileSync(diagnostic, 'utf8').includes('private-failed-body'))
    assert.ok(!readFileSync(diagnostic, 'utf8').includes('private transport failure'))
    assert.throws(() => runner({ ...firstContext, prompt: 'private prompt' }, () => undefined), /dispatch_context_invalid/)
    assert.throws(() => { globalThis[PROVIDER_STAGE_PUBLISHER_SYMBOL] = () => undefined }, TypeError)
    assert.throws(() => { globalThis[PROVIDER_DISPATCH_CONTEXT_RUNNER_SYMBOL] = () => undefined }, TypeError)
  } finally { globalThis.fetch = originalFetch; rmSync(directory, { recursive: true, force: true }) }
})
