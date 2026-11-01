import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { gunzipSync } from 'node:zlib'
import { beforeAll, expect, it } from 'vitest'

interface Scenario {
  scenario: string; passed: boolean; reads: number
  appended: { seq: number; type: string }[]
}
interface Replay {
  runId: string; rootArchiveSha256: string
  bridge: Scenario[]
  replacement: { snapshotLast: number; liveLast: number; passed: boolean; appended: unknown[] }[]
  pinned: { passed: boolean; proofReads: unknown[] }
  refresh: { passed: boolean; proofReads: unknown[] }
}

const root = resolve(import.meta.dirname, '../../../..')
const fixture = resolve(import.meta.dirname, 'fixtures/retained-context-race/fixture.json.gz')
const expectedFixtureSha = '9c89436a9dc661a31b3a455d60d3c38f0d99b1c32a8c5e8457586ba430b7dceb'
let result: Replay

beforeAll(() => {
  const compressed = readFileSync(fixture)
  expect(createHash('sha256').update(compressed).digest('hex')).toBe('5bc32681c40f53f97fa0a419dc9df3379542f178eda55f7c00653708dddd8240')
  const bytes = gunzipSync(compressed)
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(expectedFixtureSha)
  const evidence = resolve(root, 'evidence/retained-context-race')
  const execution = mkdtempSync(resolve(evidence, 'run-'))
  const temporary = resolve(execution, 'tmp')
  mkdirSync(temporary)
  const networkGuard = resolve(execution, 'network-guard.mjs')
  writeFileSync(networkGuard, `import {createRequire,syncBuiltinESMExports} from 'node:module';
const require=createRequire(import.meta.url), deny=()=>{throw new Error('offline-replay-network-forbidden')};
for(const key of ['http','https']){const m=require('node:'+key);m.request=deny;m.get=deny;}
const net=require('node:net');net.connect=deny;net.createConnection=deny;net.Server.prototype.listen=deny;
require('node:tls').connect=deny;globalThis.fetch=deny;syncBuiltinESMExports();\n`)
  const environment: NodeJS.ProcessEnv = {
    SystemRoot: process.env.SystemRoot, PATH: process.env.PATH, NODE_ENV: 'test',
    TEMP: temporary, TMP: temporary, TSX_DISABLE_CACHE: '1',
    NODE_COMPILE_CACHE: resolve(execution, 'node-cache'), XDG_CACHE_HOME: resolve(execution, 'cache'),
    DSH_SESSION_ENTRY: process.env.DSH_SESSION_ENTRY,
  }
  const loader = resolve(root, 'node_modules/.pnpm/tsx@4.23.13/node_modules/tsx/dist/loader.mjs')
  const output = execFileSync(process.execPath, [
    '--import', pathToFileURL(networkGuard).href,
    '--import', pathToFileURL(loader).href,
    resolve(import.meta.dirname, 'retained-context-race-support.mts'),
  ], { cwd: root, env: environment, input: bytes, timeout: 60_000, windowsHide: true, maxBuffer: 1_048_576 })
  writeFileSync(resolve(execution, 'replay-result.json'), output)
  result = JSON.parse(output.toString('utf8')) as Replay
  expect(result.runId).toBe('run_13b596fd7295472d9db23713864d792d')
  expect(result.rootArchiveSha256).toBe('169fef9437dbc9902e84d99abdeea435e98024748bbfd16a5b545e24b73907a2')
}, 65_000)

it('re-reads the complete authenticated context after the actual 591 to 592 relay', () => {
  const relay = result.bridge.find(row => row.scenario === 'relay591to592')!
  expect(relay.passed).toBe(true)
  expect(relay.reads).toBe(2)
  expect(relay.appended).toEqual([
    { seq: 593, type: 'compaction/prune' }, { seq: 594, type: 'user/message' },
  ])
})

it('uses one read for a stable complete historical prefix', () => {
  const stable = result.bridge.find(row => row.scenario === 'stable591')!
  expect(stable.passed).toBe(true)
  expect(stable.reads).toBe(1)
  expect(stable.appended).toEqual([
    { seq: 592, type: 'compaction/prune' }, { seq: 593, type: 'user/message' },
  ])
})

it.each(['historical-rewrite', 'cancel-during-reread', 'deadline-during-reread'])(
  'does not append after %s', scenario => {
    const stopped = result.bridge.find(row => row.scenario === scenario)!
    expect(stopped.passed).toBe(false)
    expect(stopped.reads).toBe(2)
    expect(stopped.appended).toEqual([])
  },
)

it('keeps the replacement full-equality gate closed for an old snapshot', () => {
  const old = result.replacement.find(row => row.snapshotLast === 591 && row.liveLast === 592)!
  expect(old.passed).toBe(false)
  expect(old.appended).toEqual([])
})

it('retains the original pinned and refreshed child Provider proof checks', () => {
  expect(result.pinned.passed).toBe(true)
  expect(result.refresh.passed).toBe(true)
  expect(result.pinned.proofReads).toHaveLength(8)
  expect(result.refresh.proofReads).toHaveLength(8)
})
