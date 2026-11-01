// @vitest-environment jsdom
// Controlled presentation wires, not actual Provider or publication evidence.
import { createHash, webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import fixture from '../../../../artifacts/d2-perpetual-saved-report/synthetic-presentation-fixture.json'
import advice from '../../../../artifacts/d2-perpetual-advice/available-pure-persisted-example.json'
import { researchDecisionSynthesisV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { settingsApi } from '../api/decisionSettings'
import { decisionBodyHash as hash } from '../api/decisionOriginals'
import { readSavedPerpetualReport } from '../api/perpetualSavedReport'
import { buildPerpetualReadingSnapshot, renderPerpetualReadingHtml } from './perpetualReportExport'
import { PerpetualSavedReportBoundary } from './PerpetualSavedReport'
import nativeReportRaw from './fixtures/perpetualSavedAction.native-sql-report.json?raw'
import nativeReportSource from './fixtures/perpetualSavedAction.native-sql-report.provenance.json'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
afterEach(() => vi.restoreAllMocks())

function report() {
  const packet = JSON.parse(JSON.parse(fixture.artifact_json).payload_json).perpetual_synthesis
  return { ...fixture, probability_definitions: {
    schema_version: 'perpetual-probability-definitions.v1', status: 'unavailable',
    binding: { owner_id: fixture.owner_id, run_id: fixture.run_id,
      mapped_ref: packet.binding.mapped_ref, provider_emission_ref: packet.binding.provider_emission_ref,
      synthesis_ref: packet.binding.synthesis_ref }, reason_code: 'prepared_probability_definitions_unavailable',
  } }
}
async function page(wire: unknown) {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(wire)))
  const host = document.createElement('div'), root = createRoot(host)
  await act(async () => root.render(<PerpetualSavedReportBoundary runId={fixture.run_id}><p>旧分支</p></PerpetualSavedReportBoundary>))
  for (let count = 0; count < 50 && !host.querySelector('[aria-label="已核对报告审核状态"]'); count += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  }
  return { host, close: async () => { await act(async () => root.unmount()) } }
}
function comparisonInputs(comparison: typeof advice.comparison) {
  return { fixture_basis: 'synthetic-comparison-inputs-not-producer', opportunity_id: comparison.opportunity_id,
    comparison_as_of: comparison.as_of, bindings: comparison.bindings }
}
async function action() {
  const wire = report(), packet = JSON.parse(JSON.parse(wire.artifact_json).payload_json).perpetual_synthesis
  const primary = researchDecisionSynthesisV2Schema.parse(packet.synthesis), a = structuredClone(advice)
  const at = '2026-01-01T00:25:30+00:00', admission = { ref: 'synthetic:admission', hash: 'a'.repeat(64) }
  const reference = async (ref: string, body: unknown) => ({ ref, hash: await hash(body) })
  const { snapshot: s, revision: r, comparison: c, target: t } = a
  Object.assign(t, { assigned_at: at.replace('+00:00', 'Z'), window_start: '2026-01-01T00:26:00Z', window_end: '2026-01-01T04:26:00Z',
    instrument_ref: primary.candidate_set.context.instrument_ref,
    candidate_target_ref: primary.market_distribution.direction_distribution.target_ref, anchor_rule_ref: s.bindings.anchor_rule })
  Object.assign(c, { as_of: primary.as_of, forecast_as_of: primary.as_of, opportunity_id: primary.opportunity_id,
    scope_id: primary.candidate_set.context.scope_id, target_ref: await reference('synthetic:target', t) })
  const probabilities = primary.market_distribution.direction_distribution.probabilities
  if (probabilities === null) throw new Error('Controlled fixture requires its original probabilities')
  c.market_distribution.direction_distribution.probabilities = probabilities
  c.bindings.target = c.target_ref
  c.market_distribution.market_target_ref = c.target_ref
  c.market_distribution.direction_distribution.target_ref = c.target_ref
  const context = primary.candidate_set.context
  Object.assign(c.bindings, { settings: context.settings_ref, instrument: context.instrument_ref,
    account: context.account_ref, utility: context.utility_ref })
  c.market_distribution.instrument_ref = context.instrument_ref
  c.price_bridge.known_at = primary.as_of
  for (const probability of [c.market_distribution.direction_distribution, c.event_probability.occurrence_distribution, c.event_probability.outcome_given_occurrence]) probability.as_of = primary.as_of
  c.binding_hash = await hash(comparisonInputs(c))
  c.payload_hash = await hash(Object.fromEntries(Object.entries(c).filter(([key]) => key !== 'payload_hash')))
  Object.assign(s, { run_id: wire.run_id, context: primary.candidate_set.context, opportunity_id: primary.opportunity_id,
    known_at: at.replace('+00:00', 'Z'), decision_cutoff_at: primary.as_of, source_result_ref: packet.binding.mapped_ref.output_id,
    source_result_hash: packet.binding.mapped_ref.payload_hash })
  Object.assign(s.bindings, { native_synthesis: packet.binding.synthesis_ref, candidate_set: primary.candidate_set_ref,
    comparison: await reference('synthetic:comparison', c), final_target: c.target_ref,
    candidate_target: t.candidate_target_ref, instrument: s.context.instrument_ref, settings: s.context.settings_ref,
    account: s.context.account_ref, position: s.context.position_ref, utility: s.context.utility_ref })
  const selected = primary.candidate_set.candidates[0]
  Object.assign(r, { context: s.context, opportunity_id: s.opportunity_id, bindings: s.bindings,
    published_at: at.replace('+00:00', 'Z'), as_of: primary.as_of, selected_candidate: selected,
    selected_candidate_ref: primary.candidate_set.candidate_refs[0], valid_until: selected.valid_until,
    target_spec_ref: c.target_ref.ref, target_spec_hash: c.target_ref.hash })
  const derivation = { schema_version: 'perpetual-publication-derivation.v1',
    native_synthesis_ref: packet.binding.synthesis_ref, review_authorization_ref: s.bindings.review_authorization,
    admission_ref: admission, task_binding_ref: admission, mapped_original: packet.binding.mapped_ref,
    provider_original: packet.binding.provider_emission_ref, transport_original: packet.binding.provider_emission_ref,
    producer_generation: 1, native_wire_originals_ref: admission, research_materials_ref: admission,
    native_paths_ref: admission, bound_paths_ref: admission, comparison_inputs_ref: await reference('synthetic:comparison-inputs', comparisonInputs(c)),
    comparison_ref: s.bindings.comparison, final_target_ref: c.target_ref, anchor_receipt_ref: s.bindings.anchor_receipt,
    candidate_id_mapping: { open_long: 'open_long', open_short: 'open_short', no_change: 'no_change' },
    unavailable_candidates: [], changes: 'one_assigned_target_rebinding_and_typed_datetime_wire_projection_only',
    original_raw_forecast_retained: true, probabilities_and_amplitudes_changed: false,
    unit_basis: '100_USDT_comparison_not_balance_size_or_fill', primary_position_state: 'unknown',
    uncovered_branches: ['long', 'short'], gaps: ['synthetic_unknown'] }
  s.bindings.derivation = await reference('synthetic:derivation', derivation)
  s.snapshot_hash = await hash(Object.fromEntries(Object.entries(s).filter(([key]) => key !== 'snapshot_hash')))
  r.payload_hash = await hash(Object.fromEntries(Object.entries(r).filter(([key]) => key !== 'payload_hash')))
  return { schema_version: 'perpetual-saved-action.v1', status: 'available',
    binding: { owner_id: wire.owner_id, run_id: wire.run_id, artifact_id: wire.artifact_id,
      report_synthesis_ref: packet.binding.synthesis_ref, admission_ref: admission }, accepted_at: at,
    command_receipt: await commandReceipt(wire.run_id, s, r, at), snapshot: s, revision: r, comparison: c,
    target: t, cost_assumptions: a.cost_assumptions, cost_assumptions_ref: await reference('synthetic:cost', a.cost_assumptions),
    derivation, financial_qualified: false, execution_authorized: false }
}
async function commandReceipt(run: string, s: typeof advice.snapshot, r: typeof advice.revision, at: string) {
  const receipt = { schema_version: 'action-command-receipt.v1', owner_id: 'system:action-publication:v1',
    request_id: 'publish_' + await hash([run, s.opportunity_id]), plan_id: r.plan_id, operation: 'publish',
    payload_hash: 'b'.repeat(64), accepted_at: at, response: { status: 'published', opportunity_id: s.opportunity_id,
      decision_id: s.id, snapshot_hash: s.snapshot_hash, branches: [{ position_context: r.position_context,
        plan_id: r.plan_id, revision_id: r.revision_id, revision_hash: r.payload_hash, outbox_id: 2 }],
      qualification: 'research_trial', execution: 'paper_only', uncovered_branches: ['long', 'short'],
      gaps: ['synthetic_unknown'], server_time: at } }
  return { ...receipt, receipt_hash: await hash(receipt) }
}
type Action = Awaited<ReturnType<typeof action>>
it('decodes the exact complete authenticated native and private SQL report golden', async () => {
  expect(createHash('sha256').update(nativeReportRaw).digest('hex')).toBe('a992e436cbe049a29e91867f3b94c10f7919c9ac8f2d76c0dc4346b0c177309f')
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(nativeReportRaw))
  const decoded = await readSavedPerpetualReport(nativeReportSource.run_id)
  if (!decoded || decoded.action_publication?.status !== 'available') throw new Error('Expected original available report')
  const { artifact: _artifact, payload: _payload, ...wire } = decoded
  expect(wire).toEqual(JSON.parse(nativeReportRaw))
  expect(decoded.financial_qualified).toBe(false)
  expect(decoded.execution_authorized).toBe(false)
})

it('retains the original integer outbox identity from the actual receipt shape', async () => {
  const saved = await action() // The preserved a992e436 same-Run receipt has integer outbox_id 2.
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ ...report(), action_publication: saved })))
  const decoded = await readSavedPerpetualReport(fixture.run_id)
  if (decoded?.action_publication?.status !== 'available') throw new Error('Expected available action')
  expect(decoded.action_publication.command_receipt.response.branches[0].outbox_id).toBe(2)
  expect(decoded.action_publication.command_receipt).toEqual(saved.command_receipt)
})

it.each([['string', '2'], ['boolean', true], ['zero', 0], ['negative', -1],
  ['fraction', 1.5], ['unsafe integer', Number.MAX_SAFE_INTEGER + 1]] as const)(
  'rejects %s outbox identity even with a consistent receipt hash', async (_label, outboxId) => {
    const saved = await action(), { receipt_hash: _oldHash, ...body } = saved.command_receipt
    const response = { ...body.response, branches: [{ ...body.response.branches[0], outbox_id: outboxId }] }
    const command = { ...body, response }, receipt = { ...command, receipt_hash: await hash(command) }
    vi.spyOn(settingsApi, 'session').mockReturnValue(null)
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({
      ...report(), action_publication: { ...saved, command_receipt: receipt },
    })))
    await expect(readSavedPerpetualReport(fixture.run_id)).rejects.toThrow()
  },
)

async function reseal(value: Action, bindInput = true) {
  const { snapshot: s, revision: r, comparison: c, derivation: d, command_receipt: receipt } = value
  c.target_ref.hash = await hash(value.target)
  r.target_spec_hash = c.target_ref.hash
  c.binding_hash = await hash(comparisonInputs(c))
  c.payload_hash = await hash(Object.fromEntries(Object.entries(c).filter(([key]) => key !== 'payload_hash')))
  if (bindInput) d.comparison_inputs_ref.hash = c.binding_hash
  s.bindings.comparison = { ...s.bindings.comparison, hash: await hash(c) }
  d.comparison_ref = s.bindings.comparison
  s.bindings.derivation = { ...s.bindings.derivation, hash: await hash(d) }
  r.bindings = s.bindings
  s.snapshot_hash = await hash(Object.fromEntries(Object.entries(s).filter(([key]) => key !== 'snapshot_hash')))
  r.payload_hash = await hash(Object.fromEntries(Object.entries(r).filter(([key]) => key !== 'payload_hash')))
  receipt.response.snapshot_hash = s.snapshot_hash
  receipt.response.branches[0].revision_hash = r.payload_hash
  receipt.receipt_hash = await hash(Object.fromEntries(Object.entries(receipt).filter(([key]) => key !== 'receipt_hash')))
}
it('rejects a foreign complete comparison input hash with every other seal consistent', async () => {
  const saved = await action()
  saved.derivation.comparison_inputs_ref.hash = 'c'.repeat(64)
  await reseal(saved, false)
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ ...report(), action_publication: saved })))
  await expect(readSavedPerpetualReport(fixture.run_id)).rejects.toThrow()
})
const corruptions: [string, (value: Action) => void][] = [
  ['owner', value => { value.binding.owner_id = 'foreign-owner' }],
  ['Run', value => { value.binding.run_id = 'foreign-run' }],
  ['artifact', value => { value.binding.artifact_id = 'foreign-artifact' }],
  ['snapshot Run', value => { value.snapshot.run_id = 'foreign-run' }],
  ['early receipt', value => { value.accepted_at = value.command_receipt.accepted_at = '2026-01-01T00:24:59.999999Z' }],
  ['future receipt', value => { value.accepted_at = value.command_receipt.accepted_at = '2026-01-01T00:26:00.000001Z' }],
  ['command identity', value => { value.command_receipt.request_id = 'publish_wrong' }],
  ['command plan', value => { value.command_receipt.plan_id = 'foreign-plan' }],
  ['report ref', value => { value.binding.report_synthesis_ref = { ref: 'foreign-ref', hash: 'c'.repeat(64) } }],
  ['mapped source', value => { value.derivation.mapped_original = { ...value.derivation.mapped_original, output_id: 'foreign-original' } }],
  ['provider source', value => { value.derivation.provider_original = { ...value.derivation.provider_original, output_id: 'foreign-original' } }],
  ['admission source', value => { value.binding.admission_ref = { ref: 'foreign-admission', hash: 'c'.repeat(64) } }],
  ['selected mapping', value => { value.derivation.candidate_id_mapping.open_long = 'open_short' }],
  ['target horizon', value => { value.target.horizon = '24h' }],
  ['target instrument', value => { value.target.instrument_ref = { ref: 'foreign-instrument', hash: 'c'.repeat(64) } }],
  ['cost content', value => { value.cost_assumptions.assumed_taker_rate = '0.123456789012345678' }],
  ['authorization', value => { value.execution_authorized = true }],
  ['qualification', value => { value.financial_qualified = true }],
]

it.each(corruptions)('rejects inconsistent %s before returning a displayable or exportable report', async (_name, mutate) => {
  const saved = await action()
  mutate(saved)
  await reseal(saved)
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ ...report(), action_publication: saved })))
  await expect(readSavedPerpetualReport(fixture.run_id)).rejects.toThrow()
})

it('distinguishes an old reading copy without an action attachment from a missing publication', async () => {
  const view = await page(report())
  try {
    expect(view.host.textContent).toContain('此副本未附方案比较')
    expect(view.host.textContent).not.toContain('本报告未保存动作盈利比较或首选原件')
    expect(view.host.textContent).toContain('条件盈利概率、单位预期净收益与首选方案保持未知')
  } finally { await view.close() }
})

it('shows an explicitly unsaved action without losing the authenticated report or inventing a ranking', async () => {
  const view = await page({ ...report(), action_publication: {
    schema_version: 'perpetual-saved-action.v1', status: 'unavailable', reason_code: 'action_publication_not_saved',
  } })
  try {
    expect(view.host.querySelector('[aria-label="已核对报告审核状态"]')).not.toBeNull()
    expect(view.host.textContent).toContain('该次研究尚未保存已发表的方案比较')
    expect(view.host.querySelector('table[aria-label="发表时方案比较"]')).toBeNull()
    expect(view.host.textContent).toContain('PDF 导出尚未接通')
  } finally { await view.close() }
})

it('shows the exact historical selection, conditional probabilities, unit returns, costs and both clocks', async () => {
  const saved = await action(), view = await page({ ...report(), action_publication: saved })
  try {
    const section = view.host.querySelector('[aria-label="已发表方案原件"]')!
    expect(section.textContent).toContain('发表时入选方案')
    expect(section.querySelectorAll('tbody tr')).toHaveLength(3)
    expect(section.textContent).toContain('209.115631349798540329')
    expect(section.textContent).toContain('43.5%')
    expect(section.textContent).toContain('成交概率未知')
    expect(section.textContent).toContain('0.0005')
    expect(section.textContent).toContain(fixture.published_at)
    expect(section.textContent).toContain(saved.accepted_at)
    expect(section.textContent).not.toContain('当前可执行')
    expect(section.querySelector('button,a')).toBeNull()
  } finally { await view.close() }
})

it('keeps exactly the same action wire in existing JSON and inert HTML reading exports', async () => {
  const saved = await action(), view = await page({ ...report(), action_publication: saved })
  try {
    const value = await readSavedPerpetualReport(fixture.run_id)
    expect(value).not.toBeNull()
    if (!value) throw new Error('Expected authenticated report')
    expect(JSON.parse(buildPerpetualReadingSnapshot(value)).report.action_publication).toEqual(saved)
    const section = view.host.querySelector<HTMLElement>('[aria-label="已保存的永续研究报告"]')!
    const html = new DOMParser().parseFromString(renderPerpetualReadingHtml(value, section), 'text/html')
    expect(html.body.textContent).toContain('发表时入选方案')
    expect(JSON.parse(html.querySelector('[data-perpetual-reading-json]')!.textContent!).report.action_publication).toEqual(saved)
    expect(html.querySelector('script,button,a[href]')).toBeNull()
    expect(value.source_json).toBe(fixture.source_json)
  } finally { await view.close() }
})

it('keeps historical publication visible after its window expires without granting current permission', async () => {
  const view = await page({ ...report(), read_at: '2030-01-01T00:00:00Z', action_publication: await action() })
  try {
    expect(view.host.querySelectorAll('table[aria-label="发表时方案比较"] tbody tr')).toHaveLength(3)
    expect(view.host.textContent).toContain('不是当前授权')
  } finally { await view.close() }
})

it.each(['receipt', 'snapshot', 'comparison'] as const)('rejects corrupted %s seals before any readable value', async kind => {
  const saved = await action()
  if (kind === 'receipt') saved.command_receipt.receipt_hash = '0'.repeat(64)
  if (kind === 'snapshot') saved.snapshot.snapshot_hash = '0'.repeat(64)
  if (kind === 'comparison') saved.comparison.payload_hash = '0'.repeat(64)
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ ...report(), action_publication: saved })))
  await expect(readSavedPerpetualReport(fixture.run_id)).rejects.toThrow()
})
