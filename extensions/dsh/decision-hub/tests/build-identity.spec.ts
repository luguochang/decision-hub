import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { isAbsolute, relative, resolve } from 'node:path'
import { expect, it } from 'vitest'
import { resolveConfig } from 'vite'
import hostConfig from '../vite.host.config.js'
import testConfig from '../vitest.config.js'

const repoRoot = resolve(import.meta.dirname, '../../../..')
const pluginRelative = 'extensions/dsh/decision-hub'
const pluginRoot = resolve(repoRoot, pluginRelative)
const fixtureParent = resolve(repoRoot, 'evidence/build-identity')
const generatorRelative = `${pluginRelative}/scripts/generate-build-identity.mjs`
const generatedRelative = `${pluginRelative}/src/generated-build-identity.ts`
const sourceInputs = [
  `${pluginRelative}/src/perpetual-synthesis-protocol.ts`,
  `${pluginRelative}/src/reviewer-model-view.ts`,
  'packages/contracts_ts/src/index.ts',
  'packages/contracts_ts/src/generated/contract.ts',
] as const
const controlInputs = [
  `${pluginRelative}/package.json`, `${pluginRelative}/cordis.patch.yml`,
  generatorRelative, `${pluginRelative}/scripts/build-client.mjs`,
  `${pluginRelative}/vite.host.config.ts`, `${pluginRelative}/tsconfig.json`,
  'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'packages/contracts_ts/package.json',
  'infra/dsh/presets/decision-research/agent.cordis.yml',
  'infra/dsh/presets/decision-research/preset.yml', 'infra/dsh/upstream.lock.json',
] as const

type Fixture = { root: string; plugin: string; generator: string }

function within(parent: string, child: string): void {
  const part = relative(realpathSync(parent), realpathSync(child))
  if (part.startsWith('..') || isAbsolute(part)) throw new Error('synthetic fixture escaped its owned root')
}

function syntheticFixture(): Fixture {
  mkdirSync(fixtureParent, { recursive: true })
  within(repoRoot, fixtureParent)
  const root = mkdtempSync(resolve(fixtureParent, 'synthetic-'))
  within(fixtureParent, root)
  const oldEntries = ['index.ts', 'research-tool.ts', 'query-catalog.ts', 'synthesis-tool.ts',
    'final-review-gate.ts', 'frozen-review-prompt.ts', 'reviewer-tool.ts', 'host/bridge.ts', 'client/index.js']
  const paths = new Set([...sourceInputs, ...controlInputs,
    ...oldEntries.map(name => `${pluginRelative}/src/${name}`)])
  for (const name of paths) {
    const file = resolve(root, name)
    mkdirSync(resolve(file, '..'), { recursive: true })
    writeFileSync(file, name.endsWith('.json') ? JSON.stringify({ synthetic_fixture: true })
      : name.endsWith('.yaml') || name.endsWith('.yml') ? '# synthetic build input, no financial authority\n'
        : '// synthetic build input, no financial authority\n')
  }
  const generator = resolve(root, generatorRelative)
  copyFileSync(resolve(repoRoot, generatorRelative), generator)
  return { root, plugin: resolve(root, pluginRelative), generator }
}

function generate(fixture: Fixture): { buildHash: string; inputs: string[] } {
  const temporary = resolve(fixture.root, 'temporary')
  mkdirSync(temporary, { recursive: true })
  const raw = execFileSync(process.execPath, [fixture.generator], {
    cwd: fixture.plugin, encoding: 'utf8', timeout: 30_000,
    env: { ...process.env, TEMP: temporary, TMP: temporary, NODE_COMPILE_CACHE: temporary },
  })
  const value = JSON.parse(raw) as { buildHash?: unknown; inputs?: unknown }
  if (typeof value.buildHash !== 'string' || !/^[a-f0-9]{64}$/.test(value.buildHash)
    || !Array.isArray(value.inputs) || !value.inputs.every(item => typeof item === 'string')) {
    throw new Error('real generator CLI returned an invalid identity')
  }
  return { buildHash: value.buildHash, inputs: value.inputs }
}

function changeInput(fixture: Fixture, name: string): void {
  const file = resolve(fixture.root, name)
  const raw = readFileSync(file, 'utf8')
  writeFileSync(file, name.endsWith('.json')
    ? JSON.stringify({ ...JSON.parse(raw), synthetic_revision: 2 })
    : raw + (name.endsWith('.yml') || name.endsWith('.yaml')
      ? '\n# synthetic changed input\n' : '\n// synthetic changed input\n'))
}

it.each(sourceInputs)('binds the actual source bytes of %s through the real CLI', name => {
  const fixture = syntheticFixture(), before = generate(fixture)
  changeInput(fixture, name)
  const after = generate(fixture)
  expect(after.buildHash).not.toBe(before.buildHash)
  expect(after.inputs).toContain(name)
})

it.each(controlInputs)('binds the declared build control %s through the real CLI', name => {
  const fixture = syntheticFixture(), before = generate(fixture)
  changeInput(fixture, name)
  const after = generate(fixture)
  expect(after.buildHash).not.toBe(before.buildHash)
  expect(after.inputs).toContain(name)
})

it('is stable for unchanged bytes and excludes only its own generated output', () => {
  const fixture = syntheticFixture(), first = generate(fixture), repeat = generate(fixture)
  expect(repeat).toEqual(first)
  writeFileSync(resolve(fixture.root, generatedRelative), '// synthetic stale output to regenerate\n')
  const regenerated = generate(fixture)
  expect(regenerated).toEqual(first)
  expect(regenerated.inputs).not.toContain(generatedRelative)
  expect(readFileSync(resolve(fixture.root, generatedRelative), 'utf8')).toContain(first.buildHash)
})

it.each([['host', hostConfig], ['vitest', testConfig]] as const)('%s resolves workspace contracts through the real Vite resolver', async (name, selected) => {
  const config = await resolveConfig({ ...selected, root: pluginRoot, configFile: false,
    envFile: false, cacheDir: resolve(fixtureParent, 'vite-resolve-cache'), logLevel: 'silent' }, 'build')
  const resolved = await config.createResolver()('@decision-hub/contracts-ts', resolve(pluginRoot, 'src/index.ts'))
  expect(resolved).toBeDefined()
  expect(realpathSync(resolved!)).toBe(realpathSync(resolve(repoRoot, 'packages/contracts_ts/src/index.ts')))
  if (name === 'vitest') {
    const sourceRoot = process.env.DSH_UPSTREAM_SOURCE_ROOT ?? resolve(repoRoot, '.cache/dsh-upstream/source')
    const llm = await config.createResolver()('@deepseek-ai/dsh-llm', resolve(pluginRoot, 'src/index.ts'))
    expect(llm).toBeDefined()
    expect(realpathSync(llm!)).toBe(realpathSync(resolve(sourceRoot, 'packages/llm/llm/lib/index.js')))
  }
})
