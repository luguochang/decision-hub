import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { gunzipSync } from 'node:zlib'
import { beforeAll, expect, it } from 'vitest'

const root = resolve(import.meta.dirname, '../../../..')
const fixture = resolve(import.meta.dirname, 'fixtures/retained-context-admission-order/recorded.json.gz')
interface Outcome {
  scenarios: { name: string; evidence: number; facts: number; has_delayed_evidence: boolean;
    full_canonical_fresh_verifier_passed: boolean; detail_reads: number; root_events_unchanged_during_reads: boolean;
    fresh_reconstructed_sources_are_new_objects: boolean }[]
  original_bad_marker_still_rejected: boolean
  no_child: { sources: number; proof_reads: number; detail_reads: number }
  original_bad_marker_error: ErrorEvidence
  negative_cases: { mode: string; passed: boolean; error: ErrorEvidence | null }[]
}
interface ErrorEvidence { name: string; constructor_name: string; code: string | null; message: string; production_frames: string[] }
let result: Outcome
beforeAll(() => {
  const zipped = readFileSync(fixture)
  expect(createHash('sha256').update(zipped).digest('hex')).toBe('288301fd894cb0f3fbca3ea7fd9774be2e645caf87c21fb9f521c715768fd803')
  const bytes = gunzipSync(zipped)
  expect(createHash('sha256').update(bytes).digest('hex')).toBe('8b8a81b1b778b57c6ce4af7a6f4f9b238581d034a200d3747a13afd91a1efcb1')
  const outputRoot = process.env.DSH_REPLAY_OUTPUT_ROOT ?? resolve(root, 'evidence/retained-context-admission-order')
  mkdirSync(outputRoot, { recursive: true })
  const run = mkdtempSync(resolve(outputRoot, 'run-')), temporary = resolve(run, 'tmp')
  mkdirSync(temporary)
  const guard = resolve(run, 'no-io.mjs')
  const contracts = pathToFileURL(resolve(root, 'packages/contracts_ts/src/index.ts')).href
  writeFileSync(guard, `import {createRequire,registerHooks,syncBuiltinESMExports} from 'node:module';
registerHooks({resolve(specifier,context,next){return specifier==='@decision-hub/contracts-ts'?next(${JSON.stringify(contracts)},context):next(specifier,context)}});
const require=createRequire(import.meta.url),deny=()=>{throw Error('recorded_order_replay_network_denied')};
for(const key of ['http','https']){const m=require('node:'+key);m.request=deny;m.get=deny;}
const net=require('node:net');net.connect=deny;net.createConnection=deny;net.Socket.prototype.connect=deny;net.Server.prototype.listen=deny;
require('node:tls').connect=deny;globalThis.fetch=deny;syncBuiltinESMExports();\n`)
  const loader = resolve(root, 'node_modules/.pnpm/tsx@4.23.13/node_modules/tsx/dist/loader.mjs')
  const env: NodeJS.ProcessEnv = { SystemRoot: process.env.SystemRoot, PATH: process.env.PATH, NODE_ENV: 'test',
    TEMP: temporary, TMP: temporary, TSX_DISABLE_CACHE: '1', NODE_COMPILE_CACHE: resolve(run, 'node-cache'),
    XDG_CACHE_HOME: resolve(run, 'cache'), DSH_SESSION_ENTRY: process.env.DSH_SESSION_ENTRY }
  const output = execFileSync(process.execPath, ['--import', pathToFileURL(guard).href, '--import', pathToFileURL(loader).href,
    resolve(import.meta.dirname, 'retained-context-admission-order-support.mts')],
  { cwd: root, env, input: bytes, windowsHide: true, timeout: 60_000, maxBuffer: 1_048_576 })
  writeFileSync(resolve(run, 'result.json'), output)
  result = JSON.parse(output.toString('utf8')) as Outcome
}, 65_000)

it('includes the recorded EV09 and four Facts committed during child/proof awaits before the single fresh detail read', () => {
  const value = result.scenarios.find(row => row.name === 'late-admission')!
  expect(value.evidence).toBe(15)
  expect(value.facts).toBe(35)
  expect(value.has_delayed_evidence).toBe(true)
  expect(value.full_canonical_fresh_verifier_passed).toBe(true)
  expect(value.detail_reads).toBe(1)
  expect(value.root_events_unchanged_during_reads).toBe(true)
})
it('freshly reconstructs the stable recorded snapshot with new authenticated objects', () => {
  const value = result.scenarios.find(row => row.name === 'stable')!
  expect(value.full_canonical_fresh_verifier_passed).toBe(true)
  expect(value.fresh_reconstructed_sources_are_new_objects).toBe(true)
  expect(value.detail_reads).toBe(1)
})
it('keeps historical bad marker348 rejected without altering its bytes', () => {
  expect(result.original_bad_marker_still_rejected).toBe(true)
  expect(result.original_bad_marker_error).toMatchObject({ name: 'Error', constructor_name: 'HubClientError',
    code: 'research_provider_native_context_invalid', message: 'research_provider_native_context_invalid' })
  expect(result.original_bad_marker_error.production_frames.some(frame => frame.startsWith('retained-context.ts:348:'))).toBe(true)
})
it('keeps the no-child read on the original single-detail path', () => {
  expect(result.no_child).toEqual({ sources: 0, proof_reads: 0, detail_reads: 1 })
})
it.each(['missing-selected', 'changed-selected', 'lost-accepted', 'wrong-root', 'wrong-generation',
  'wrong-provider', 'changed-prefix', 'cancel-during-proof', 'deadline-during-proof'])(
  'still rejects %s with fresh authentication', mode => {
    const value = result.negative_cases.find(row => row.mode === mode)!
    expect(value.passed).toBe(false)
    const message = ['wrong-root', 'wrong-generation'].includes(mode) ? 'decision_hub_review_context_link_mismatch'
      : ['cancel-during-proof', 'deadline-during-proof'].includes(mode) ? 'decision_hub_review_context_binding_changed'
        : 'research_provider_native_context_invalid'
    const file = ['wrong-root', 'wrong-generation', 'cancel-during-proof', 'deadline-during-proof'].includes(mode)
      ? 'bridge.ts:' : ['wrong-provider', 'changed-prefix'].includes(mode) ? 'child-research-context.ts:' : 'retained-context.ts:'
    expect(value.error?.message).toBe(message)
    expect(value.error?.production_frames.some(frame => frame.startsWith(file))).toBe(true)
    expect(value.error?.name).toBe('Error')
    expect(value.error?.constructor_name).toBe(file === 'retained-context.ts:' ? 'HubClientError' : 'Error')
    expect(value.error?.code).toBe(file === 'retained-context.ts:' ? 'research_provider_native_context_invalid' : null)
  },
)
it('does not absorb later records outside the declared native prefix', () => {
  expect(result.negative_cases.find(row => row.mode === 'outside-prefix')!.passed).toBe(true)
})
