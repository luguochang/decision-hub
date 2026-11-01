// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { act, type ComponentType } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import actual from '../../../../artifacts/d2-manual-paper-execution/signed-api-pure-example.json'
import actualEth from '../../../../artifacts/d2-manual-paper-execution/eth-wire-example.json'
import { settingsApi } from '../api/decisionSettings'
import { ApiRequestError } from '../api/client'
import { decisionBodyHash } from '../api/decisionOriginals'
import { makeManualPaperIntent, saveManualPaperIntent, perpetualManualPaper, readManualPaperIntent, type ManualPaperReceipt, type ManualPaperExecution } from '../api/perpetualManualPaper'
import type { PerpetualAdviceSelection, PerpetualAdviceView } from '../api/perpetualAdvice'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const prepared = actual.commands.find(value => value.operation === 'perp_prepare')!
const selection = { owner_id: prepared.owner_id, partition: prepared.response.command.partition, settings_revision: prepared.response.command.settings_revision,
  settings_state_version: prepared.response.command.settings_state_version, horizon: '4h' } as PerpetualAdviceSelection
const value = { ...selection, status: 'available', revision: { revision_id: prepared.response.command.revision.ref, payload_hash: prepared.response.command.revision.hash,
  selected_candidate: { action: 'open', target_side: 'long' } }, target: { window_end: '2026-10-18T16:00:00Z' },
  context: { account_snapshot: { account_kind: 'paper', equity: '2500', available_margin: '2500' }, position_context: { state: 'flat' } } } as unknown as PerpetualAdviceView
beforeEach(() => {
  localStorage.clear(); vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id, csrf_token: 'csrf', expires_at: '2099-01-01T00:00:00Z' })
  vi.spyOn(settingsApi, 'options').mockResolvedValue({ owner_id: selection.owner_id, instruments: [
    { instrument_id: 'okx:BTC-USDT-SWAP', contract: { tick_sz: '0.1' } },
    { instrument_id: 'okx:ETH-USDT-SWAP', contract: { tick_sz: '0.01' } },
  ] } as unknown as Awaited<ReturnType<typeof settingsApi.options>>)
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-18T12:01:00Z'))
  vi.spyOn(perpetualManualPaper, 'capabilities').mockResolvedValue({ schema_version: 'perpetual-manual-capabilities.v1', owner_id: selection.owner_id, configured: true, worker_liveness: 'unknown', horizon: '4h', instrument_id: 'okx:BTC-USDT-SWAP', execution: 'paper_only' })
  vi.spyOn(perpetualManualPaper, 'guide').mockRejectedValue(new Error('no captured quote in this fixture'))
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  vi.stubGlobal('IntersectionObserver', class { constructor(private cb: IntersectionObserverCallback) {} observe() { this.cb([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver) } disconnect() {} })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
async function render(view: PerpetualAdviceView | null = value) {
  const files = import.meta.glob('./PerpetualManualPaperPanel.tsx')
  expect(files['./PerpetualManualPaperPanel.tsx'], 'explicit manual paper panel is missing').toBeDefined()
  const module = await files['./PerpetualManualPaperPanel.tsx']() as { PerpetualManualPaperPanel: ComponentType<{ value: PerpetualAdviceView | null; selection: PerpetualAdviceSelection }> }
  const Panel = module.PerpetualManualPaperPanel, host = document.createElement('div'); document.body.append(host); const root = createRoot(host)
  await act(async () => root.render(<Panel value={view} selection={selection} />)); await settle()
  return { host, click: async (text: string) => { const button = [...host.querySelectorAll('button')].find(item => item.textContent === text)!; expect(button).toBeDefined(); await act(async () => button.click()); await settle() },
    rerender: async (nextView: PerpetualAdviceView | null, nextSelection: PerpetualAdviceSelection) => {
      await act(async () => root.render(<Panel value={nextView} selection={nextSelection} />)); await settle()
    },
    close: async () => { await act(async () => root.unmount()); host.remove() } }
}
function input(host: HTMLElement, name: string, value: string) {
  const node = host.querySelector(`input[name="${name}"]`) as HTMLInputElement
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(node, value)
  node.dispatchEvent(new Event('input', { bubbles: true })); node.dispatchEvent(new Event('change', { bubbles: true }))
}

it('requires real paper flat context and never submits while merely viewing advice', async () => {
  const post = vi.spyOn(perpetualManualPaper, 'command')
  const page = await render({ ...value, context: { ...value.context!, account_snapshot: { ...value.context!.account_snapshot!, account_kind: 'user_declared' } } } as unknown as PerpetualAdviceView)
  try { expect(page.host.textContent).toContain('先保存真实模拟账户'); expect(post).not.toHaveBeenCalled() } finally { await page.close() }
})

it('recovers accepted execution when current advice is unavailable after the ledger changes', async () => {
  const queued = actual.commands.find(row => row.operation === 'perp_manual_intent')!
  const original = await makeManualPaperIntent(queued.response.command)
  await saveManualPaperIntent(original)
  vi.spyOn(perpetualManualPaper, 'receipt').mockResolvedValue(queued as unknown as ManualPaperReceipt)
  const read = vi.spyOn(perpetualManualPaper, 'execution').mockResolvedValue({ schema_version: 'perpetual-manual-execution-view.v1',
    owner_id: selection.owner_id, intent: queued.response.intent, receipt: null, fill: null, episode: null, outcome: null } as unknown as ManualPaperExecution)
  const post = vi.spyOn(perpetualManualPaper, 'command')
  const page = await render(null)
  try { expect(read).toHaveBeenCalledWith(original, queued.response.intent!.id); expect(page.host.textContent).toContain('排队等待报价'); expect(post).not.toHaveBeenCalled() } finally { await page.close() }
})

it('uses explicit80 and user exit inputs after visible display; timeout reopen only reads original receipt', async () => {
  const calls: string[] = []
  vi.spyOn(perpetualManualPaper, 'command').mockImplementation(async intent => {
    calls.push(intent.command.schema_version)
    expect((await readManualPaperIntent(selection.owner_id))!.command).toEqual(intent.command)
    if (intent.command.schema_version === 'perpetual-manual-intent-command.v1') {
      expect(intent.command.max_entry_notional_usdt).toBe('80')
      expect(intent.command.stop_price_usdt).toBe('9000')
      throw new TypeError('timeout after explicit confirmation')
    }
    const original = actual.commands.find(row => row.operation === (intent.command.schema_version === 'perpetual-manual-prepare.v1' ? 'perp_prepare' : 'perp_display'))!
    return { ...original, request_id: intent.command.request_id, payload_hash: intent.command_hash, response: { ...original.response, command: intent.command } } as unknown as ManualPaperReceipt
  })
  const page = await render()
  await page.click('准备手动模拟')
  expect(page.host.textContent).toContain('参考报价暂未读取')
  expect(calls).toEqual(['perpetual-manual-prepare.v1'])
  expect((page.host.querySelector('input[name="amount"]') as HTMLInputElement).value).toBe('')
  const utcExit = new Date('2026-10-18T12:05:00Z'), localExit = new Date(utcExit.valueOf() - utcExit.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
  await act(async () => { input(page.host, 'amount', '80'); input(page.host, 'stop', '9000'); input(page.host, 'take', '12000'); input(page.host, 'exit', localExit) })
  await act(async () => (page.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
  await page.click('确认这笔手动模拟')
  expect(calls).toEqual(['perpetual-manual-prepare.v1', 'perpetual-manual-display.v1', 'perpetual-manual-intent-command.v1'])
  expect(page.host.textContent).toContain('先读取原回执')
  await page.close()
  const get = vi.spyOn(perpetualManualPaper, 'receipt').mockRejectedValue(new Error('original not found yet'))
  const reopened = await render()
  try { expect(get).toHaveBeenCalled(); expect(calls).toHaveLength(3); expect(reopened.host.textContent).toContain('先读取原回执') } finally { await reopened.close() }
})

it('archives only a definite422 and permits explicit correction plus a new preparation after a completed episode', async () => {
  const queued = actual.commands.find(row => row.operation === 'perp_manual_intent')!
  const previous = await makeManualPaperIntent(queued.response.command)
  await saveManualPaperIntent(previous)
  vi.spyOn(perpetualManualPaper, 'receipt').mockResolvedValue(queued as unknown as ManualPaperReceipt)
  vi.spyOn(perpetualManualPaper, 'execution').mockResolvedValue({ schema_version: 'perpetual-manual-execution-view.v1', owner_id: selection.owner_id,
    intent: queued.response.intent, receipt: { status: 'filled' }, fill: null, episode: { remaining_contracts: '0' }, outcome: null } as unknown as ManualPaperExecution)
  const post = vi.spyOn(perpetualManualPaper, 'command').mockRejectedValueOnce(new ApiRequestError('invalid command', 'http', 422))
    .mockImplementation(async original => ({ ...prepared, request_id: original.command.request_id, payload_hash: original.command_hash,
      response: { ...prepared.response, command: original.command } } as unknown as ManualPaperReceipt))
  const page = await render()
  try {
    await page.click('准备手动模拟')
    expect(post).toHaveBeenCalledTimes(1)
    expect(await readManualPaperIntent(selection.owner_id)).toBeNull()
    const rejected = Object.keys(localStorage).find(key => key.includes(':rejected:'))!
    expect(JSON.parse(localStorage.getItem(rejected)!).original.command.schema_version).toBe('perpetual-manual-prepare.v1')
    expect(page.host.textContent).toContain('未获接受')
    await page.click('准备手动模拟')
    expect(post).toHaveBeenCalledTimes(2)
    expect((page.host.querySelector('input[name="amount"]') as HTMLInputElement).value).toBe('')
    expect(Object.keys(localStorage).some(key => key.includes('manual-paper-completed.v1:'))).toBe(true)
  } finally { await page.close() }
})

it('shows the selected authenticated tick size without filling stop or take prices', async () => {
  vi.spyOn(perpetualManualPaper, 'command').mockImplementation(async original => ({ ...prepared,
    request_id: original.command.request_id, payload_hash: original.command_hash,
    response: { ...prepared.response, command: original.command } } as unknown as ManualPaperReceipt))
  const page = await render()
  try {
    expect(page.host.textContent).toContain('0.1 USDT')
    expect(page.host.textContent).not.toContain('0.01 USDT')
    await page.click('准备手动模拟')
    expect((page.host.querySelector('input[name="stop"]') as HTMLInputElement).value).toBe('')
    expect((page.host.querySelector('input[name="take"]') as HTMLInputElement).value).toBe('')
    expect(page.host.textContent).toContain('必须是 0.1 的整数倍')
    expect(page.host.textContent).toContain('不会自动生成价格')
  } finally { await page.close() }
})

it('shows the prepared quote guide, applies protocol T1, and reports local input errors without another POST', async () => {
  const api = perpetualManualPaper
  const previous = api.guide
  const guide = vi.fn().mockResolvedValue({
    schema_version: 'perpetual-manual-input-guide.v1', owner_id: selection.owner_id, prepare_request_id: 'pc-prepare',
    context: prepared.response.context, target_side: 'long', tick_sz: '0.1', latest_exit_at: prepared.response.protocol!.T1,
    server_time: '2026-10-18T12:01:00Z', quote: { status: 'fresh',
      sampling_receipt: { ref: 'aqsr_guide', hash: 'a'.repeat(64) }, capture_ref: { ref: 'raw_guide', hash: 'b'.repeat(64) },
      bid: '9990', ask: '10010', provider_observed_at: '2026-10-18T12:00:59Z', observed_local_lower: '2026-10-18T12:00:59Z',
      observed_local_upper: '2026-10-18T12:00:59.100Z', received_at: '2026-10-18T12:00:59.100Z', expires_at: '2026-10-18T12:01:04Z' },
  })
  api.guide = guide
  const post = vi.spyOn(perpetualManualPaper, 'command').mockImplementation(async original => ({ ...prepared,
    request_id: original.command.request_id, payload_hash: original.command_hash,
    response: { ...prepared.response, command: original.command } } as unknown as ManualPaperReceipt))
  const page = await render()
  try {
    await page.click('准备手动模拟')
    expect(guide).toHaveBeenCalledTimes(1)
    expect(page.host.textContent).toContain('买一 9990 USDT')
    expect(page.host.textContent).toContain('卖一 10010 USDT')
    expect(page.host.textContent).toContain('做多：止损价应低于拟入场价，止盈价应高于拟入场价')
    expect(page.host.textContent).toContain('成交时会另取确认后的新报价')
    expect(page.host.textContent).toContain('本准备未单列新入场截止')
    expect((page.host.querySelector('input[name="stop"]') as HTMLInputElement).value).toBe('')
    expect((page.host.querySelector('input[name="take"]') as HTMLInputElement).value).toBe('')
    await page.click('使用允许的最晚时间')
    const expected = new Date(Math.min(Date.parse(prepared.response.protocol!.T1), Date.parse(value.target!.window_end)))
    expect(new Date((page.host.querySelector('input[name="exit"]') as HTMLInputElement).value).valueOf()).toBe(expected.valueOf())
    await act(async () => { input(page.host, 'amount', 'not-a-number'); input(page.host, 'stop', '9000'); input(page.host, 'take', '12000') })
    await act(async () => (page.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
    await page.click('确认这笔手动模拟')
    expect(page.host.textContent).toContain('请输入明确的正数金额与价格。')
    expect(page.host.textContent).not.toContain('结果尚不确定')
    expect(post).toHaveBeenCalledTimes(1)
    const ethSelection = { ...selection, partition: { ...selection.partition, instrument_id: 'okx:ETH-USDT-SWAP' } } as PerpetualAdviceSelection
    const ethValue = { ...value, ...ethSelection } as unknown as PerpetualAdviceView
    await page.rerender(ethValue, ethSelection)
    expect(page.host.textContent).not.toContain('买一 9990 USDT')
    expect(page.host.querySelector('input[name="amount"]')).toBeNull()
  } finally {
    await page.close()
    api.guide = previous
  }
})

it('does not restore a delayed preparation after the selected revision changes', async () => {
  let release!: (value: ManualPaperReceipt) => void
  const delayed = new Promise<ManualPaperReceipt>(resolve => { release = resolve })
  const post = vi.spyOn(perpetualManualPaper, 'command').mockReturnValue(delayed)
  vi.spyOn(perpetualManualPaper, 'receipt').mockImplementation(() => new Promise(() => {}))
  const guide = vi.mocked(perpetualManualPaper.guide)
  const page = await render()
  try {
    await page.click('准备手动模拟')
    expect(post).toHaveBeenCalledTimes(1)
    const nextValue = { ...value, revision: { ...value.revision!, revision_id: 'perp_revision_new', payload_hash: 'c'.repeat(64) } } as PerpetualAdviceView
    await page.rerender(nextValue, selection)
    const original = post.mock.calls[0][0]
    release({ ...prepared, request_id: original.command.request_id, payload_hash: original.command_hash,
      response: { ...prepared.response, command: original.command } } as unknown as ManualPaperReceipt)
    await settle()
    expect(page.host.querySelector('input[name="amount"]')).toBeNull()
    expect([...page.host.querySelectorAll('button')].some(button => button.textContent === '准备手动模拟')).toBe(true)
    expect(guide).not.toHaveBeenCalled()
  } finally { await page.close() }
})

it('does not revive a stored preparation when opening a newer revision', async () => {
  const original = await makeManualPaperIntent(prepared.response.command)
  await saveManualPaperIntent(original)
  vi.spyOn(perpetualManualPaper, 'receipt').mockResolvedValue(prepared as unknown as ManualPaperReceipt)
  const guide = vi.mocked(perpetualManualPaper.guide)
  const nextValue = { ...value, revision: { ...value.revision!, revision_id: 'perp_revision_new', payload_hash: 'd'.repeat(64) } } as PerpetualAdviceView
  const page = await render(nextValue)
  try {
    expect(page.host.querySelector('input[name="amount"]')).toBeNull()
    expect([...page.host.querySelectorAll('button')].some(button => button.textContent === '准备手动模拟')).toBe(true)
    expect(guide).not.toHaveBeenCalled()
  } finally { await page.close() }
})

it('re-reads a pending guide as fresh without repeating the preparation POST', async () => {
  const freshGuide = { schema_version: 'perpetual-manual-input-guide.v1', owner_id: selection.owner_id, prepare_request_id: 'pc-prepare',
    context: prepared.response.context, target_side: 'long', tick_sz: '0.1', latest_exit_at: prepared.response.protocol!.T1,
    server_time: '2026-10-18T12:01:00Z', quote: { status: 'fresh', sampling_receipt: { ref: 'aqsr_refresh', hash: 'e'.repeat(64) },
      capture_ref: { ref: 'raw_refresh', hash: 'f'.repeat(64) }, bid: '9990', ask: '10010', provider_observed_at: '2026-10-18T12:00:59Z',
      observed_local_lower: '2026-10-18T12:00:59Z', observed_local_upper: '2026-10-18T12:00:59.100Z', received_at: '2026-10-18T12:00:59.100Z',
      expires_at: '2026-10-18T12:01:04Z' } } as any
  const guide = vi.mocked(perpetualManualPaper.guide).mockReset()
    .mockResolvedValueOnce({ ...freshGuide, quote: { status: 'pending' } })
    .mockResolvedValueOnce(freshGuide)
  const post = vi.spyOn(perpetualManualPaper, 'command').mockImplementation(async original => ({ ...prepared,
    request_id: original.command.request_id, payload_hash: original.command_hash,
    response: { ...prepared.response, command: original.command } } as unknown as ManualPaperReceipt))
  const page = await render()
  try {
    await page.click('准备手动模拟')
    expect(page.host.textContent).toContain('暂未捕获')
    expect(post).toHaveBeenCalledTimes(1)
    await page.click('重新读取参考报价')
    expect(page.host.textContent).toContain('买一 9990 USDT')
    expect(guide).toHaveBeenCalledTimes(2)
    expect(post).toHaveBeenCalledTimes(1)
  } finally { await page.close() }
})

it('shows only authenticated settled funding and directs the next account revision flow', async () => {
  const queued = actual.commands.find(row => row.operation === 'perp_manual_intent')!
  const original = await makeManualPaperIntent(queued.response.command)
  await saveManualPaperIntent(original)
  vi.spyOn(perpetualManualPaper, 'receipt').mockResolvedValue(queued as unknown as ManualPaperReceipt)
  const wire = structuredClone(actualEth.execution) as unknown as Record<string, any>
  wire.owner_id = selection.owner_id
  wire.intent = queued.response.intent
  wire.episode = { ...wire.episode, owner_id: selection.owner_id, account_id: selection.partition.account_id,
    funding_status: 'complete_zero_settlements' }
  wire.outcome = { ...wire.outcome, id: 'perp_outcome_settled', version: 2, status: 'settled',
    funding_cash_usdt: '0', net_pnl_usdt: wire.outcome.trade_cash_delta_usdt,
    funding_coverage_ref: { ref: 'perp_funding_coverage', hash: 'a'.repeat(64) },
    gaps: ['mark_risk_path_unavailable'] }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(wire), { status: 200 })))
  const page = await render()
  try {
    expect(page.host.textContent).toContain('已完成资金费覆盖核对')
    expect(page.host.textContent).toContain('实际资金费 0 USDT')
    expect(page.host.textContent).toContain(`净收益 ${wire.outcome.net_pnl_usdt} USDT`)
    expect(page.host.textContent).not.toContain('净收益与胜负保持未知')
    expect(page.host.querySelector('a[href="#/settings"]')?.textContent).toContain('返回账户设置')
    expect(page.host.textContent).toContain('不会沿用旧建议授权')
  } finally { await page.close() }
})

it('separates one-hour entry permission from four-hour holding and rejects expired entry before any POST', async () => {
  const wire = structuredClone(prepared) as unknown as ManualPaperReceipt
  wire.response.protocol!.T1 = '2026-10-18T16:00:00Z'
  const { payload_hash: _protocolHash, ...protocolBody } = wire.response.protocol!
  wire.response.protocol!.payload_hash = await decisionBodyHash(protocolBody)
  wire.response.context!.protocol_ref = { ref: wire.response.protocol!.id, hash: wire.response.protocol!.payload_hash }
  vi.mocked(perpetualManualPaper.guide).mockImplementation(async original => ({
    schema_version: 'perpetual-manual-input-guide.v1', owner_id: selection.owner_id,
    prepare_request_id: original.command.request_id, context: wire.response.context!, target_side: 'long', tick_sz: '0.1',
    latest_entry_at: '2026-10-18T13:00:00Z', latest_exit_at: wire.response.protocol!.T1,
    server_time: '2026-10-18T12:01:00Z', quote: { status: 'pending' },
  }))
  const post = vi.spyOn(perpetualManualPaper, 'command').mockImplementation(async original => {
    const { receipt_hash: _receiptHash, ...body } = { ...wire, request_id: original.command.request_id,
      payload_hash: original.command_hash, response: { ...wire.response, command: original.command } }
    return { ...body, receipt_hash: await decisionBodyHash(body) }
  })
  const page = await render()
  try {
    await page.click('准备手动模拟')
    expect(page.host.querySelector('time[aria-label="新入场许可截止"]')?.getAttribute('datetime')).toBe('2026-10-18T13:00:00Z')
    expect(page.host.querySelector('time[aria-label="持仓最晚退出"]')?.getAttribute('datetime')).toBe('2026-10-18T16:00:00Z')
    expect(page.host.textContent).toContain('本笔模拟计划的盈利概率尚未单独计算')
    await page.click('使用允许的最晚时间')
    expect(new Date((page.host.querySelector('input[name="exit"]') as HTMLInputElement).value).valueOf()).toBe(Date.parse('2026-10-18T16:00:00Z'))
    await act(async () => { input(page.host, 'amount', '80'); input(page.host, 'stop', '9000'); input(page.host, 'take', '12000') })
    await act(async () => (page.host.querySelector('input[type="checkbox"]') as HTMLInputElement).click())
    vi.mocked(Date.now).mockReturnValue(Date.parse('2026-10-18T13:00:00Z'))
    await page.click('确认这笔手动模拟')
    expect(page.host.textContent).toContain('新入场许可已到期')
    expect(post).toHaveBeenCalledTimes(1)
  } finally { await page.close() }
})

it('keeps an existing position and its original execution readable after entry advice expires', async () => {
  const queued = actual.commands.find(row => row.operation === 'perp_manual_intent')!
  const original = await makeManualPaperIntent(queued.response.command)
  await saveManualPaperIntent(original)
  vi.spyOn(perpetualManualPaper, 'receipt').mockResolvedValue(queued as unknown as ManualPaperReceipt)
  const read = vi.spyOn(perpetualManualPaper, 'execution').mockResolvedValue({ schema_version: 'perpetual-manual-execution-view.v1',
    owner_id: selection.owner_id, intent: queued.response.intent, receipt: { status: 'filled' }, fill: null,
    episode: { remaining_contracts: '1' }, outcome: null } as unknown as ManualPaperExecution)
  const post = vi.spyOn(perpetualManualPaper, 'command')
  const page = await render({ ...value, status: 'expired' })
  try {
    expect(page.host.textContent).toContain('按原保护条件等待退出')
    expect(page.host.textContent).toContain('已模拟成交')
    await page.click('读取模拟执行状态')
    expect(read).toHaveBeenCalledTimes(2)
    expect(post).not.toHaveBeenCalled()
  } finally { await page.close() }
})
