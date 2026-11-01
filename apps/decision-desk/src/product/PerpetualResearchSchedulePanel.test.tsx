// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import originals from '../api/perpetualResearchScheduleReceiptFixture.json'
import settingsFixture from '../api/decisionSettingsReceiptFixture.json'
import { settingsApi } from '../api/decisionSettings'
import { decisionBodyHash as hash } from '../api/decisionOriginals'
import { perpetualResearchSchedules, readScheduleIntent, type ScheduleIntent } from '../api/perpetualResearchSchedules'
import { decisionSettingsCurrentViewV1Schema, decisionSettingsPartitionV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { PerpetualResearchSchedulePanel } from './PerpetualResearchSchedulePanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const actual = originals.find(value => value.command.operation === 'enable')!
const partition = decisionSettingsPartitionV1Schema.parse(actual.command.partition)
const paperPartition = decisionSettingsPartitionV1Schema.parse({ ...partition, account_kind: 'paper', account_id: 'B-paper-account' })
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: actual.owner_id, csrf_token: 'signed-csrf', expires_at: '2099-01-01T00:00:00Z' })
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })
function view(receipt: unknown = null, selected = partition) {
  const value = receipt as typeof actual | null
  return { schema_version: 'perpetual-research-schedule-view.v1', owner_id: actual.owner_id, partition: selected, horizon: '24h', state: value?.state ?? 'not_enabled',
    state_version: value?.state_version ?? 0, revision: value?.revision ?? null, next_due_at: value?.accepted_at ?? null, last_slot: null, missed_slots: 0, receipt }
}
function current(selected = partition) {
  return decisionSettingsCurrentViewV1Schema.parse({ schema_version: 'decision-settings-current-view.v1', state_version: 1, reevaluation_intent_state: 'pending', scan_generation: 1, scan_after_plan_id: null,
    revision: { schema_version: 'decision-settings-revision.v1', revision_id: settingsFixture.receipt.revision.ref, settings_key: settingsFixture.receipt.settings_key, owner_id: actual.owner_id,
      partition: selected, revision_no: 1, parent: null, command_hash: settingsFixture.receipt.command_hash, operation: 'upsert',
      values: { ...settingsFixture.intent.command.values, account_snapshot: { ...settingsFixture.intent.command.values.account_snapshot, account_kind: selected.account_kind, account_id: selected.account_id } },
      created_at: settingsFixture.receipt.accepted_at, payload_hash: settingsFixture.receipt.revision.hash } })
}
async function receipt(original: ScheduleIntent) {
  const body = { schema_version: original.command.schema_version === 'perpetual-research-schedule-command.v1' ? 'perpetual-research-schedule-receipt.v1' : 'perpetual-research-schedule-receipt.v2', owner_id: original.owner_id, request_id: original.command.request_id, command: original.command,
    command_hash: original.command_hash, schedule_id: 'perpschedule_' + await hash([original.owner_id, original.command.partition, '24h']), state_version: original.command.expected_state_version + 1,
    state: original.command.operation === 'enable' ? 'active' : 'paused', accepted_at: actual.accepted_at }
  const digest = await hash(body)
  return { ...body, payload_hash: digest, revision: { ref: 'perpschedulecmd_' + await hash([original.owner_id, original.command.request_id]), hash: digest } }
}
const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
async function render(selected = partition) {
  const host = document.createElement('div'); document.body.append(host); const root = createRoot(host)
  await act(async () => root.render(<PerpetualResearchSchedulePanel current={current(selected)} horizon="24h" />)); await settle()
  return { host, click: async (label: string) => { const button = [...host.querySelectorAll('button')].find(item => item.textContent === label); expect(button).toBeDefined(); await act(async () => button!.click()); await settle() },
    close: async () => { await act(async () => root.unmount()); host.remove() } }
}
it('authenticates the actual signed API schedule originals and rejects another horizon', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(view(actual)), { status: 200 })))
  expect((await perpetualResearchSchedules.current(partition, '24h')).revision).toEqual(actual.revision)
  await expect(perpetualResearchSchedules.current(partition, '72h')).rejects.toThrow('perpetual_schedule_view_mismatch')
})
it('requires a click then stores the complete command before signed enable and pause', async () => {
  let saved: unknown = null
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method !== 'POST') return new Response(JSON.stringify(view(saved, paperPartition)), { status: 200 })
    const original = await readScheduleIntent(paperPartition, '24h'); expect(original).not.toBeNull()
    expect(JSON.parse(String(init.body))).toEqual(original!.command)
    expect(new Headers(init.headers).get('X-Settings-CSRF')).toBe('signed-csrf')
    expect(new Headers(init.headers).get('X-Owner-Id')).toBeNull()
    saved = await receipt(original!); return new Response(JSON.stringify(saved), { status: 200 })
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render(paperPartition)
  try {
    expect(fetcher.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true)
    await page.click('启用此期限周期研究')
    const enabled = JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body))
    expect(enabled.schema_version).toBe('perpetual-research-schedule-command.v2')
    expect(enabled.input_policy).toBe('saved_preferences_current_paper_ledger.v1')
    expect(page.host.textContent).toContain('当前模拟账户账本')
    expect(page.host.textContent).toContain('周期研究已启用')
    await page.click('暂停此期限周期研究')
    expect(page.host.textContent).toContain('周期研究已暂停')
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(2)
    expect(page.host.textContent).toContain('不会自动下单')
  } finally { await page.close() }
})
it('reopens an uncertain original through GET only and permits an explicit identical retry', async () => {
  // An uncertain pre-upgrade intent must retain its original v1 command bytes.
  localStorage.setItem(`decision-hub:periodic-research-intent.v1:${actual.owner_id}:${encodeURIComponent(JSON.stringify(partition, Object.keys(partition).sort()))}:24h`, JSON.stringify({ owner_id: actual.owner_id, command: actual.command, command_hash: actual.command_hash }))
  const original = (await readScheduleIntent(partition, '24h'))!
  const fetcher = vi.fn(async (url: unknown, init?: RequestInit) => {
    if (String(url).includes('/current?')) return new Response(JSON.stringify(view()), { status: 200 })
    if (init?.method === 'POST') {
      expect(JSON.parse(String(init.body))).toEqual(original.command)
      return new Response(JSON.stringify(await receipt(original)), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: { error_code: 'not_found' } }), { status: 404 })
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render()
  try {
    expect(fetcher.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true)
    expect(await readScheduleIntent(partition, '24h')).toEqual(original)
    await page.click('重试同一周期设置')
    expect(JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body)).schema_version).toBe('perpetual-research-schedule-command.v1')
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(await readScheduleIntent(partition, '24h')).toBeNull()
  } finally { await page.close() }
})

it('refreshes an active slot and shows its failed Run without another command', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  let advanced = false
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') throw new Error('unexpected_command')
    return new Response(JSON.stringify({ ...view(actual), last_slot: advanced ? {
      request_id: 'riskresearch_auto', status: 'queued', run_id: 'run_auto',
      run_status: 'failed', error_code: 'research_method_binding_failed',
      protocol_version: 'risk-research-request.perpetual-motives.v2',
    } : null }), { status: 200 })
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render()
  try {
    expect(page.host.querySelector('a')).toBeNull()
    advanced = true
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) }); await settle()
    expect(page.host.querySelector('a')?.getAttribute('href')).toBe('#/progress?run=run_auto')
    expect(page.host.textContent).toContain('本次研究失败')
    expect(fetcher.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true)
  } finally { await page.close() }
})

it('keeps the last readable Run when an explicit refresh fails', async () => {
  let offline = false
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') throw new Error('unexpected_command')
    if (offline) throw new Error('network_unavailable')
    return new Response(JSON.stringify({ ...view(actual), last_slot: {
      request_id: 'riskresearch_auto', status: 'queued', run_id: 'run_auto',
      run_status: 'running', error_code: null,
      protocol_version: 'risk-research-request.perpetual-motives.v2',
    } }), { status: 200 })
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render()
  try {
    offline = true
    await page.click('刷新周期研究状态')
    expect(page.host.querySelector('a')?.getAttribute('href')).toBe('#/progress?run=run_auto')
    expect(page.host.textContent).toContain('上次读取结果已保留')
    expect(fetcher.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true)
  } finally { await page.close() }
})

it('does not replace a confirmed pause with an older polling response', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  let saved: unknown = actual, reads = 0, oldReadStarted = false
  let releaseOld!: () => void
  const oldRead = new Promise<void>(resolve => { releaseOld = resolve })
  vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') {
      saved = await receipt((await readScheduleIntent(partition, '24h'))!)
      return new Response(JSON.stringify(saved), { status: 200 })
    }
    reads += 1
    if (reads === 2) {
      oldReadStarted = true; await oldRead
      return new Response(JSON.stringify(view(actual)), { status: 200 })
    }
    return new Response(JSON.stringify(view(saved)), { status: 200 })
  }))
  const page = await render()
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) }); await settle()
    expect(oldReadStarted).toBe(true)
    await page.click('暂停此期限周期研究')
    expect(page.host.textContent).toContain('周期研究已暂停')
    await act(async () => { releaseOld() }); await settle()
    expect(page.host.textContent).toContain('周期研究已暂停')
    expect(page.host.textContent).not.toContain('周期研究已启用')
  } finally { releaseOld(); await page.close() }
})

it('does not replace a confirmed enable with a delayed paused bootstrap', async () => {
  const pausedCommand: ScheduleIntent['command'] = { ...actual.command,
    schema_version: 'perpetual-research-schedule-command.v2', partition: { ...paperPartition, account_kind: 'paper' },
    horizon: '24h', interval_seconds: 3600, settings_binding_policy: 'follow_current_saved_head',
    input_policy: 'saved_preferences_current_paper_ledger.v1', operation: 'pause', request_id: 'bootstrap-paused' }
  const paused = await receipt({ owner_id: actual.owner_id, command: pausedCommand, command_hash: await hash(pausedCommand) })
  let saved: unknown = paused, reads = 0
  let releaseBootstrap!: () => void
  const bootstrap = new Promise<void>(resolve => { releaseBootstrap = resolve })
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') {
      saved = await receipt((await readScheduleIntent(paperPartition, '24h'))!)
      return new Response(JSON.stringify(saved), { status: 200 })
    }
    if (++reads === 1) {
      await bootstrap
      return new Response(JSON.stringify(view(paused, paperPartition)), { status: 200 })
    }
    return new Response(JSON.stringify(view(saved, paperPartition)), { status: 200 })
  })
  vi.stubGlobal('fetch', fetcher)
  const page = await render(paperPartition)
  try {
    await page.click('刷新周期研究状态')
    expect(page.host.textContent).toContain('周期研究已暂停')
    await page.click('启用此期限周期研究')
    expect(page.host.textContent).toContain('周期研究已启用')
    await act(async () => { releaseBootstrap() }); await settle()
    expect(page.host.textContent).toContain('周期研究已启用')
    expect(page.host.textContent).not.toContain('周期研究已暂停')
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(await readScheduleIntent(paperPartition, '24h')).toBeNull()
  } finally { releaseBootstrap(); await page.close() }
})
