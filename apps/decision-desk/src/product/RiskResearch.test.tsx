// @vitest-environment jsdom
import { act } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { riskResearchStatusSchema, type RiskResearchRequest, type RiskResearchStatus } from '@decision-hub/contracts-ts'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import * as clientApi from '../api/client'
import { RiskResearch } from './RiskResearch'
import { followupKey, researchOwner } from './researchFollowup'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())

it('keeps the clicked owner request after newer automatic work and restores its failure on reload with one POST', async () => {
  const scope = realStates[0].scope_id
  localStorage.removeItem(followupKey(scope))
  const a = { ...realStates[0].request!, request_id: 'engineering-followup-A', run_id: 'engineering-run-A', status: 'queued' as const, run_status: 'running', error_code: null }
  const b = { ...a, request_id: 'engineering-automatic-B', status: 'pending_admission' as const, run_id: null, run_status: null, attempts: 0, trigger_refs: ['engineering-automatic-alert'] }
  let commandId = ''
  let failed = false
  let posts = 0
  const call = vi.spyOn(clientApi, 'request').mockImplementation(async (path, init) => {
    if (init?.method === 'POST') {
      posts += 1
      commandId = (JSON.parse(String(init.body)) as { request_id: string }).request_id
      return a
    }
    if (String(path).includes('/commands/')) return {
      schema_version: 'risk-research-owner-receipt.v1', owner_id: researchOwner, scope_id: scope,
      command_id: commandId, accepted_at: a.created_at, as_of: realStates[0].as_of,
      request: failed ? { ...a, status: 'failed', run_status: 'failed', error_code: 'research_method_binding_failed' } : a,
      next_automatic_request: failed ? b : null,
    }
    return { ...realStates[0], enabled: true, request: posts === 0 ? null : failed ? b : a }
  })
  const navigate = vi.fn()
  const element = document.createElement('div'); document.body.append(element)
  let client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let root = createRoot(element)
  const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
  const mount = async () => {
    await act(async () => root.render(<QueryClientProvider client={client}><RiskResearch scopeId={scope} onNavigate={navigate} /></QueryClientProvider>))
    await settle()
  }
  try {
    await mount()
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')!.click())
    await settle()
    expect(posts).toBe(1)
    expect(element.textContent).toContain('我本次的研究')
    expect(element.textContent).toContain('研究正在执行')
    failed = true
    await act(async () => { await client.invalidateQueries({ queryKey: ['risk-research', scope] }); await client.invalidateQueries({ queryKey: ['risk-research-owner-command'] }) })
    await settle()
    expect(element.textContent).toContain('本次执行失败')
    expect(element.textContent).toContain('下次自动研究计划')
    expect(element.querySelector('[aria-label="下次自动研究计划"]')?.textContent).toContain('engineering-automatic-B')
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看研究执行记录')!.click())
    expect(navigate).toHaveBeenLastCalledWith('report', { run: 'engineering-run-A', back: '#/home' })
    await act(async () => root.unmount()); client.clear()
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); root = createRoot(element)
    const getsBeforeReload = call.mock.calls.filter(([path]) => String(path).includes('/commands/')).length
    await mount()
    expect(element.textContent).toContain('本次执行失败')
    expect(element.textContent).toContain('下次自动研究计划')
    expect(call.mock.calls.filter(([path]) => String(path).includes('/commands/')).length).toBeGreaterThan(getsBeforeReload)
    expect(JSON.parse(localStorage.getItem(followupKey(scope))!).canonicalRequestId).toBe(a.request_id)
    expect(posts).toBe(1)
  } finally {
    await act(async () => root.unmount()); client.clear(); element.remove(); localStorage.removeItem(followupKey(scope))
  }
})

it('restores an uncertain save with GET only and explicitly retries the original missing command', async () => {
  const scope = realStates[0].scope_id
  localStorage.removeItem(followupKey(scope))
  const a = { ...realStates[0].request!, request_id: 'engineering-recovered-A', status: 'pending_admission' as const, run_id: null, run_status: null, attempts: 0, error_code: null }
  const commands: string[] = []
  const keys: string[] = []
  let saved = false
  vi.spyOn(clientApi, 'request').mockImplementation(async (path, init) => {
    if (init?.method === 'POST') {
      commands.push((JSON.parse(String(init.body)) as { request_id: string }).request_id)
      keys.push(new Headers(init.headers).get('Idempotency-Key')!)
      if (commands.length === 1) throw new clientApi.ApiRequestError('network unavailable', 'network')
      saved = true
      return a
    }
    if (String(path).includes('/commands/')) {
      if (!saved) throw new clientApi.ApiRequestError('risk_research_owner_receipt_not_found', 'http', 404)
      return { schema_version: 'risk-research-owner-receipt.v1', owner_id: researchOwner, scope_id: scope,
        command_id: commands[0], accepted_at: a.created_at, as_of: realStates[0].as_of, request: a, next_automatic_request: null }
    }
    return { ...realStates[0], enabled: true, request: null }
  })
  const element = document.createElement('div'); document.body.append(element)
  let client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let root = createRoot(element)
  const mount = async () => {
    await act(async () => root.render(<QueryClientProvider client={client}><RiskResearch scopeId={scope} onNavigate={vi.fn()} /></QueryClientProvider>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
  }
  try {
    await mount()
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')!.click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(commands).toHaveLength(1)
    expect(JSON.parse(localStorage.getItem(followupKey(scope))!).canonicalRequestId).toBeNull()
    await act(async () => root.unmount()); client.clear()
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); root = createRoot(element)
    await mount()
    expect(commands).toHaveLength(1)
    const retry = Array.from(element.querySelectorAll('button')).find(button => button.textContent === '重试保存本次请求')!
    expect(retry.disabled).toBe(false)
    await act(async () => retry.click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    expect(commands).toHaveLength(2)
    expect(commands[1]).toBe(commands[0])
    expect(keys).toEqual([commands[0], commands[0]])
    expect(JSON.parse(localStorage.getItem(followupKey(scope))!).canonicalRequestId).toBe(a.request_id)
    expect(element.textContent).toContain('我本次的研究')
    expect(element.textContent).not.toContain('重试保存本次请求')
  } finally {
    await act(async () => root.unmount()); client.clear(); element.remove(); localStorage.removeItem(followupKey(scope))
  }
})

it.each(['failed', 'cancelled'] as const)('allows an explicit new owner command after a queued request has a %s Run', async run_status => {
  const scope = realStates[0].scope_id
  const originalCommand = `engineering-terminal-${run_status}`
  const original = { ...realStates[0].request!, request_id: `engineering-terminal-request-${run_status}`,
    status: 'queued' as const, run_id: `engineering-terminal-run-${run_status}`, run_status, error_code: null }
  const automatic = { ...original, request_id: 'engineering-newer-automatic', run_id: 'engineering-automatic-running', run_status: 'running', trigger_refs: ['engineering-automatic-alert'] }
  const next = { ...original, request_id: `engineering-new-request-${run_status}`, status: 'pending_admission' as const,
    run_id: null, run_status: null, attempts: 0, trigger_refs: [] }
  localStorage.setItem(followupKey(scope), JSON.stringify({ version: 1, ownerId: researchOwner, scopeId: scope,
    commandId: originalCommand, canonicalRequestId: original.request_id }))
  const commands: string[] = []
  const call = vi.spyOn(clientApi, 'request').mockImplementation(async (path, init) => {
    if (init?.method === 'POST') {
      const command = (JSON.parse(String(init.body)) as { request_id: string }).request_id
      expect(new Headers(init.headers).get('Idempotency-Key')).toBe(command)
      commands.push(command)
      return next
    }
    if (String(path).includes('/commands/')) {
      const command = decodeURIComponent(String(path).split('/commands/')[1])
      return { schema_version: 'risk-research-owner-receipt.v1', owner_id: researchOwner, scope_id: scope,
        command_id: command, accepted_at: original.created_at, as_of: realStates[0].as_of,
        request: command === originalCommand ? original : next,
        next_automatic_request: command === originalCommand ? automatic : null }
    }
    return { ...realStates[0], enabled: true, request: automatic }
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const navigate = vi.fn()
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  const settle = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
  const update = () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')!
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><RiskResearch scopeId={scope} onNavigate={navigate} /></QueryClientProvider>))
    await settle()
    expect(call.mock.calls.some(([path]) => String(path).endsWith(`/commands/${originalCommand}`))).toBe(true)
    expect(commands).toHaveLength(0)
    expect(element.textContent).toContain(run_status === 'failed' ? '本次执行失败' : '当前执行已取消')
    expect(element.textContent).not.toContain('当前请求尚未结束，无需重复提交')
    expect(update().disabled).toBe(false)
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看研究执行记录')!.click())
    expect(navigate).toHaveBeenLastCalledWith('report', { run: original.run_id, back: '#/home' })
    await act(async () => update().click())
    await settle()
    expect(commands).toHaveLength(1)
    expect(commands[0]).not.toBe(originalCommand)
    expect(JSON.parse(localStorage.getItem(followupKey(scope))!)).toMatchObject({
      commandId: commands[0], canonicalRequestId: next.request_id,
    })
    expect(element.textContent).toContain(`请求：${next.request_id}`)
    expect(update().disabled).toBe(true)
  } finally {
    await act(async () => root.unmount()); client.clear(); element.remove(); localStorage.removeItem(followupKey(scope))
  }
})

it.each([
  { status: 'pending_admission', run_id: null, run_status: null },
  { status: 'claiming', run_id: null, run_status: null },
  { status: 'queued', run_id: 'engineering-active-run', run_status: 'running' },
] as const)('keeps updates disabled for an active $status request with Run status $run_status', async state => {
  await renderState(synthetic({ ...state, error_code: null }), async element => {
    const update = Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')!
    expect(update.disabled).toBe(true)
    expect(element.textContent).toContain('当前请求尚未结束，无需重复提交')
    await act(async () => update.click())
  })
})

// The two terminal cases use the preserved real11 observation. Only API fields
// are projected; UTC database timestamps keep their original values. All other
// state variations below are explicitly synthetic UI controls, not new Runs.
const observation = JSON.parse(readFileSync(resolve(process.cwd(),
  '../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh11/admission-budget-observation01.json'), 'utf8')) as {
  observed_at: string
  requests: Array<{ request: { request_id: string; scope_id: string; instrument: 'BTC-USDT' | 'ETH-USDT'; status: string; run_id: string | null; created_at: string; available_at: string; attempts: number; error_code: string }; triggers: Array<{ trigger_key: string }> }>
}
const dbUtc = (value: string) => value.replace(' ', 'T') + 'Z'
const realStates = observation.requests.map(({ request, triggers }) => riskResearchStatusSchema.parse({
  schema_version: 'risk-research-status.v1', scope_id: request.scope_id, enabled: true, as_of: observation.observed_at,
  request: { schema_version: 'risk-research-request.v1', request_id: request.request_id, scope_id: request.scope_id,
    instrument: request.instrument, status: request.status, run_id: request.run_id, run_status: null,
    created_at: dbUtc(request.created_at), available_at: dbUtc(request.available_at), attempts: request.attempts,
    error_code: request.error_code, trigger_refs: triggers.map(trigger => trigger.trigger_key) },
}))
const synthetic = (overrides: Partial<RiskResearchRequest>) => riskResearchStatusSchema.parse({
  ...realStates[0], request: { ...realStates[0].request, ...overrides },
})
async function renderState(view: RiskResearchStatus, check: (element: HTMLDivElement, navigate: ReturnType<typeof vi.fn>) => void | Promise<void>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['risk-research', view.scope_id], view)
  const request = vi.spyOn(clientApi, 'request').mockRejectedValue(new Error('No transport is allowed in this UI control'))
  const navigate = vi.fn()
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><RiskResearch scopeId={view.scope_id} onNavigate={navigate} /></QueryClientProvider>))
    await check(element, navigate)
    expect(request).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); client.clear(); element.remove() }
}

it.each(realStates)('explains preserved real11 $request.instrument manual-admission failure without promising future recovery', async view => {
  expect(view.request!.status).toBe('failed')
  expect(view.request!.run_id).toBeNull()
  expect(Date.parse(view.request!.available_at)).toBeGreaterThan(Date.parse(view.as_of))
  await renderState(view, element => {
    expect(element.textContent).toContain('研究准入次数已用尽，本次请求未进入研究')
    expect(element.querySelector('[role="alert"]')?.textContent).toContain('本次请求已结束')
    expect(element.textContent).not.toContain('等待计划处理')
    expect(element.textContent).not.toContain('最早再次检查时间')
    expect(element.querySelector('code')?.textContent).toBe('research_admission_budget_exhausted')
    expect(element.textContent).not.toContain('查看研究执行记录')
    expect(Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')?.disabled).toBe(false)
  })
})

it('shows a synthetic pending quota block with the saved earliest check, not a promised quota reset', async () => {
  await renderState(synthetic({ status: 'pending_admission', attempts: 2 }), element => {
    expect(element.textContent).toContain('准入次数暂不足，尚未开始研究')
    expect(element.textContent).toContain('最早再次检查时间：北京时间 2026/10/08 12:14:25')
    expect(element.textContent).toContain('不保证准入次数恢复或研究开始')
    expect(element.textContent).not.toContain('本次请求已结束')
    expect(element.textContent).not.toContain('研究已进入执行队列')
  })
})

it('preserves ordinary synthetic waiting without inventing an admission error', async () => {
  await renderState(synthetic({ status: 'pending_admission', attempts: 0, error_code: null }), element => {
    expect(element.textContent).toContain('研究请求已保存，等待计划处理')
    expect(element.textContent).toContain('最早再次检查时间')
    expect(element.textContent).not.toContain('准入次数已用尽')
    expect(element.querySelector('[role="alert"]')).toBeNull()
  })
})

it.each([
  ['study_research_active', '当前资产已有研究正在执行'],
  ['study_host_terminal_unknown', '上一项研究的运行结束状态尚未确认'],
])('explains the known synthetic admission reason %s without guessing a finished Run', async (error_code, expected) => {
  await renderState(synthetic({ status: 'pending_admission', attempts: 1, error_code }), element => {
    expect(element.textContent).toContain(expected)
    expect(element.querySelector('code')?.textContent).toBe(error_code)
    expect(element.textContent).not.toContain('本次请求未进入研究')
  })
})

it('keeps an unknown synthetic failure inspectable without rendering its code as the main explanation', async () => {
  await renderState(synthetic({ error_code: 'unrecognized_admission_reason' }), element => {
    expect(element.textContent).toContain('本次研究未完成')
    expect(element.textContent).toContain('具体原因保留在“触发与失败记录”')
    expect(element.querySelector('strong')?.textContent).not.toContain('unrecognized')
    expect(element.querySelector('code')?.textContent).toBe('unrecognized_admission_reason')
  })
})

it.each(['pending_admission', 'failed'] as const)('explains fresh activity confirmation for %s without granting a Run or rewriting the old request', async status => {
  await renderState(synthetic({ status, attempts: status === 'failed' ? 3 : 2, error_code: 'study_host_activity_refresh_required' }), element => {
    expect(element.textContent).toContain('完整、未过期的运行环境核验')
    expect(element.textContent).toContain('旧研究的结束记录保持原样')
    expect(element.textContent).not.toContain('研究已进入执行队列')
    const button = Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')!
    expect(button.disabled).toBe(status !== 'failed')
    if (status === 'failed') {
      expect(element.textContent).toContain('系统不会自动发起第四次')
      expect(element.textContent).toContain('会保存一项新的请求')
      expect(element.textContent).toContain('保存请求不代表研究已经开始')
    }
  })
})

it('explains a retired third lease and permits a new intent without claiming execution', async () => {
  await renderState(synthetic({ status: 'failed', attempts: 3, error_code: 'risk_research_claim_lease_expired' }), element => {
    expect(element.textContent).toContain('未能在有效租约内完成')
    expect(element.textContent).toContain('系统不会自动发起第四次')
    expect(element.textContent).toContain('保存请求不代表研究已经开始')
    expect(element.textContent).not.toContain('研究已进入执行队列')
    expect(Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')!.disabled).toBe(false)
  })
})

it('preserves an already queued synthetic Run and its existing execution-record navigation', async () => {
  await renderState(synthetic({ status: 'queued', run_id: 'run-ui-control', run_status: 'admitted', error_code: null }), async (element, navigate) => {
    expect(element.textContent).toContain('研究已进入执行队列')
    expect(element.textContent).not.toContain('准入次数暂不足')
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看研究执行记录')!.click())
    expect(navigate).toHaveBeenCalledWith('progress', { run: 'run-ui-control', back: '#/home' })
  })
})

const request13 = JSON.parse(readFileSync(resolve(process.cwd(),
  '../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh13/request-observation01.json'), 'utf8')) as {
  requests: Array<{ instrument: 'BTC-USDT' | 'ETH-USDT'; new_request: {
    request_id: string; scope_id: string; instrument: string; created_at: string; available_at: string; attempts: number; run_id: string
  }; trigger_originals: Array<{ source_ref: string }> }>
}
const terminal13 = JSON.parse(readFileSync(resolve(process.cwd(),
  '../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh13/execution01/receipt.json'), 'utf8')) as {
  observed_at: string; runs: Array<{ run_id: string; run: { status: string; error_code: string } }>
}
// Real13 request identity/timestamps and terminal fields are projected into the
// public DTO. The saved ETH PC DOM independently shows observation_unavailable.
// This is an isolated display control, not a new HTTP response or successful Run.
const failed13 = request13.requests.map(({ new_request, trigger_originals }) => {
  const run = terminal13.runs.find(item => item.run_id === new_request.run_id)!.run
  return riskResearchStatusSchema.parse({ schema_version: 'risk-research-status.v1',
    scope_id: new_request.scope_id, enabled: true, as_of: terminal13.observed_at,
    request: { schema_version: 'risk-research-request.v1', request_id: new_request.request_id,
      scope_id: new_request.scope_id, instrument: new_request.instrument, run_id: new_request.run_id,
      attempts: new_request.attempts, status: 'failed',
      created_at: dbUtc(new_request.created_at), available_at: dbUtc(new_request.available_at),
      run_status: run.status, error_code: run.error_code, trigger_refs: trigger_originals.map(item => item.source_ref) },
  })
})

it.each(failed13)('classifies preserved real13 $request.instrument with its saved Run as execution or result-read failure', async view => {
  await renderState(view, async (element, navigate) => {
    expect(element.textContent).toContain('研究已进入执行')
    expect(element.textContent).not.toContain('准入核验尚未通过')
    expect(element.textContent).not.toContain('本次请求未进入研究')
    expect(element.textContent).not.toContain('最早再次检查时间')
    if (view.request!.error_code === 'research_observation_unavailable') {
      expect(element.querySelector('strong')?.textContent).toContain('结果状态暂不可读取')
      expect(element.textContent).toContain('结果原件仍需独立核对')
    } else {
      expect(view.request!.error_code).toBe('research_worker_failed')
      expect(element.querySelector('strong')?.textContent).toContain('本次执行失败')
    }
    expect(element.querySelector('code')?.textContent).toBe(view.request!.error_code)
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看研究执行记录')!.click())
    expect(view.request!.run_status).toBe('failed')
    expect(navigate).toHaveBeenCalledWith('report', { run: view.request!.run_id, back: '#/home' })
  })
})

it.each(['running', null])('does not infer a terminal Run from an unavailable observation with synthetic Run status %s', async run_status => {
  await renderState(synthetic({ run_id: 'run-observation-control', run_status, error_code: 'research_observation_unavailable' }), async (element, navigate) => {
    expect(element.querySelector('strong')?.textContent).toBe('研究已进入执行，结果状态暂不可读取')
    expect(element.textContent).not.toContain('本次执行失败')
    expect(element.textContent).not.toContain('执行状态记录为失败')
    expect(element.textContent).not.toContain('准入核验尚未通过')
    expect(element.textContent).toContain('读取或重试能否成功以实际回执为准')
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看研究执行记录')!.click())
    expect(navigate).toHaveBeenCalledWith('progress', { run: 'run-observation-control', back: '#/home' })
  })
})

it('never uses a synthetic budget error to erase an existing Run or claim it did not enter research', async () => {
  await renderState(synthetic({ run_id: 'run-existing-control', run_status: 'failed' }), element => {
    expect(element.querySelector('strong')?.textContent).toContain('研究已进入执行')
    expect(element.textContent).not.toContain('本次请求未进入研究')
    expect(element.textContent).not.toContain('准入核验尚未通过')
    expect(element.querySelector('[role="alert"]')?.textContent).not.toContain('准入次数已用尽')
    expect(element.querySelector('code')?.textContent).toBe('research_admission_budget_exhausted')
  })
})

it('keeps a synthetic unknown post-Run failure inspectable without inventing an admission or execution cause', async () => {
  await renderState(synthetic({ run_id: 'run-unknown-control', run_status: null, error_code: 'unknown_execution_control' }), element => {
    expect(element.querySelector('strong')?.textContent).toContain('本次结果尚未确认')
    expect(element.textContent).not.toContain('准入核验尚未通过')
    expect(element.textContent).not.toContain('本次执行失败')
    expect(element.querySelector('code')?.textContent).toBe('unknown_execution_control')
  })
})

it('preserves synthetic paused no-Run admission classification and its existing recovery boundary', async () => {
  await renderState(synthetic({ run_id: null, run_status: null, error_code: 'study_not_active' }), element => {
    expect(element.textContent).toContain('当前持续研究尚未启用')
    expect(element.textContent).toContain('核对原因与恢复条件后')
    expect(element.textContent).not.toContain('研究已进入执行')
    expect(element.textContent).not.toContain('查看研究执行记录')
  })
})

it('respects a synthetic cancelled Run without classifying it as an admission refusal', async () => {
  await renderState(synthetic({ run_id: 'run-cancelled-control', run_status: 'cancelled', error_code: null }), async (element, navigate) => {
    expect(element.querySelector('strong')?.textContent).toContain('当前执行已取消')
    expect(element.textContent).not.toContain('准入核验尚未通过')
    expect(element.textContent).not.toContain('本次执行失败')
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看研究执行记录')!.click())
    expect(navigate).toHaveBeenCalledWith('report', { run: 'run-cancelled-control', back: '#/home' })
  })
})

it('blocks repeated updates until the saved request ends and preserves refresh receipts across scopes', async () => {
  const pending = synthetic({ status: 'pending_admission', attempts: 2, trigger_refs: ['automatic-ui-control'] })
  const settled = riskResearchStatusSchema.parse({ ...pending, request: { ...pending.request, status: 'failed', attempts: 3 } })
  const other = riskResearchStatusSchema.parse({ ...pending, scope_id: 'other-ui-scope', request: {
    ...pending.request, scope_id: 'other-ui-scope', request_id: 'other-ui-request', instrument: 'ETH-USDT',
  } })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['risk-research', pending.scope_id], pending)
  client.setQueryData(['risk-research', other.scope_id], other)
  localStorage.removeItem(followupKey(pending.scope_id))
  localStorage.removeItem(followupKey(other.scope_id))
  const transport = vi.spyOn(clientApi, 'request').mockImplementation(async (path, init) => {
    if (init?.method === 'POST') return pending.request
    if (String(path).includes('/commands/')) return {
      schema_version: 'risk-research-owner-receipt.v1', owner_id: researchOwner, scope_id: pending.scope_id,
      command_id: decodeURIComponent(String(path).split('/commands/')[1]),
      accepted_at: pending.request!.created_at, as_of: pending.as_of,
      request: (client.getQueryData(['risk-research', pending.scope_id]) as RiskResearchStatus).request,
      next_automatic_request: null,
    }
    return pending
  })
  const navigate = vi.fn()
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  const render = async (scope: string) => act(async () => root.render(<QueryClientProvider client={client}><RiskResearch scopeId={scope} onNavigate={navigate} /></QueryClientProvider>))
  const updateState = async (view: RiskResearchStatus) => act(async () => {
    client.setQueryData(['risk-research', pending.scope_id], view)
    await client.invalidateQueries({ queryKey: ['risk-research-owner-command', researchOwner, pending.scope_id] })
    await new Promise(resolve => setTimeout(resolve, 0))
  })
  const refreshButton = () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '更新当前资产研究')!
  try {
    await render(pending.scope_id)
    for (const request of [
      pending.request,
      { ...pending.request, status: 'claiming' },
      { ...pending.request, status: 'queued', run_id: 'run-active-update-control', run_status: 'running', error_code: null },
    ]) {
      const active = riskResearchStatusSchema.parse({ ...pending, request })
      await act(async () => { client.setQueryData(['risk-research', pending.scope_id], active); await new Promise(resolve => setTimeout(resolve, 0)) })
      expect(refreshButton().disabled).toBe(true)
      expect(element.textContent).toContain('当前请求尚未结束，无需重复提交')
      expect(element.textContent).toContain(`请求：${pending.request!.request_id}`)
      await act(async () => refreshButton().click())
      expect(transport).not.toHaveBeenCalled()
      expect(client.getQueryData(['risk-research', pending.scope_id])).toEqual(active)
    }
    expect(element.textContent).toContain('研究正在执行')
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '查看研究执行记录')!.click())
    expect(navigate).toHaveBeenCalledWith('progress', { run: 'run-active-update-control', back: '#/home' })
    navigate.mockClear()
    await updateState(settled)
    expect(refreshButton().disabled).toBe(false)
    await act(async () => refreshButton().click())
    expect(element.querySelector('[data-request-id]')?.getAttribute('data-request-id')).toBe(pending.request!.request_id)
    expect(element.textContent).toContain('已收到本次更新请求，提交时已合并到等待队列。提交已保存；下方持续核对这次原请求。')
    expect(transport.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(refreshButton().disabled).toBe(true)
    expect(element.textContent).toContain('本次核验返回研究准入次数已用尽')
    expect(element.textContent).toContain('最早再次检查时间')
    expect(element.textContent).toContain('核验次数：2/3')
    expect(element.textContent).not.toContain('研究已进入执行队列')
    expect(client.getQueryData(['risk-research', pending.scope_id])).toEqual(pending)
    expect(navigate).not.toHaveBeenCalled()

    const ended = riskResearchStatusSchema.parse({ ...pending, request: { ...pending.request, status: 'completed', run_id: 'run-receipt-terminal-control', error_code: null } })
    await updateState(ended)
    expect(element.textContent).toContain('关联研究执行已结束')
    expect(element.querySelector('[data-request-id]')?.textContent).toContain('提交时已合并')
    expect(element.querySelector('[data-request-id]')?.textContent).not.toContain('尚未开始研究')
    expect(refreshButton().disabled).toBe(false)
    await updateState(settled)

    let resolvePost!: (value: unknown) => void
    transport.mockImplementationOnce(() => new Promise(resolve => { resolvePost = resolve }))
    await act(async () => refreshButton().click())
    await render(other.scope_id)
    await act(async () => resolvePost(pending.request))
    expect(element.querySelector('[data-request-id]')).toBeNull()
    expect(element.textContent).not.toContain('已收到本次更新请求')
    expect(element.querySelector('[role="alert"]')).toBeNull()
    await render(pending.scope_id)
    expect(element.querySelector('[data-request-id]')).toBeNull()
  } finally {
    await act(async () => root.unmount()); client.clear(); element.remove()
    localStorage.removeItem(followupKey(pending.scope_id)); localStorage.removeItem(followupKey(other.scope_id))
  }
})
