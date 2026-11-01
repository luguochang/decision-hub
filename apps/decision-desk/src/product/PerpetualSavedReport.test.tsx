// @vitest-environment jsdom
// Synthetic presentation wire; actual SQL/native read is tested separately.
import { webcrypto } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import fixture from '../../../../artifacts/d2-perpetual-saved-report/synthetic-presentation-fixture.json'
import actualSaved from '../../../../artifacts/d2-perpetual-saved-report/available-actual-v5-example.json'
import definedSaved from './__fixtures__/saved-probability-definitions-available.json'
import { primarySavedPerpetualSynthesis, readSavedPerpetualReport } from '../api/perpetualSavedReport'
import * as savedReportApi from '../api/perpetualSavedReport'
import { PerpetualSavedReportBoundary } from './PerpetualSavedReport'
import { settingsApi } from '../api/decisionSettings'
import { ApiRequestError, API_REQUEST_TIMEOUT_MS } from '../api/client'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

function withoutSavedDefinitions(wire: typeof fixture | typeof actualSaved) {
  const packet = JSON.parse(JSON.parse(wire.artifact_json).payload_json).perpetual_synthesis
  return { ...wire, probability_definitions: { schema_version: 'perpetual-probability-definitions.v1', status: 'unavailable',
    binding: { owner_id: wire.owner_id, run_id: wire.run_id, mapped_ref: packet.binding.mapped_ref,
      provider_emission_ref: packet.binding.provider_emission_ref, synthesis_ref: packet.binding.synthesis_ref },
    reason_code: 'prepared_probability_definitions_unavailable' } }
}

it('parses the exact saved causal report returned from the actual persisted pipeline', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(withoutSavedDefinitions(actualSaved))))
  const saved = await readSavedPerpetualReport(actualSaved.run_id)
  expect(saved?.source_sha256).toBe(actualSaved.source_sha256)
  expect(saved?.artifact_id).toBe(actualSaved.artifact_id)
  expect(saved?.financial_qualified).toBe(false)
})

it('reads exact signed V5 source, shows saved human body and suppresses old bundle exports', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(withoutSavedDefinitions(fixture))))
  const host = document.createElement('div'), root = createRoot(host)
  try {
    await act(async () => root.render(<PerpetualSavedReportBoundary runId={fixture.run_id}><button>旧PDF导出</button></PerpetualSavedReportBoundary>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(host.textContent).toContain('合成四小时永续报告')
    expect(host.textContent).toContain('这是用于阅读验证的合成事实段')
    expect(host.textContent).toContain('尚未取得金融资格')
    expect(host.textContent).toContain('PDF 导出尚未接通')
    expect(host.textContent).not.toContain('旧PDF导出')
    expect(host.querySelector('a[download]')?.getAttribute('href')).toContain(`/research/runs/${fixture.run_id}/report/source`)
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls[0][1]?.credentials).toBe('include')
  } finally { await act(async () => root.unmount()) }
})

it('rejects changed original bytes or another Run without offering a V1 fallback', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(withoutSavedDefinitions({ ...fixture, source_json: fixture.source_json+' ' }))))
  await expect(readSavedPerpetualReport(fixture.run_id)).rejects.toThrow()
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(withoutSavedDefinitions(fixture))))
  await expect(readSavedPerpetualReport('other-run')).rejects.toThrow()
})

it('retains the existing V1 report branch only on explicit not-applicable response', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'perpetual_report_not_applicable' }), { status: 404 }))
  const host = document.createElement('div'), root = createRoot(host)
  try {
    await act(async () => root.render(<PerpetualSavedReportBoundary runId="legacy"><p>已有历史报告</p></PerpetualSavedReportBoundary>))
    expect(host.textContent).toContain('已有历史报告')
  } finally { await act(async () => root.unmount()) }
})

it('reads saved probabilities and report identity without inventing an action comparison', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(withoutSavedDefinitions(fixture))))
  const host = document.createElement('div'), root = createRoot(host)
  try {
    await act(async () => root.render(<PerpetualSavedReportBoundary runId={fixture.run_id}><p>旧报告</p></PerpetualSavedReportBoundary>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    const identity = host.querySelector('[aria-label="报告身份"]')!
    expect(identity.textContent).toContain('比特币 USDT 永续合约（BTC）')
    expect(identity.textContent).toContain('4 小时')
    expect(identity.textContent).toContain('用户自报账户')
    expect(identity.textContent).not.toContain('run-test')
    expect(identity.textContent).not.toContain('unknown-account')
    expect(identity.textContent).not.toContain('fixture:settings')
    const scope = host.querySelector<HTMLDetailsElement>('[aria-label="原报告范围与条件"]')!
    expect(scope.open).toBe(false)
    expect(scope.textContent).toContain('okx:BTC-USDT-SWAP')
    expect(scope.textContent).toContain('run-test')
    expect(scope.textContent).toContain('unknown-account')
    expect(scope.textContent).toContain('fixture:settings')
    expect(host.querySelector('[aria-label="事件发生概率"]')?.textContent).toContain('未知')
    const conditional = host.querySelector('[aria-label="事件发生后的结果概率"]')!
    expect(conditional.textContent).toContain('cool（名称未提供）：50%')
    expect(conditional.textContent).toContain('hot（名称未提供）：20%')
    expect(conditional.textContent).toContain('未保存可读定义')
    expect(conditional.textContent).not.toContain('synthetic:given-defined-event-E')
    expect(scope.textContent).toContain('结果 1：cool；原保存概率 0.5')
    expect(scope.textContent).toContain('结果 3：hot；原保存概率 0.2')
    expect(scope.textContent).toContain('synthetic:given-defined-event-E')
    expect(host.querySelector('[aria-label="市场方向概率"]')?.textContent).toContain('上涨：43.5%')
    const actions = host.querySelector('[aria-label="候选动作与比较"]')!
    expect(actions.textContent).toContain('此副本未附方案比较')
    expect(actions.textContent).toContain('条件盈利概率、单位预期净收益与首选方案保持未知')
    expect(actions.querySelector('table')).toBeNull()
    const candidates = actions.querySelector('details')!
    expect(candidates.open).toBe(false)
    expect(candidates.textContent).toContain('开仓，目标多头')
    expect(candidates.textContent).toContain('开仓，目标空头')
    expect(candidates.textContent).toContain('维持，目标空仓')
    const next = host.querySelector('nav[aria-label="报告后续操作"]')!
    expect(next.querySelector('a')?.getAttribute('href')).toBe('#/settings')
    expect(next.querySelector('a')?.textContent).toBe('查看当前建议与模拟')
    expect(next.textContent).toContain('核对原任务、账户、合约与设置版本')
    expect(next.textContent).not.toContain('run-test')
    expect(next.textContent).toContain('历史报告不能沿用为当前授权')
    expect(next.textContent).toContain('重新读取同一模拟账户')
    expect(next.textContent).toContain('以太坊 USDT 永续合约（ETH）')
    expect(next.textContent).toContain('保存新的 ETH 设置')
    expect(next.textContent).toContain('发起独立 ETH 研究')
    expect(next.querySelector('button')).toBeNull()
    expect(host.querySelector('details > pre')?.parentElement?.hasAttribute('open')).toBe(false)
  } finally { await act(async () => root.unmount()) }
})

it('reads saved zero probability and keeps twelve candidate branches collapsed without ranking them', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(withoutSavedDefinitions(actualSaved))))
  const saved = (await readSavedPerpetualReport(actualSaved.run_id))!
  const candidates = primarySavedPerpetualSynthesis(saved.payload.perpetual_synthesis.synthesis).candidate_set.candidates
  // Presentation-only branch fixture; comparison and selection are deliberately absent.
  for (let index = 0; index < 9; index += 1) candidates.push({ ...candidates[index % 3], candidate_id: `unranked-${index}`,
    position_branch: index < 5 ? 'long' : 'short', action: index % 2 ? 'reduce' : 'close' })
  vi.spyOn(savedReportApi, 'readSavedPerpetualReport').mockResolvedValue(saved)
  const host = document.createElement('div'), root = createRoot(host)
  try {
    await act(async () => root.render(<PerpetualSavedReportBoundary runId={actualSaved.run_id}><p>旧报告</p></PerpetualSavedReportBoundary>))
    expect(host.querySelector('[aria-label="市场方向概率"]')?.textContent).toContain('下跌：0%')
    expect(host.querySelector('[aria-label="事件发生概率"]')?.textContent).toContain('未知')
    const actions = host.querySelector('[aria-label="候选动作与比较"]')!
    expect(actions.querySelector('details')?.open).toBe(false)
    expect(actions.querySelectorAll('li')).toHaveLength(12)
    expect(actions.textContent).toContain('已有多头仓位时')
    expect(actions.textContent).toContain('已有空头仓位时')
    expect(actions.textContent).toContain('未知')
    expect(actions.querySelector('table')).toBeNull()
    expect(actions.textContent).not.toContain('0 USDT')
    expect(actions.textContent).not.toContain('0%')
  } finally { await act(async () => root.unmount()) }
})

it('checks saved probability definitions and displays each exact target statement beside its probabilities', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(definedSaved)))
  const saved = await readSavedPerpetualReport(definedSaved.run_id)
  expect(saved?.probability_definitions.status).toBe('available')
  const [event, occurrence, outcome] = definedSaved.probability_definitions.definitions
  const host = document.createElement('div'), root = createRoot(host)
  try {
    await act(async () => root.render(<PerpetualSavedReportBoundary runId={definedSaved.run_id}><p>旧报告</p></PerpetualSavedReportBoundary>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    const eventView = host.querySelector('[aria-label="事件发生概率"]')!
    expect(eventView.textContent).toContain(event.definition.statement)
    expect(eventView.textContent).toContain(occurrence.definition.statement)
    const outcomeView = host.querySelector('[aria-label="事件发生后的结果概率"]')!
    expect(outcomeView.textContent).toContain(outcome.definition.statement)
    expect(outcomeView.textContent).not.toContain('未保存可读定义')
    expect(outcomeView.textContent).not.toContain('结果 1（名称未提供）')
    const payload = JSON.parse(JSON.parse(definedSaved.artifact_json).payload_json)
    for (const item of payload.perpetual_synthesis.synthesis.event_probability.outcome_given_occurrence.probabilities ?? []) {
      expect(outcomeView.textContent).toContain(`${item.outcome_id}：`)
    }
    const scope = host.querySelector<HTMLDetailsElement>('[aria-label="原报告范围与条件"]')!
    expect(scope.open).toBe(false)
    expect(scope.textContent).toContain(event.definition_ref.ref)
    expect(scope.textContent).toContain(occurrence.definition_ref.hash)
    expect(host.querySelector('[aria-label="报告身份"]')?.textContent).not.toContain(definedSaved.run_id)
  } finally { await act(async () => root.unmount()) }
})

it('rejects probability-definition identity, role, reference and original-body changes', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  const fetch = vi.spyOn(globalThis, 'fetch')
  const mutations = [
    (wire: any) => { wire.probability_definitions.binding.owner_id = 'foreign-owner' },
    (wire: any) => { wire.probability_definitions.binding.run_id = 'other-run' },
    (wire: any) => { wire.probability_definitions.binding.mapped_ref.payload_hash = 'a'.repeat(64) },
    (wire: any) => { wire.probability_definitions.binding.provider_emission_ref.output_id = 'other-emission' },
    (wire: any) => { wire.probability_definitions.binding.synthesis_ref.ref = 'other-synthesis' },
    (wire: any) => { wire.probability_definitions.definitions[0].definition.statement += ' changed' },
    (wire: any) => { wire.probability_definitions.definitions[1].definition_ref.hash = 'b'.repeat(64) },
    (wire: any) => { wire.probability_definitions.definitions[1].definition_ref.ref = 'other-target' },
    (wire: any) => { wire.probability_definitions.definitions[2].definition.role = 'occurrence_target' },
    (wire: any) => { wire.probability_definitions.definitions.reverse() },
    (wire: any) => { wire.probability_definitions.definitions.pop() },
  ]
  for (const mutate of mutations) {
    const wire = structuredClone(definedSaved)
    mutate(wire)
    fetch.mockResolvedValue(new Response(JSON.stringify(wire)))
    await expect(readSavedPerpetualReport(definedSaved.run_id)).rejects.toThrow()
  }
  const unavailable = withoutSavedDefinitions(fixture)
  unavailable.probability_definitions.binding.owner_id = 'foreign-owner'
  fetch.mockResolvedValue(new Response(JSON.stringify(unavailable)))
  await expect(readSavedPerpetualReport(fixture.run_id)).rejects.toThrow()
})

async function errorClassificationPage() {
  const host = document.createElement('div'), root = createRoot(host)
  await act(async () => root.render(<PerpetualSavedReportBoundary runId={fixture.run_id}><p>未核对的旧报告回退</p></PerpetualSavedReportBoundary>))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
  return { host, retry: async () => {
    await act(async () => host.querySelector('button')!.click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
  }, close: async () => { await act(async () => root.unmount()) } }
}
function expectSafeReadError(host: HTMLElement, category: 'identity' | 'integrity' | 'read') {
  const message = host.querySelector('[role="alert"]')?.textContent ?? ''
  expect(message).toContain(category === 'identity' ? '恢复本地会话' : category === 'integrity' ? '原件或完整性' : '暂时未能读取')
  if (category !== 'identity') expect(message).not.toContain('恢复本地会话')
  expect(message).not.toContain('PRIVATE_ERROR_DETAIL')
  expect(message).not.toContain('perpetual_report_')
  expect(host.textContent).not.toContain('未核对的旧报告回退')
  expect(host.querySelector('[aria-label="已保存的永续研究报告"]')).toBeNull()
  expect(host.querySelector('button')?.textContent).toBe('重试读取')
}

it.each([
  [401, 'PRIVATE_ERROR_DETAIL', 'identity'],
  [403, 'PRIVATE_ERROR_DETAIL', 'identity'],
  [409, 'perpetual_report_original_unavailable', 'integrity'],
  [503, 'PRIVATE_ERROR_DETAIL', 'read'],
  [404, 'PRIVATE_ERROR_DETAIL', 'read'],
] as const)('saved report error classification maps HTTP%s safely without legacy fallback', async (status, detail, category) => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail, private: 'PRIVATE_ERROR_DETAIL' }), { status }))
  const page = await errorClassificationPage()
  try { expectSafeReadError(page.host, category) } finally { await page.close() }
})

it.each(['network', 'timeout'] as const)('saved report error classification makes %s a retryable read failure', async kind => {
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(kind === 'network' ? new TypeError('PRIVATE_ERROR_DETAIL') : new ApiRequestError('PRIVATE_ERROR_DETAIL', 'timeout'))
  const page = await errorClassificationPage()
  try { expectSafeReadError(page.host, 'read') } finally { await page.close() }
})

it.each(['schema', 'json', 'hash'] as const)('saved report error classification refuses 200 %s integrity failures', async mutation => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  const wire = withoutSavedDefinitions(fixture)
  const changed = mutation === 'schema' ? { ...wire, financial_qualified: true }
    : mutation === 'json' ? { ...wire, artifact_json: 'PRIVATE_ERROR_DETAIL invalid JSON' }
      : { ...wire, source_json: wire.source_json + ' ' }
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(changed)))
  const page = await errorClassificationPage()
  try { expectSafeReadError(page.host, 'integrity') } finally { await page.close() }
})

it('saved report error classification reconnects for a detected client owner change without exposing the report', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: 'foreign-owner', csrf_token: 'PRIVATE_ERROR_DETAIL', expires_at: '2099-01-01T00:00:00Z' })
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(withoutSavedDefinitions(fixture))))
  const page = await errorClassificationPage()
  try { expectSafeReadError(page.host, 'identity') } finally { await page.close() }
})

it('saved report error classification retries the same report using only an explicit GET', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'PRIVATE_ERROR_DETAIL' }), { status: 503 }))
    .mockImplementation(async () => new Response(JSON.stringify(withoutSavedDefinitions(fixture))))
  const page = await errorClassificationPage()
  try {
    expectSafeReadError(page.host, 'read'); expect(fetcher).toHaveBeenCalledTimes(1)
    await page.retry()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls.every(([url, init]) => url === '/v1/decision-settings/research/runs/' + fixture.run_id + '/report'
      && (init?.method ?? 'GET') === 'GET')).toBe(true)
    expect(page.host.querySelector('[role="alert"]')).toBeNull()
    expect(page.host.querySelector('[aria-label="已保存的永续研究报告"]')).not.toBeNull()
  } finally { await page.close() }
})

it('saved report dedicated wait accepts a 40s delayed 200 through full original and available-definition validation', async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => new Promise<Response>((resolve, reject) => {
    const timer = setTimeout(() => resolve(new Response(JSON.stringify(definedSaved))), 40_000)
    init!.signal!.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('aborted', 'AbortError')) }, { once: true })
  }))
  const pending = readSavedPerpetualReport(definedSaved.run_id).then(value => ({ value, error: null }), error => ({ value: null, error }))
  await vi.advanceTimersByTimeAsync(40_000)
  const outcome = await pending
  expect(outcome.error).toBeNull()
  expect(outcome.value?.source_sha256).toBe(definedSaved.source_sha256)
  expect(outcome.value?.probability_definitions.status).toBe('available')
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0][0]).toBe('/v1/decision-settings/research/runs/' + definedSaved.run_id + '/report')
  expect(fetcher.mock.calls[0][1]?.credentials).toBe('include')
  expect(API_REQUEST_TIMEOUT_MS).toBe(30_000)
  expect(vi.getTimerCount()).toBe(0)
})

it('saved report dedicated wait retains external abort and clears timers before a late 200', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const controller = new AbortController(); let completed = 0
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => new Promise<Response>((resolve, reject) => {
    const timer = setTimeout(() => { completed++; resolve(new Response(JSON.stringify(definedSaved))) }, 40_000)
    init!.signal!.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('aborted', 'AbortError')) }, { once: true })
  }))
  const pending = readSavedPerpetualReport(definedSaved.run_id, controller.signal).then(() => null, error => error)
  await vi.advanceTimersByTimeAsync(1_000); controller.abort()
  expect(await pending).toMatchObject({ name: 'AbortError' })
  expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
  await vi.advanceTimersByTimeAsync(100_000)
  expect(completed).toBe(0); expect(fetcher).toHaveBeenCalledTimes(1)
})

it('exact BTC18 saved report wire passes the full reader and renders approved original body with honest probabilities', async () => {
  const raw = readFileSync('../../artifacts/d2-perpetual-saved-report/btc18-actual-v5-example.json', 'utf8')
  const owner = (JSON.parse(raw) as { owner_id: string }).owner_id
  const run = 'run_f811707c3c77475db895ff931e55cec2'
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: owner, csrf_token: 'controlled-owner-session', expires_at: '2099-01-01T00:00:00Z' })
  const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(raw))
  const saved = await readSavedPerpetualReport(run)
  expect(saved?.run_id).toBe(run)
  expect(saved?.source_sha256).toBe('99bafd883bc5c8f3d483787027d54284c3b938f7e2bfc19695cdfd347f286294')
  expect(saved?.payload.perpetual_synthesis.binding.native_review_verdict).toBe('approved')
  expect(saved?.payload.perpetual_synthesis.authorization?.verdict).toBe('approved')
  expect(saved?.probability_definitions.status).toBe('available')
  expect(saved?.financial_qualified).toBe(false); expect(saved?.execution_authorized).toBe(false)
  const host = document.createElement('div'), root = createRoot(host)
  try {
    await act(async () => root.render(<PerpetualSavedReportBoundary runId={run}><p>未经核对的旧报告</p></PerpetualSavedReportBoundary>))
    await vi.waitFor(async () => {
      await act(async () => { await Promise.resolve() })
      expect(host.querySelector('[aria-label="已保存的永续研究报告"]')).not.toBeNull()
    })
    expect(host.textContent).toContain(saved!.artifact.headline)
    expect(host.textContent).toContain(saved!.artifact.summary)
    expect(host.textContent).toContain(saved!.payload.facts[0])
    const direction = host.querySelector('[aria-label="市场方向概率"]')!
    expect(direction.textContent).toContain('上涨：38%'); expect(direction.textContent).toContain('横盘：24%'); expect(direction.textContent).toContain('下跌：38%')
    for (const label of ['事件发生概率', '事件发生后的结果概率']) {
      const estimate = host.querySelector(`[aria-label="${label}"]`)!
      expect(estimate.textContent).toContain('未知（原报告未提供概率）')
      expect(estimate.textContent).not.toContain('：0%')
    }
    expect(host.querySelector('[role="alert"]')).toBeNull()
    expect(host.textContent).not.toContain('未经核对的旧报告')
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls.every(([url, init]) => url === '/v1/decision-settings/research/runs/' + run + '/report'
      && (init?.method ?? 'GET') === 'GET' && init?.credentials === 'include')).toBe(true)
  } finally { await act(async () => root.unmount()) }
})
