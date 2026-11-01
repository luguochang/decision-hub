import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
const helperPath = new URL('./development-model-settings.mjs', import.meta.url)
// A missing implementation is an explicit RED assertion, not an import error.
test('development capacity helper exists', () => assert.ok(existsSync(helperPath)))
const helper = existsSync(helperPath) ? await import(helperPath) : null
const route = { provider: 'codexai', api: 'openai-responses', baseURL: 'https://fixture.invalid/v1', model: 'gpt-6.1-sol' }
const legacy = () => ({ 'llm-pi-ai': { providers: { codexai: {
  displayName: 'Development model', api: route.api, baseURL: route.baseURL,
  apiKeyEnv: 'OPENAI_API_KEY', reasoning: 'medium', models: [{ id: route.model, name: route.model,
    contextWindow: 128000, maxTokens: 8192, input: ['text', 'image'], reasoningEfforts: {
      low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: 'max' } }],
} } }, 'agent-default-model': { provider: 'codexai', model: route.model } })
const source = () => { const value = legacy(); value['llm-pi-ai'].providers.codexai.models[0].contextWindow = 262144; return value }
const yaml = createRequire(import.meta.url)('yaml')
test('fresh generated home uses matching declaration instead of hardcoded 128k', () => {
  const dir = mkdtempSync(resolve(tmpdir(), 'development-capacity-'))
  const from = resolve(dir, 'source.yaml'); writeFileSync(from, yaml.stringify(source()))
  const quote = value => "'" + value.replaceAll("'", "''") + "'"
  const result = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command',
    `$ErrorActionPreference='Stop'; . ./infra/dsh/development-runtime.ps1; Initialize-DevelopmentHome -HomePath ${quote(resolve(dir, 'home'))} -CredentialEnvironment @{OPENAI_BASE_URL='https://fixture.invalid/v1'} -Model 'gpt-6.1-sol' -ModelSettingsSource ${quote(from)};`], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  assert.equal(JSON.parse(readFileSync(resolve(dir, 'home/settings.yaml'), 'utf8'))['llm-pi-ai'].providers.codexai.models[0].contextWindow, 262144)
  const bytes = readFileSync(resolve(dir, 'home/settings.yaml'), 'utf8')
  writeFileSync(from, 'invalid: [')
  const again = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command',
    `$ErrorActionPreference='Stop'; . ./infra/dsh/development-runtime.ps1; Initialize-DevelopmentHome -HomePath ${quote(resolve(dir, 'home'))} -CredentialEnvironment @{} -Model 'gpt-6.1-sol' -ModelSettingsSource ${quote(from)};`], { encoding: 'utf8' })
  assert.equal(again.status, 0, again.stderr); assert.equal(readFileSync(resolve(dir, 'home/settings.yaml'), 'utf8'), bytes)
})
if (helper) {
  test('stopped migration changes one scalar, preserves source, private backup and idempotence', () => {
    const dir = mkdtempSync(resolve(tmpdir(), 'development-capacity-migration-'))
    const home = resolve(dir, 'data/development/fixture/dsh-home')
    mkdirSync(home, { recursive: true }); mkdirSync(resolve(dir, 'data/development/fixture/runtime'))
    const sourcePath = resolve(dir, 'source.yaml'); const targetPath = resolve(home, 'settings.yaml')
    const from = yaml.stringify(source()); const original = JSON.stringify(legacy(), null, 2)
    writeFileSync(sourcePath, from); writeFileSync(targetPath, original)
    const quote = value => "'" + value.replaceAll("'", "''") + "'"
    // CLI call through the actual guarded PS boundary; hashes are independent fixture digests.
    const digest = raw => (createRequire(import.meta.url)('node:crypto')).createHash('sha256').update(raw).digest('hex')
    const run = (sourceSha, targetSha, prefix = '') => spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command',
      `$ErrorActionPreference='Stop'; . ./infra/dsh/development-runtime.ps1; function Get-NetTCPConnection { @() }; ${prefix}; Invoke-DevelopmentModelCapacityMigration -RepositoryRoot ${quote(dir)} -DevelopmentRoot 'data/development/fixture' -SourcePath ${quote(sourcePath)} -ExpectedSourceSha256 '${sourceSha}' -ExpectedTargetSha256 '${targetSha}' | ConvertTo-Json -Compress`], { encoding: 'utf8' })
    const drift = run(digest(from), '0'.repeat(64))
    assert.notEqual(drift.status, 0); assert.equal(readFileSync(targetPath, 'utf8'), original)
    const sourceDrift = run('0'.repeat(64), digest(original))
    assert.notEqual(sourceDrift.status, 0); assert.equal(readFileSync(targetPath, 'utf8'), original)
    const running = run(digest(from), digest(original),
      `$supervisor=@{pid=$PID;started_ticks=(Get-Process -Id $PID).StartTime.ToUniversalTime().Ticks.ToString();state='running';launch_id='fixture'}; $supervisor | ConvertTo-Json | Set-Content -LiteralPath ${quote(resolve(dir, 'data/development/fixture/runtime/supervisor.json'))}`)
    assert.notEqual(running.status, 0); assert.match(running.stderr, /development_capacity_instance_not_absent/)
    writeFileSync(resolve(dir, 'data/development/fixture/runtime/supervisor.json'), JSON.stringify({ pid: 2147483647, started_ticks: '1', state: 'stopped' }))
    writeFileSync(resolve(dir, 'data/development/fixture/runtime/processes.json'), '[]')
    const result = run(digest(from), digest(original))
    assert.equal(result.status, 0, result.stderr)
    const receipt = JSON.parse(result.stdout)
    assert.equal(receipt.changed, true)
    assert.equal(readFileSync(receipt.backup, 'utf8'), original)
    const acl = spawnSync('pwsh', ['-NoProfile', '-NonInteractive', '-Command',
      `. ./infra/dsh/decision-settings-session.ps1; Test-ProductSettingsOwnerOnlyAcl -LiteralPath ${quote(receipt.backup)} -Identity ([Security.Principal.WindowsIdentity]::GetCurrent().User) -Directory $false`], { encoding: 'utf8' })
    assert.equal(acl.status, 0, acl.stderr); assert.equal(acl.stdout.trim(), 'True')
    assert.equal(readFileSync(targetPath, 'utf8'), original.replace('128000', '262144'))
    assert.equal(readFileSync(sourcePath, 'utf8'), from)
    const second = run(digest(from), digest(readFileSync(targetPath, 'utf8')))
    assert.equal(second.status, 0, second.stderr); assert.equal(JSON.parse(second.stdout).changed, false)
    assert.ok(!result.stdout.includes(route.baseURL)); assert.ok(!result.stdout.includes('OPENAI_API_KEY'))
    const locked = run(digest(from), digest(readFileSync(targetPath, 'utf8')),
      `$held=[IO.File]::Open(${quote(resolve(dir, 'data/development/fixture/runtime/supervisor.lock'))},'OpenOrCreate','ReadWrite','None')`)
    assert.notEqual(locked.status, 0)
    assert.match(locked.stderr, /development_instance_running/)
  })
  test('linked source ancestor is rejected and lookup stdout carries no route or credentials', () => {
    const dir = mkdtempSync(resolve(tmpdir(), 'development-capacity-links-'))
    const actual = resolve(dir, 'actual'); mkdirSync(actual)
    writeFileSync(resolve(actual, 'settings.yaml'), yaml.stringify({ ...source(), credential: 'do-not-print' }))
    const linked = resolve(dir, 'linked'); symlinkSync(actual, linked, 'junction')
    assert.throws(() => helper.readDevelopmentPlainFile(resolve(linked, 'settings.yaml')), /development_capacity_path_linked/)
    const digest = (createRequire(import.meta.url)('node:crypto')).createHash('sha256').update(route.baseURL).digest('hex')
    const result = spawnSync(process.execPath, [helperPath.pathname.replace(/^\/([A-Z]:)/, '$1'), '--source', resolve(actual, 'settings.yaml'), '--model', route.model, '--base-url-sha256', digest], { encoding: 'utf8' })
    assert.equal(result.status, 0, result.stderr)
    assert.equal(JSON.parse(result.stdout).contextWindow, 262144)
    assert.ok(!result.stdout.includes(route.baseURL)); assert.ok(!result.stdout.includes('do-not-print'))
  })
  test('matching declaration is source-declared and only context scalar changes, idempotently', () => {
    const raw = JSON.stringify(legacy(), null, 2)
    const from = yaml.stringify({ ...source(), credential: 'do-not-copy' })
    assert.deepEqual(helper.declaredDevelopmentCapacity(from, route), { contextWindow: 262144, provenance: 'source_declared_not_verified_remote_maximum' })
    const prepared = helper.prepareDevelopmentCapacity(from, raw, route)
    assert.equal(prepared.text, raw.replace('128000', '262144'))
    assert.equal(helper.prepareDevelopmentCapacity(from, prepared.text, route).text, prepared.text)
    assert.ok(!prepared.text.includes('do-not-copy'))
  })
  for (const field of ['provider', 'api', 'baseURL', 'model']) {
    test(`rejects mismatched ${field}`, () => assert.throws(() => helper.declaredDevelopmentCapacity(JSON.stringify(source()), { ...route, [field]: 'wrong' }), /development_capacity_/))
  }
  for (const value of [undefined, 0, 1, 8192, -1, 1.5, '262144', Number.MAX_SAFE_INTEGER + 1]) {
    test(`rejects bad capacity ${value}`, () => {
      const from = source(); from['llm-pi-ai'].providers.codexai.models[0].contextWindow = value
      assert.throws(() => helper.declaredDevelopmentCapacity(JSON.stringify(from), route), /development_capacity_/)
    })
  }
  test('rejects duplicate model and YAML keys', () => {
    const from = source(); from['llm-pi-ai'].providers.codexai.models.push(from['llm-pi-ai'].providers.codexai.models[0])
    assert.throws(() => helper.declaredDevelopmentCapacity(JSON.stringify(from), route), /development_capacity_/)
    assert.throws(() => helper.declaredDevelopmentCapacity('a: 1\na: 2', route), /development_capacity_/)
  })
  test('arbitrary hand edits never count as launcher template', () => {
    for (const mutate of [v => { v.policy = true }, v => { v['llm-pi-ai'].providers.codexai.models[0].maxTokens = 9000 },
      v => { v['llm-pi-ai'].providers.codexai.reasoning = 'high' }]) {
      const target = legacy(); mutate(target)
      assert.throws(() => helper.prepareDevelopmentCapacity(JSON.stringify(source()), JSON.stringify(target), route), /development_capacity_target_not_generated_template/)
    }
  })
  test('non-template context numeric spellings cannot inflate migrated capacity', () => {
    for (const spelling of ['1.28e5', '128000.0', '128e3']) {
      const raw = JSON.stringify(legacy()).replace('128000', spelling)
      assert.throws(() => helper.prepareDevelopmentCapacity(JSON.stringify(source()), raw, route), /development_capacity_target_not_generated_template/)
    }
  })
  test('real readonly declaration aligns known development target without writing either', () => {
    const sourcePath = resolve('data/dsh-live/settings.yaml')
    const targetPath = resolve('data/development/default/dsh-home/settings.yaml')
    const from = readFileSync(sourcePath, 'utf8'); const target = readFileSync(targetPath, 'utf8')
    const actualRoute = { ...route, baseURL: JSON.parse(target)['llm-pi-ai'].providers.codexai.baseURL }
    const prepared = helper.prepareDevelopmentCapacity(from, target, actualRoute)
    assert.equal(JSON.parse(prepared.text)['llm-pi-ai'].providers.codexai.models[0].contextWindow, 262144)
    assert.equal(readFileSync(sourcePath, 'utf8'), from); assert.equal(readFileSync(targetPath, 'utf8'), target)
  })
}
