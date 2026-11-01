// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import fixture from '../api/decisionSettingsReceiptFixture.json'
import { settingsApi } from '../api/decisionSettings'
import { decisionBodyHash as hash } from '../api/decisionOriginals'
import { decisionSettingsCurrentViewV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { makePerpetualResearchIntent, perpetualResearch, perpetualResearchIntentKey, readPerpetualResearchIntent, savePerpetualResearchIntent, type PerpetualResearchIntent } from '../api/perpetualResearch'
import { PerpetualResearchPanel } from './PerpetualResearchPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: fixture.intent.owner_id, csrf_token: 'signed-session-csrf', expires_at: '2099-01-01T00:00:00Z' })
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear() })
function current(version = 1) {
  return decisionSettingsCurrentViewV1Schema.parse({ schema_version: 'decision-settings-current-view.v1', state_version: version, reevaluation_intent_state: 'pending', scan_generation: version, scan_after_plan_id: null,
    revision: { schema_version: 'decision-settings-revision.v1', revision_id: fixture.receipt.revision.ref, settings_key: fixture.receipt.settings_key, owner_id: fixture.intent.owner_id,
      partition: fixture.intent.command.partition, revision_no: version, parent: null, command_hash: fixture.receipt.command_hash, operation: 'upsert', values: fixture.intent.command.values,
      created_at: fixture.receipt.accepted_at, payload_hash: fixture.receipt.revision.hash } })
}
async function receipt(intent: PerpetualResearchIntent, changed = {}) {
  const currentPaper = String(intent.command.schema_version) === 'perpetual-research-command.v3'
  const body = { schema_version: currentPaper ? 'perpetual-research-receipt.v3' : intent.command.horizon === '4h' ? 'perpetual-research-receipt.v1' : 'perpetual-research-receipt.v2', ...(intent.command.horizon === '4h' && !currentPaper ? {} : { horizon: intent.command.horizon }), ...(currentPaper ? { input_policy: 'saved_preferences_current_paper_ledger.v1', input_binding_ref: { ref: 'account-input-original', hash: 'b'.repeat(64) } } : {}), owner_id: intent.owner_id, request_id: intent.command.request_id, command_hash: await hash(intent.command),
    partition: intent.command.partition, settings_revision: intent.command.expected_settings_revision, event_id: 'accepted-event', study_id: 'accepted-study', run_id: 'actual-run/with?characters',
    admission_ref: { ref: 'admission-original', hash: 'a'.repeat(64) }, opportunity_id: 'accepted-opportunity', accepted_at: '2026-10-19T00:00:00Z', status: 'queued', financial_qualified: false, ...changed }
  return { ...body, payload_hash: await hash(body) }
}
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })

it('explicitly requests current paper ledger v3 while preserving saved preferences and rejecting a legacy receipt', async () => {
  const paper = structuredClone(current())
  paper.revision.partition.account_kind = 'paper'
  paper.revision.values!.account_snapshot.account_kind = 'paper'
  paper.revision.settings_key = 'dsettings_' + await hash([paper.revision.owner_id, paper.revision.partition])
  const { payload_hash: _seal, ...body } = paper.revision
  paper.revision.payload_hash = await hash(body)
  const original = await makePerpetualResearchIntent(paper)
  expect(original.command).toMatchObject({ schema_version: 'perpetual-research-command.v3', input_policy: 'saved_preferences_current_paper_ledger.v1' })
  expect(original.command.expected_settings_revision).toEqual({ ref: paper.revision.revision_id, hash: paper.revision.payload_hash })
  await savePerpetualResearchIntent(original)
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    expect(JSON.parse(String(init?.body))).toEqual(original.command)
    return new Response(JSON.stringify(await receipt(original)))
  })
  vi.stubGlobal('fetch', fetcher)
  expect((await perpetualResearch.command(original)).schema_version).toBe('perpetual-research-receipt.v3')
  expect(fetcher).toHaveBeenCalledTimes(1)
  const legacy = await receipt(original, { schema_version: 'perpetual-research-receipt.v1' })
  delete (legacy as Record<string, unknown>).horizon
  delete (legacy as Record<string, unknown>).input_policy
  delete (legacy as Record<string, unknown>).input_binding_ref
  const { payload_hash: _legacySeal, ...legacyBody } = legacy
  legacy.payload_hash = await hash(legacyBody)
  fetcher.mockImplementation(async () => new Response(JSON.stringify(legacy)))
  await expect(perpetualResearch.receipt(original)).rejects.toThrow()
})

it('recovers legacy pending intent bytes without upgrading its account policy or submitting a new request', async () => {
  const original = await makePerpetualResearchIntent(current())
  expect(original.command.schema_version).toBe('perpetual-research-command.v1')
  const bytes = JSON.stringify(original)
  localStorage.setItem(perpetualResearchIntentKey(original.owner_id), bytes)
  const recovered = await readPerpetualResearchIntent(original.owner_id, original.command.partition)
  expect(recovered).toEqual(original)
  const fetcher = vi.fn(async () => new Response(JSON.stringify(await receipt(original))))
  vi.stubGlobal('fetch', fetcher)
  expect((await perpetualResearch.receipt(recovered!)).schema_version).toBe('perpetual-research-receipt.v1')
  expect(localStorage.getItem(perpetualResearchIntentKey(original.owner_id))).toBe(bytes)
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe('GET')
})
async function render(view: ReturnType<typeof current> | null = current(), horizon: '4h' | '24h' | '72h' = '4h') {
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const draw = async (next: ReturnType<typeof current> | null) => { await act(async () => root.render(<PerpetualResearchPanel current={next} horizon={horizon} />)); await settle() }
  await draw(view)
  return { host, draw, click: async (text: string) => { const button = [...host.querySelectorAll('button')].find(item => item.textContent === text); expect(button).toBeDefined(); await act(async () => button!.click()); await settle() }, close: async () => { await act(async () => root.unmount()); host.remove() } }
}

it('requires saved settings and persists the exact intent before one signed POST, with queued run navigation only', async () => {
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const intent = await readPerpetualResearchIntent(fixture.intent.owner_id)
    expect(intent).not.toBeNull()
    expect(JSON.parse(String(init?.body))).toEqual(intent!.command)
    expect(new Headers(init?.headers).get('X-Settings-CSRF')).toBe('signed-session-csrf')
    expect(new Headers(init?.headers).get('X-Owner-Id')).toBeNull()
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe(intent!.command.request_id)
    return new Response(JSON.stringify(await receipt(intent!)), { status: 200 })
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render(null)
  try {
    expect(page.host.textContent).toContain('先保存')
    expect((page.host.querySelector('button[data-research-start]') as HTMLButtonElement).disabled).toBe(true)
    await page.draw(current())
    await page.click('研究 BTC 4 小时方案')
    expect(fetcher).toHaveBeenCalledTimes(1)
    const original = await readPerpetualResearchIntent(fixture.intent.owner_id)
    expect(original!.command.partition).toEqual(fixture.intent.command.partition)
    expect(original!.command.expected_settings_revision).toEqual(fixture.receipt.revision)
    expect(original!.command.expected_settings_state_version).toBe(1)
    expect(page.host.textContent).toContain('研究请求已接受')
    expect(page.host.textContent).toContain('不表示报告或预测已经完成')
    expect(page.host.querySelector('a')?.getAttribute('href')).toBe('#/progress?run=actual-run%2Fwith%3Fcharacters&back=%23%2Fsettings')
  } finally { await page.close() }
})

it('recovers a timed out original after reopen using GET only and marks a changed saved revision explicitly', async () => {
  const post = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') throw new TypeError('connection lost')
    return new Response(JSON.stringify({ detail: 'perpetual_research_receipt_missing' }), { status: 404 })
  })
  vi.stubGlobal('fetch', post)
  const first = await render()
  await first.click('研究 BTC 4 小时方案')
  const original = await readPerpetualResearchIntent(fixture.intent.owner_id)
  await first.close()
  expect(original).not.toBeNull()
  const get = vi.fn(async (_url: unknown, init?: RequestInit) => {
    expect(init?.method ?? 'GET').toBe('GET')
    return new Response(JSON.stringify(await receipt(original!)), { status: 200 })
  })
  vi.stubGlobal('fetch', get)
  const next = current()
  next.state_version = 2; next.revision.revision_no = 2
  next.revision.revision_id = 'new-saved-original'; next.revision.payload_hash = 'b'.repeat(64)
  const reopened = await render(next)
  try {
    expect(get.mock.calls[0][0]).toBe('/v1/decision-settings/research/commands/' + encodeURIComponent(original!.command.request_id))
    expect(post.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET'])
    expect(get).toHaveBeenCalledTimes(1)
    expect(reopened.host.textContent).toContain('原研究使用的是另一份已保存设置')
    expect(reopened.host.querySelector('a')?.getAttribute('href')).toContain('actual-run%2Fwith%3Fcharacters')
    expect((await readPerpetualResearchIntent(fixture.intent.owner_id))!.command).toEqual(original!.command)
  } finally { await reopened.close() }
})

it('rejects rehashed foreign, substituted-settings and changed-command receipts instead of accepting their run IDs', async () => {
  const original = await makePerpetualResearchIntent(current())
  await savePerpetualResearchIntent(original)
  for (const change of [{ owner_id: 'foreign' }, { request_id: 'other-request' }, { partition: { ...original.command.partition, account_id: 'other-account' } },
    { settings_revision: { ...original.command.expected_settings_revision, hash: 'f'.repeat(64) } }, { command_hash: 'c'.repeat(64) }]) {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(await receipt(original, change)), { status: 200 })))
    await expect(perpetualResearch.receipt(original)).rejects.toThrow('perpetual_research_receipt_identity_mismatch')
  }
})

it('preserves a timed out selected 24h command across 72h switch and recovers only its original GET', async () => {
  const legacy = await makePerpetualResearchIntent(current())
  await savePerpetualResearchIntent(legacy)
  const post = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'GET') return new Response(JSON.stringify({ detail: 'perpetual_research_receipt_missing' }), { status: 404 })
    expect(JSON.parse(String(init?.body)).horizon).toBe('24h')
    throw new TypeError('connection lost')
  })
  vi.stubGlobal('fetch', post)
  const page24 = await render(current(), '24h')
  await page24.click('研究 BTC 24 小时方案')
  await page24.close()
  expect(post.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET'])
  const original = await readPerpetualResearchIntent(fixture.intent.owner_id, current().revision.partition, '24h')
  expect(original?.command.schema_version).toBe('perpetual-research-command.v2')
  expect((await readPerpetualResearchIntent(fixture.intent.owner_id))?.command).toEqual(legacy.command)
  const page72 = await render(current(), '72h')
  expect(page72.host.textContent).not.toContain('结果待确认')
  await page72.close()
  expect(post.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET'])
  const get = vi.fn(async (_url: unknown, init?: RequestInit) => {
    expect(init?.method ?? 'GET').toBe('GET')
    return new Response(JSON.stringify(await receipt(original!)))
  })
  vi.stubGlobal('fetch', get)
  const reopened = await render(current(), '24h')
  try {
    expect(get).toHaveBeenCalledTimes(1)
    expect(reopened.host.textContent).toContain('研究请求已接受')
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(await receipt(original!, { horizon: '72h' }))))
    await expect(perpetualResearch.receipt(original!)).rejects.toThrow('perpetual_research_receipt_identity_mismatch')
  } finally { await reopened.close() }
})

it('retains an old unindexed V1 intent when a different account saves a new 4h request', async () => {
  const legacy = await makePerpetualResearchIntent(current())
  if (legacy.command.schema_version !== 'perpetual-research-command.v1') throw new Error('legacy V1 fixture required')
  localStorage.setItem(perpetualResearchIntentKey(legacy.owner_id), JSON.stringify(legacy))
  const command = { ...legacy.command, request_id: 'other-account-request', partition: { ...legacy.command.partition, account_id: 'other-account' } }
  await savePerpetualResearchIntent({ ...legacy, command, command_hash: await hash(command) })
  expect((await readPerpetualResearchIntent(legacy.owner_id, legacy.command.partition, '4h'))?.command).toEqual(legacy.command)
})


it('explicitly starts a new same-settings research while retaining old receipt; uncertain requests and new owners stay isolated', async () => {
  const originals: PerpetualResearchIntent[] = []
  const fetcher = vi.fn(async (url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const command = JSON.parse(String(init.body))
      const original = { owner_id: fixture.intent.owner_id, command, command_hash: await hash(command) } as PerpetualResearchIntent
      originals.push(original)
      if (originals.length === 2) return new Response(JSON.stringify({detail:'study_research_active'}),{status:409})
      if (originals.length === 4) throw new TypeError('uncertain new request')
      return new Response(JSON.stringify(await receipt(original)))
    }
    const original = originals.find(item => String(url).endsWith(encodeURIComponent(item.command.request_id)))!
    if (original === originals[3]) return new Response(JSON.stringify({ detail: 'perpetual_research_receipt_missing' }), { status: 404 })
    return new Response(JSON.stringify(await receipt(original)))
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render()
  try {
    await page.click('研究 BTC 4 小时方案')
    const oldBytes = localStorage.getItem(perpetualResearchIntentKey(fixture.intent.owner_id))!
    expect(page.host.textContent).toContain('将发起一次新研究')
    await page.click('重新研究当前已保存设置')
    expect(originals).toHaveLength(2)
    expect(originals[1].command.request_id).not.toBe(originals[0].command.request_id)
    expect(originals[1].command.expected_settings_revision).toEqual(originals[0].command.expected_settings_revision)
    expect(Array.from({length:localStorage.length},(_,i)=>localStorage.getItem(localStorage.key(i)!)).some(raw => raw?.includes(JSON.stringify(oldBytes)))).toBe(true)
    expect(page.host.textContent).toContain('服务端已明确拒绝')
    expect(page.host.textContent).toContain('重新研究当前已保存设置')
    await page.click('核对历史回执 1')
    expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/v1/decision-settings/research/commands/' + originals[0].command.request_id)
    await page.click('重新研究当前已保存设置')
    expect(originals).toHaveLength(3)
    await page.click('重新研究当前已保存设置')
    expect(originals).toHaveLength(4)
    expect(page.host.textContent).not.toContain('重新研究当前已保存设置')
    await page.click('核对历史回执 1')
    expect(page.host.textContent).not.toContain('重新研究当前已保存设置')
    vi.mocked(settingsApi.session).mockReturnValue({owner_id:'foreign-owner',csrf_token:'foreign',expires_at:'2099-01-01T00:00:00Z'})
    await act(async()=>{ window.dispatchEvent(new Event('decision-hub-settings-session-changed')) }); await settle()
    expect(page.host.textContent).not.toContain(originals[0].command.request_id)
    expect(originals).toHaveLength(4)
  } finally { await page.close() }
})

it('shows the automatically recovered original receipt after a lost POST response without another submit', async () => {
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') throw new TypeError('lost admission response')
    const original = await readPerpetualResearchIntent(fixture.intent.owner_id)
    return new Response(JSON.stringify(await receipt(original!)))
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render()
  try {
    await page.click('研究 BTC 4 小时方案')
    const original = await readPerpetualResearchIntent(fixture.intent.owner_id)
    expect(fetcher.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET'])
    expect(fetcher.mock.calls[1][0]).toBe('/v1/decision-settings/research/commands/' + original!.command.request_id)
    expect(page.host.textContent).toContain('研究请求已接受')
    expect(page.host.textContent).not.toContain('结果尚未确认')
    expect(page.host.querySelector('a')?.getAttribute('href')).toContain('actual-run%2Fwith%3Fcharacters')
  } finally { await page.close() }
})

it('only unlocks an unknown original after durable closure; unknown close stays locked and accepted close restores the original', async () => {
  const original = await makePerpetualResearchIntent(current())
  await savePerpetualResearchIntent(original)
  const oldBytes=localStorage.getItem(perpetualResearchIntentKey(original.owner_id))!
  let outcome: 'unknown' | 'accepted' | 'closed' = 'unknown'
  let newPosts = 0
  const fetcher = vi.fn(async (url: unknown, init?: RequestInit) => {
    if (String(url).endsWith('/close')) {
      expect(JSON.parse(String(init?.body))).toEqual(original.command)
      if (outcome === 'unknown') throw new TypeError('lost closure response')
      if (outcome === 'accepted') return new Response(JSON.stringify(await receipt(original)))
      const body = {schema_version:'perpetual-research-closure-receipt.v1',owner_id:original.owner_id,request_id:original.command.request_id,
        command_hash:original.command_hash,command:original.command,status:'closed_unaccepted',reason_code:'owner_closed_unaccepted',closed_at:'2026-10-19T00:00:00Z'}
      return new Response(JSON.stringify({...body,payload_hash:await hash(body)}))
    }
    if (init?.method === 'POST') {
      newPosts++
      const command = JSON.parse(String(init.body))
      expect(command.request_id).not.toBe(original.command.request_id)
      return new Response(JSON.stringify(await receipt({owner_id:original.owner_id,command,command_hash:await hash(command)})))
    }
    return new Response(JSON.stringify({detail:'perpetual_research_receipt_missing'}),{status:404})
  })
  vi.stubGlobal('fetch',fetcher)
  const page = await render()
  try {
    expect(page.host.textContent).not.toContain('重新研究当前已保存设置')
    await page.click('关闭未受理的原请求')
    expect(page.host.textContent).not.toContain('重新研究当前已保存设置')
    expect(newPosts).toBe(0)
    expect(Array.from({length:localStorage.length},(_,i)=>localStorage.getItem(localStorage.key(i)!)).some(raw=>raw?.includes('"operation":"close"'))).toBe(true)
    outcome='accepted'
    await page.click('关闭未受理的原请求')
    expect(page.host.textContent).toContain('研究请求已接受')
    expect(page.host.querySelector('a')?.getAttribute('href')).toContain('actual-run')
  } finally { await page.close() }
  outcome='closed'
  const reopened = await render()
  try {
    await reopened.click('关闭未受理的原请求')
    expect(reopened.host.textContent).toContain('服务端已关闭未受理的原请求')
    await reopened.click('重新研究当前已保存设置')
    expect(newPosts).toBe(1)
    expect(Array.from({length:localStorage.length},(_,i)=>localStorage.getItem(localStorage.key(i)!)).some(raw=>raw?.includes(JSON.stringify(oldBytes)))).toBe(true)
  } finally { await reopened.close() }
})


it('keeps historical research collapsed and recovers the exact original identity after expansion', async () => {
  const command = { schema_version: 'perpetual-research-command.v1' as const, request_id: crypto.randomUUID(), partition: current().revision.partition,
    expected_settings_revision: fixture.receipt.revision, expected_settings_state_version: 1, horizon: '4h' as const }
  const original = { owner_id: fixture.intent.owner_id, command, command_hash: await hash(command) }
  await savePerpetualResearchIntent(original)
  const nextCommand = { ...command, request_id: crypto.randomUUID() }
  const next = { owner_id: original.owner_id, command: nextCommand, command_hash: await hash(nextCommand) }
  await savePerpetualResearchIntent(next)
  const fetcher = vi.fn(async (url: unknown) => new Response(JSON.stringify(await receipt(String(url).endsWith(original.command.request_id) ? original : next))))
  vi.stubGlobal('fetch', fetcher)
  const page = await render()
  try {
    const history = page.host.querySelector<HTMLDetailsElement>('details[data-research-history]')
    expect(history).not.toBeNull()
    expect(history!.open).toBe(false)
    expect(history!.querySelector('summary')?.textContent).toBe('历史研究回执（1）')
    await act(async () => history!.querySelector('summary')!.click())
    expect(history!.open).toBe(true)
    expect(history!.textContent).toContain(original.command.request_id)
    await page.click('核对历史回执 1')
    expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/v1/decision-settings/research/commands/' + original.command.request_id)
    expect(await readPerpetualResearchIntent(original.owner_id, original.command.partition)).toEqual(next)
    expect(page.host.textContent).toContain('历史请求原回执已确认')
  } finally { await page.close() }
})

const realSetTimeout = globalThis.setTimeout
const recoveryFlush = () => act(async () => { await new Promise(resolve => realSetTimeout(resolve, 20)) })
const recoveryAdvance = async (milliseconds: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds) }); await recoveryFlush() }
const missingReceipt = () => new Response(JSON.stringify({ detail: 'perpetual_research_receipt_missing' }), { status: 404 })
const intentBytes = () => Object.fromEntries(Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)!)
  .filter(key => key.startsWith('decision-hub:perpetual-research-intent.')).map(key => [key, localStorage.getItem(key)]))
async function recoveryPage(view = current(), horizon: '4h' | '24h' = '4h') {
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  await act(async () => root.render(<PerpetualResearchPanel current={view} horizon={horizon} />)); await recoveryFlush()
  return { host, click: async (text: string) => {
    const button = [...host.querySelectorAll('button')].find(item => item.textContent === text)
    expect(button).toBeDefined(); expect(button!.disabled).toBe(false)
    await act(async () => button!.click()); await recoveryFlush()
  }, close: async () => { await act(async () => root.unmount()); host.remove() } }
}

it('automatic original receipt recovery retries missing after real POST timeout without resubmitting v3', async () => {
  const paper = structuredClone(current())
  paper.revision.partition.account_kind = 'paper'; paper.revision.values!.account_snapshot.account_kind = 'paper'
  paper.revision.settings_key = 'dsettings_' + await hash([paper.revision.owner_id, paper.revision.partition])
  const { payload_hash: _oldSeal, ...body } = paper.revision; paper.revision.payload_hash = await hash(body)
  let gets = 0
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') return new Promise<Response>((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
    })
    gets++
    const original = await readPerpetualResearchIntent(fixture.intent.owner_id, paper.revision.partition)
    return gets < 3 ? missingReceipt() : new Response(JSON.stringify(await receipt(original!)))
  })
  vi.stubGlobal('fetch', fetcher); vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const page = await recoveryPage(paper)
  try {
    await page.click('研究 BTC 4 小时方案')
    const before = intentBytes(), original = await readPerpetualResearchIntent(fixture.intent.owner_id, paper.revision.partition)
    expect(original!.command.schema_version).toBe('perpetual-research-command.v3')
    await recoveryAdvance(30_000)
    expect(gets).toBe(1); expect(page.host.textContent).not.toContain('研究请求已接受')
    expect(page.host.textContent).toContain('正在自动核对原研究回执'); expect(page.host.textContent).not.toContain('HTTP 404')
    await recoveryAdvance(1_000); expect(gets).toBe(2)
    await recoveryAdvance(2_000); expect(gets).toBe(3)
    expect(page.host.textContent).toContain('研究请求已接受')
    expect(fetcher.mock.calls.map(([, init]) => init?.method)).toEqual(['POST', 'GET', 'GET', 'GET'])
    expect(fetcher.mock.calls.slice(1).every(([url]) => url === '/v1/decision-settings/research/commands/' + original!.command.request_id)).toBe(true)
    expect(intentBytes()).toEqual(before)
    await recoveryAdvance(60_000); expect(gets).toBe(3)
  } finally { await page.close() }
})

it.each(['4h', '24h'] as const)('automatic original receipt recovery resumes exact legacy %s bytes after hiding and remount', async horizon => {
  const original = await makePerpetualResearchIntent(current(), horizon)
  await savePerpetualResearchIntent(original)
  const before = intentBytes(); let gets = 0
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    expect(init?.method).toBe('GET'); gets++
    return gets < 3 ? missingReceipt() : new Response(JSON.stringify(await receipt(original)))
  })
  vi.stubGlobal('fetch', fetcher); vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const first = await recoveryPage(current(), horizon)
  expect(gets).toBe(1)
  visibility.mockReturnValue('hidden'); await act(async () => document.dispatchEvent(new Event('visibilitychange')))
  await recoveryAdvance(60_000); expect(gets).toBe(1)
  await first.close(); visibility.mockReturnValue('visible')
  const reopened = await recoveryPage(current(), horizon)
  try {
    expect(gets).toBe(2); await recoveryAdvance(1_000)
    expect(gets).toBe(3); expect(reopened.host.textContent).toContain('研究请求已接受')
    expect(intentBytes()).toEqual(before)
    expect((await readPerpetualResearchIntent(original.owner_id, original.command.partition, horizon))!.command.schema_version)
      .toBe(horizon === '4h' ? 'perpetual-research-command.v1' : 'perpetual-research-command.v2')
  } finally { await reopened.close() }
})

it('automatic original receipt recovery stops on 403 and resumes only after explicit same-owner reconnect', async () => {
  const original = await makePerpetualResearchIntent(current()); await savePerpetualResearchIntent(original)
  let allowed = false
  const fetcher = vi.fn(async () => allowed ? new Response(JSON.stringify(await receipt(original)))
    : new Response(JSON.stringify({ detail: 'permission_denied' }), { status: 403 }))
  vi.stubGlobal('fetch', fetcher); vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const page = await recoveryPage()
  try {
    expect(page.host.textContent).toContain('重新连接本机身份')
    await recoveryAdvance(60_000); expect(fetcher).toHaveBeenCalledTimes(1)
    await page.click('核对原研究回执'); expect(fetcher).toHaveBeenCalledTimes(1)
    allowed = true
    vi.mocked(settingsApi.session).mockReturnValue({ owner_id: original.owner_id, csrf_token: 'explicit-reconnected-session', expires_at: '2099-01-01T00:00:00Z' })
    await act(async () => window.dispatchEvent(new Event('decision-hub-settings-session-changed'))); await recoveryFlush()
    expect(fetcher).toHaveBeenCalledTimes(2); expect(page.host.textContent).toContain('研究请求已接受')
  } finally { await page.close() }
})

it('automatic original receipt recovery ignores old identity in-flight response and does not retry tampered receipts', async () => {
  const original = await makePerpetualResearchIntent(current()); await savePerpetualResearchIntent(original)
  const stale = await receipt(original), tampered = await receipt(original, { request_id: 'foreign-request' })
  let resolveOld: (response: Response) => void = () => { throw new Error('GET not started') }
  const fetcher = vi.fn(async () => new Promise<Response>(resolve => { resolveOld = resolve }))
  vi.stubGlobal('fetch', fetcher); vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const page = await recoveryPage()
  try {
    vi.mocked(settingsApi.session).mockReturnValue({ owner_id: 'foreign-owner', csrf_token: 'foreign-session', expires_at: '2099-01-01T00:00:00Z' })
    await act(async () => window.dispatchEvent(new Event('decision-hub-settings-session-changed'))); await recoveryFlush()
    resolveOld(new Response(JSON.stringify(stale))); await recoveryFlush()
    expect(page.host.textContent).not.toContain('研究请求已接受'); expect(page.host.querySelector('a')).toBeNull()
    fetcher.mockImplementation(async () => new Response(JSON.stringify(tampered)))
    vi.mocked(settingsApi.session).mockReturnValue({ owner_id: original.owner_id, csrf_token: 'explicit-reconnect', expires_at: '2099-01-01T00:00:00Z' })
    await act(async () => window.dispatchEvent(new Event('decision-hub-settings-session-changed'))); await recoveryFlush()
    expect(page.host.textContent).toContain('回执与原研究请求不一致')
    const calls = fetcher.mock.calls.length; await recoveryAdvance(60_000)
    expect(fetcher).toHaveBeenCalledTimes(calls); expect(page.host.querySelector('a')).toBeNull()
  } finally { await page.close() }
})

it('automatic original receipt recovery serializes an explicit close and never closes automatically', async () => {
  const original = await makePerpetualResearchIntent(current()); await savePerpetualResearchIntent(original)
  const body = { schema_version: 'perpetual-research-closure-receipt.v1', owner_id: original.owner_id, request_id: original.command.request_id,
    command_hash: original.command_hash, command: original.command, status: 'closed_unaccepted', reason_code: 'owner_closed_unaccepted', closed_at: '2026-10-19T00:00:00Z' }
  const closure = { ...body, payload_hash: await hash(body) }
  let resolveClose: (response: Response) => void = () => { throw new Error('close not started') }
  const fetcher = vi.fn(async (url: unknown, init?: RequestInit) => {
    if (String(url).endsWith('/close')) {
      expect(init?.method).toBe('POST'); expect(JSON.parse(String(init?.body))).toEqual(original.command)
      return new Promise<Response>(resolve => { resolveClose = resolve })
    }
    expect(init?.method).toBe('GET'); return missingReceipt()
  })
  vi.stubGlobal('fetch', fetcher); vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const page = await recoveryPage()
  try {
    expect(fetcher).toHaveBeenCalledTimes(1)
    await page.click('关闭未受理的原请求')
    await recoveryAdvance(20_000); expect(fetcher).toHaveBeenCalledTimes(2)
    resolveClose(new Response(JSON.stringify(closure))); await recoveryFlush()
    expect(page.host.textContent).toContain('服务端已关闭未受理的原请求')
    await recoveryAdvance(60_000); expect(fetcher).toHaveBeenCalledTimes(2)
  } finally { await page.close() }
})
