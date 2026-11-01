// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import fixture from '../../../../artifacts/d2-perp-advice-ui/pure-presentation-fixture.json'
import { settingsApi } from '../api/decisionSettings'
import { decisionBodyHash as hash } from '../api/decisionOriginals'
import type { PerpetualAdviceSelection, PerpetualAdviceView } from '../api/perpetualAdvice'
import { planWatchApi } from '../api/perpetualPlanWatch'
import { continuousWatchFixture } from '../api/perpetualPlanWatch.test-support'
import { PerpetualPlanWatchPanel } from './PerpetualPlanWatchPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('keeps once consent separate and persists explicit continuous opt-in, pause and restarted readback', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const selection: PerpetualAdviceSelection = { ...fixture.selection as PerpetualAdviceSelection,
    partition: { ...(fixture.selection as PerpetualAdviceSelection).partition, account_kind: 'paper', account_id: 'B-watch-paper' } }
  const originalValue = fixture.value as unknown as PerpetualAdviceView
  const value: PerpetualAdviceView = { ...originalValue, partition: selection.partition,
    revision: originalValue.revision && { ...originalValue.revision,
      context: { ...originalValue.revision.context, account_kind: 'paper', account_id: 'B-watch-paper' } } }
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id, csrf_token: 'signed-session', expires_at: '2099-01-01T00:00:00Z' })
  let stored: Record<string, unknown> | null = null
  const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    if (String(url).includes('/continuous/commands') && init?.method === 'POST') {
      const command = JSON.parse(String(init.body)), r = value.revision!
      const body = { schema_version: command.schema_version === 'perpetual-plan-watch-command.v3' ? 'perpetual-plan-watch-receipt.v3' : 'perpetual-plan-watch-receipt.v2', owner_id: selection.owner_id, request_id: command.request_id, command,
        command_hash: await hash(command), watch_id: 'perpwatchcontinuous_' + await hash([selection.owner_id, selection.partition, selection.horizon, selection.settings_revision]),
        state_version: command.expected_state_version + 1, state: command.operation === 'pause' ? 'paused' : 'active', accepted_at: '2026-10-20T00:00:00Z',
        review_due_at: r.valid_until, auto_review_slot: null, origin_run_id: value.run_id, origin_artifact_id: value.report_id,
        plan_revision_ref: command.expected_revision_ref, snapshot_ref: { ref: value.snapshot!.id, hash: value.snapshot!.snapshot_hash },
        comparison_ref: r.bindings.comparison, frozen_forecast_ref: r.bindings.frozen_forecast, publication_ref: { ref: 'original-publication', hash: 'a'.repeat(64) } }
      const digest = await hash(body)
      stored = { ...body, revision: { ref: 'perpwatchcontinuouscmd_' + await hash([selection.owner_id, command.request_id]), hash: digest }, payload_hash: digest }
      return new Response(JSON.stringify(stored))
    }
    return new Response(JSON.stringify(String(url).includes('/continuous/')
      ? { schema_version: 'perpetual-plan-watch-view.v2', receipt: stored, progress: null }
      : { schema_version: 'perpetual-plan-watch-view.v1', receipt: null, expiry: null }))
  })
  vi.stubGlobal('fetch', fetcher)
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const render = async () => {
    await act(async () => root.render(<QueryClientProvider client={cache}><PerpetualPlanWatchPanel selection={selection} value={value} /></QueryClientProvider>))
    for (let i = 0; i < 50 && host.querySelector<HTMLButtonElement>('[aria-label="持续跟踪已发布方案"] button')?.disabled !== false; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  }
  const click = async (label: string) => {
    await act(async () => [...host.querySelectorAll('button')].find(b => b.textContent === label)!.click())
    for (let i = 0; i < 20; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  }
  try {
    await render()
    const section = host.querySelector('[aria-label="持续跟踪已发布方案"]')
    expect(section, 'continuous opt-in must be a separate visible control').not.toBeNull()
    expect(host.textContent).toContain('登记到期提醒一次')
    expect(section!.querySelector<HTMLInputElement>('input[type=checkbox]')!.checked).toBe(false)
    expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
    await click('开启持续跟踪')
    const posts = () => fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts()).toHaveLength(1)
    const first = JSON.parse(String(posts()[0][1]!.body))
    expect(first.schema_version).toBe('perpetual-plan-watch-command.v3')
    expect(first.continuation).toBe('follow_published_revisions')
    expect(first.input_policy).toBe('saved_preferences_current_paper_ledger.v1')
    expect(first.mode).toBe('remind_only')
    expect(first.condition_research).toBe(false)
    expect(section!.textContent).toContain('当前模拟账户账本')
    expect(host.textContent).toContain('持续跟踪已开启')
    cache.clear()
    await render()
    expect(host.textContent).toContain('持续跟踪已开启')
    expect(posts()).toHaveLength(1)
    await click('暂停持续跟踪')
    expect(JSON.parse(String(posts()[1][1]!.body)).operation).toBe('pause')
    expect(host.textContent).toContain('持续跟踪已暂停')
    await act(async () => host.querySelector<HTMLInputElement>('[aria-label="持续跟踪已发布方案"] input[type=checkbox]')!.click())
    await click('开启持续跟踪')
    expect(JSON.parse(String(posts()[2][1]!.body)).mode).toBe('remind_and_research')
    expect(JSON.parse(String(posts()[2][1]!.body)).condition_research).toBe(true)
    expect(JSON.parse(String(posts()[2][1]!.body)).input_policy).toBe('saved_preferences_current_paper_ledger.v1')
    const original = stored
    stored = Object.assign({}, stored, { owner_id: 'foreign' })
    await expect(planWatchApi.continuousCurrent(selection)).rejects.toThrow()
    stored = original
  } finally { await act(async () => root.unmount()); cache.clear(); host.remove() }
})

it('keeps legacy continuous research consent limited to expiry review', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const { selection, value, view } = await continuousWatchFixture()
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id, csrf_token: 'signed-session', expires_at: '2099-01-01T00:00:00Z' })
  const fetcher = vi.fn(async (url: string | URL | Request, _init?: RequestInit) => new Response(JSON.stringify(String(url).includes('/continuous/')
    ? view : { schema_version: 'perpetual-plan-watch-view.v1', receipt: null, expiry: null })))
  vi.stubGlobal('fetch', fetcher)
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><PerpetualPlanWatchPanel selection={selection} value={value} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !host.textContent?.includes('持续跟踪已开启'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(host.textContent).toContain('旧版授权仅涵盖每版到期复查，未授权条件即时研究')
    expect(host.textContent).not.toContain('已单独同意条件触发即时研究')
    expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  } finally { await act(async () => root.unmount()); cache.clear(); host.remove() }
})

it.each([
  { status: 'waiting', truth: 'true', state: 'monitoring', streak: 1, label: '待确认', reason: '', request: null },
  { status: 'confirmed', truth: 'true', state: 'monitoring', streak: 2, label: '已确认', reason: '', request: 'riskresearch_condition_confirmed' },
  { status: 'not_confirmed', truth: 'false', state: 'monitoring', streak: 0, label: '未确认', reason: '', request: null },
  { status: 'unknown', truth: 'unknown', state: 'unknown', streak: 0, label: '证据未知', reason: 'closed_bar_stale', request: null },
  { status: 'unmonitored', truth: 'unknown', state: 'unregistered', streak: 0, label: '未监测', reason: 'no_versioned_condition_declaration', request: null },
  { status: 'invalidated', truth: 'true', state: 'invalidated', streak: 2, label: '已失效', reason: '', request: 'riskresearch_condition_invalidated' },
])('shows $status condition evidence without turning it into new advice', async ({ status, truth, state, streak, label, reason, request }) => {
  vi.stubGlobal('crypto', webcrypto)
  const { selection, value, view } = await continuousWatchFixture()
  const invalidated = status === 'invalidated', unmonitored = status === 'unmonitored'
  const raw = { ...view, condition_monitoring: { ...view.condition_monitoring, state, new_exposure_blocked: invalidated,
    conditions: [{ ...view.condition_monitoring.conditions[0], status, truth, streak,
      purpose: unmonitored ? null : invalidated ? 'invalidation' : 'confirmation', required_samples: unmonitored ? null : 2,
      last_bar_at: unmonitored ? null : '2026-10-21T00:02:00Z', observed_at: unmonitored ? null : '2026-10-21T00:02:01Z',
      reason_codes: reason ? [reason] : [], request_id: request, outbox_ids: request ? [71] : [] }] } }
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id, csrf_token: 'signed-session', expires_at: '2099-01-01T00:00:00Z' })
  vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request) => new Response(JSON.stringify(String(url).includes('/continuous/')
    ? raw : { schema_version: 'perpetual-plan-watch-view.v1', receipt: null, expiry: null }))))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><PerpetualPlanWatchPanel selection={selection} value={value} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !host.querySelector('[aria-label="当前方案的条件监测"]'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    const panel = host.querySelector('[aria-label="当前方案的条件监测"]')
    expect(panel, 'accepted condition originals must have a visible monitoring state').not.toBeNull()
    expect(panel!.textContent).toContain(label)
    expect(panel!.textContent).toContain(view.receipt.origin_run_id)
    expect(panel!.textContent).toContain(view.receipt.plan_revision_ref.ref)
    expect(panel!.textContent).toContain(view.condition_monitoring.conditions[0].text)
    if (unmonitored) {
      expect(panel!.textContent).toContain('未监测的条件不作确认或失效判断')
      expect(panel!.textContent).toContain('连续样本：0 / 未声明')
      expect(panel!.textContent).toContain('源 K 线时间：暂无')
    } else {
      expect(panel!.textContent).toContain(`连续样本：${streak} / 2`)
      expect(panel!.querySelector('time[datetime="2026-10-21T00:02:00Z"]')).not.toBeNull()
    }
    if (reason) expect(panel!.textContent).toContain(reason)
    if (request) {
      expect(panel!.textContent).toContain(request)
      expect(panel!.textContent).toContain('不代表研究已完成')
      expect(panel!.textContent).toContain('不代表已送达')
    }
    if (status === 'confirmed') {
      expect(panel!.textContent).toContain('条件确认不会改变原概率')
      expect(panel!.textContent).toContain('经审核后正式发布的后续方案')
    }
    if (invalidated) {
      expect(panel!.textContent).toContain('服务端已拒绝这版旧建议的新增模拟开仓或加仓')
      expect(panel!.textContent).toContain('已有模拟仓位的保护继续有效')
    }
  } finally { await act(async () => root.unmount()); cache.clear(); host.remove() }
})

it('explains an unregistered monitoring source once when no conditions are available', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const { selection, value, view } = await continuousWatchFixture()
  const raw = { ...view, condition_monitoring: { ...view.condition_monitoring, state: 'unregistered', conditions: [],
    reason_codes: ['prepared_condition_declarations_unavailable'] } }
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: selection.owner_id, csrf_token: 'signed-session', expires_at: '2099-01-01T00:00:00Z' })
  vi.stubGlobal('fetch', vi.fn(async (url: string | URL | Request) => new Response(JSON.stringify(String(url).includes('/continuous/')
    ? raw : { schema_version: 'perpetual-plan-watch-view.v1', receipt: null, expiry: null }))))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={cache}><PerpetualPlanWatchPanel selection={selection} value={value} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !host.querySelector('[aria-label="当前方案的条件监测"]'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    const panel = host.querySelector('[aria-label="当前方案的条件监测"]')
    expect(panel).not.toBeNull()
    expect(panel!.textContent).toContain('未登记条件监测')
    expect(panel!.textContent!.split('prepared_condition_declarations_unavailable')).toHaveLength(2)
    expect(panel!.textContent).toContain('未监测的条件不作确认或失效判断')
  } finally { await act(async () => root.unmount()); cache.clear(); host.remove() }
})
