// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ResearchRunCommandReceipt, ResearchRunCommandResult } from '../api/types'
import { DesignStatePage } from '../DesignStatePage'
import { commandReceipt, commandResult } from './commandTestFixtures'
import { createPendingCommand, readPendingCommand } from './commandRequests'
import { listPendingRequests, pendingRequest } from './pendingRequests'
import { RunCommandRecovery } from './RunCommandRecovery'
import { useRunCommand } from './useRunCommand'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
beforeEach(() => { vi.spyOn(api, 'researchLinkedCommandReceipt').mockResolvedValue(null) })
const roots: Root[] = []
const clients: QueryClient[] = []
const containers: HTMLElement[] = []
afterEach(async () => {
  await act(async () => roots.splice(0).forEach(root => root.unmount()))
  clients.splice(0).forEach(client => client.clear())
  containers.splice(0).forEach(container => container.remove())
  sessionStorage.clear(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/')
})

function Harness({ runId = 'run-a', onConfirmed = vi.fn() }: { runId?: string; onConfirmed?: (receipt: ResearchRunCommandReceipt) => void }) {
  const state = useRunCommand(runId, 'owner', onConfirmed)
  return <><label>操作理由<input value={state.reason} disabled={state.locked || state.busy} onChange={event => state.setReason(event.target.value)} /></label><button disabled={state.locked || state.busy} onClick={() => state.execute('retry', state.reason || '报告入口原理由')}>新重试</button><RunCommandRecovery state={state} /></>
}
async function render(element: React.ReactNode) {
  const container = document.createElement('div'); document.body.append(container); containers.push(container)
  const root = createRoot(container); roots.push(root)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(client)
  const rerender = async (next: React.ReactNode) => act(async () => root.render(<QueryClientProvider client={client}>{next}</QueryClientProvider>))
  await rerender(element)
  return { container, root, rerender }
}
async function settled() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) }) }
function button(container: HTMLElement, text: string) { return [...container.querySelectorAll('button')].find(button => button.textContent === text)! }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }

describe('research command recovery across PC entry points', () => {
  it('recovers a committed response loss in the failed page by GET only, preserving report-entry reason and key', async () => {
    window.history.replaceState(null, '', '/#/report?run=run-a')
    let committed: ResearchRunCommandReceipt | null = null
    const get = vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, owner) => { committed = commandReceipt(id, command, owner); throw new Error('pc_probe_response_withheld_after_commit') })
    const first = await render(<Harness />)
    await act(async () => button(first.container, '新重试').click()); await settled()
    const saved = readPendingCommand('run-a', 'owner')!
    expect(saved.intent.command.reason).toBe('报告入口原理由')
    expect(first.container.querySelector('[role="alert"] > p')?.textContent).not.toContain('pc_probe')
    const raw = first.container.querySelector<HTMLDetailsElement>('[role="alert"] details')!
    expect(raw.open).toBe(false); expect(raw.textContent).toContain('pc_probe_response_withheld_after_commit')
    await act(async () => first.root.unmount()); roots.splice(roots.indexOf(first.root), 1)
    window.history.replaceState(null, '', '/#/failed?run=run-a')
    vi.spyOn(api, 'researchRun').mockResolvedValue({ run: { run_id: 'run-a', status: 'failed', event_title: '原任务', stop_reason: null, failure: null } } as Awaited<ReturnType<typeof api.researchRun>>)
    const navigate = vi.fn()
    const second = await render(<DesignStatePage scene="failed" params={new URLSearchParams({ run: 'run-a', back: '#/studies?study=s1' })} onNavigate={navigate} />)
    await settled()
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenLastCalledWith('run-a', saved.record.id, 'owner')
    expect(navigate).not.toHaveBeenCalled()
    const receipt = JSON.parse(second.container.querySelector('details pre')!.textContent!)
    expect(receipt.command.reason).toBe('报告入口原理由')
    await act(async () => button(second.container, '打开已确认的新研究').click()); await settled()
    expect(navigate).toHaveBeenCalledWith('queued', { run: 'child-run-a', back: '#/studies?study=s1' })
    expect(post).toHaveBeenCalledTimes(1)
  })
  it('restores pending without POST, locks original reason, and explicitly retries the same key only after missing GET', async () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '锁定原理由')
    let committed: ResearchRunCommandReceipt | null = null
    const order: string[] = []
    vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => { order.push('GET'); return committed })
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, owner) => { order.push('POST'); committed = commandReceipt(id, command, owner); return committed.result })
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />); await settled()
    expect(post).not.toHaveBeenCalled()
    expect(page.container.querySelector('input')?.value).toBe('锁定原理由')
    expect(page.container.querySelector('input')?.disabled).toBe(true)
    await act(async () => { button(page.container, '核对并按原操作重试').click(); button(page.container, '核对并按原操作重试').click() }); await settled()
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('run-a', saved.intent.command, 'owner')
    expect(order).toEqual(['GET', 'GET', 'POST', 'GET'])
    expect(listPendingRequests('research-command')).toEqual([])
    expect(confirmed).toHaveBeenCalledTimes(1)
  })
  it('does not POST when lookup errors or a wrong-owner receipt is returned', async () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原理由')
    const get = vi.spyOn(api, 'researchCommandReceipt').mockRejectedValue(new Error('HTTP 500 storage_unavailable'))
    const post = vi.spyOn(api, 'researchCommand')
    const page = await render(<Harness />); await settled()
    await act(async () => button(page.container, '核对并按原操作重试').click()); await settled()
    expect(post).not.toHaveBeenCalled()
    get.mockResolvedValue(commandReceipt('run-a', saved.intent.command, 'wrong-owner'))
    await act(async () => button(page.container, '核对并按原操作重试').click()); await settled()
    expect(post).not.toHaveBeenCalled()
    expect(page.container.textContent).toContain('完整回执与原请求不一致')
    expect(readPendingCommand('run-a', 'owner')).not.toBeNull()
  })
  it('rejects a mismatched POST before acknowledgement or subsequent receipt lookup', async () => {
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
    vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command) => ({ ...commandResult(id, command), request_id: 'other-key' }))
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />)
    await act(async () => button(page.container, '新重试').click()); await settled()
    expect(get).toHaveBeenCalledTimes(1)
    expect(readPendingCommand('run-a', 'owner')).not.toBeNull()
    expect(confirmed).not.toHaveBeenCalled()
    expect(page.container.textContent).toContain('任务操作回执与原请求不一致')
  })
  it('keeps accepted POST unconfirmed until the full receipt exists, then explicit GET skips another POST', async () => {
    let committed: ResearchRunCommandReceipt | null = null
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, owner) => { committed = commandReceipt(id, command, owner); return committed.result })
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />)
    await act(async () => button(page.container, '新重试').click()); await settled()
    expect(confirmed).not.toHaveBeenCalled(); expect(readPendingCommand('run-a', 'owner')).not.toBeNull()
    expect(page.container.textContent).toContain('尚无可核对的完整回执')
    get.mockResolvedValue(committed)
    await act(async () => button(page.container, '核对并按原操作重试').click()); await settled()
    expect(post).toHaveBeenCalledTimes(1); expect(confirmed).toHaveBeenCalledTimes(1)
  })
  it('keeps rejected result and original reason, without navigation or saved-feedback success', async () => {
    let committed: ResearchRunCommandReceipt | null = null
    vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
    vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, owner) => { committed = commandReceipt(id, command, owner, 'rejected'); return committed.result })
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />)
    await act(async () => button(page.container, '新重试').click()); await settled()
    expect(page.container.querySelector('input')?.value).toBe('报告入口原理由')
    expect(page.container.textContent).toContain('服务端拒绝了原操作')
    expect(page.container.textContent).not.toContain('反馈已确认保存')
    expect(page.container.textContent).not.toContain('已确认创建后续研究')
    expect(confirmed).not.toHaveBeenCalled(); expect(listPendingRequests('research-command')).toEqual([])
  })
  it('retains late committed intent without navigating or clearing a different Run reason', async () => {
    window.history.replaceState(null, '', '/#/report?run=run-a')
    let committed: ResearchRunCommandReceipt | null = null
    const delayed = deferred<ResearchRunCommandResult>()
    vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
    vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, owner) => { committed = commandReceipt(id, command, owner); return delayed.promise })
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />)
    await act(async () => button(page.container, '新重试').click())
    const saved = readPendingCommand('run-a', 'owner')!
    window.history.replaceState(null, '', '/#/report?run=run-b')
    await page.rerender(<Harness runId="run-b" onConfirmed={confirmed} />)
    await act(async () => {
      const input = page.container.querySelector('input')!
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'B的新理由'); input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => delayed.resolve(committed!.result)); await settled()
    expect(confirmed).not.toHaveBeenCalled(); expect(window.location.hash).toBe('#/report?run=run-b')
    expect(page.container.querySelector('input')?.value).toBe('B的新理由')
    expect(listPendingRequests('research-command')).toEqual([saved.record])
  })
  it('does not send a new command after leaving while the initial GET is pending', async () => {
    const delayed = deferred<ResearchRunCommandReceipt | null>()
    vi.spyOn(api, 'researchCommandReceipt').mockReturnValue(delayed.promise)
    const post = vi.spyOn(api, 'researchCommand')
    const page = await render(<Harness />)
    await act(async () => button(page.container, '新重试').click())
    await act(async () => page.root.unmount()); roots.splice(roots.indexOf(page.root), 1)
    await act(async () => delayed.resolve(null)); await settled()
    expect(post).not.toHaveBeenCalled(); expect(readPendingCommand('run-a', 'owner')).not.toBeNull()
  })
  it('keeps confirmed receipt and locked pending when local cleanup fails', async () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原理由')
    vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(commandReceipt('run-a', saved.intent.command))
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied') })
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />); await settled()
    expect(page.container.textContent).toContain('已确认创建后续研究')
    expect(page.container.textContent).toContain('本地凭据清理未完成')
    expect(page.container.querySelector('input')?.disabled).toBe(true)
    expect(confirmed).not.toHaveBeenCalled()
  })
  it('keeps an already verified outcome when journal read fails during acknowledgement', async () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原理由')
    vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => {
      vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('read denied') })
      return commandReceipt('run-a', saved.intent.command)
    })
    const page = await render(<Harness />); await settled()
    expect(page.container.textContent).toContain('已确认创建后续研究')
    expect(page.container.textContent).toContain('本地凭据清理未完成')
    expect(page.container.querySelector('input')?.disabled).toBe(true)
    expect(page.container.querySelector('details pre')?.textContent).toContain(saved.intent.command.request_id)
  })
  it('notifies another mounted entry when an unmounted entry releases its synchronous busy guard', async () => {
    const delayed = deferred<ResearchRunCommandResult>()
    let result: ResearchRunCommandResult | undefined
    vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
    vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command) => { result = commandResult(id, command); return delayed.promise })
    const first = await render(<Harness />)
    await act(async () => button(first.container, '新重试').click())
    const second = await render(<Harness />); await settled()
    expect(button(second.container, '核对并按原操作重试').disabled).toBe(true)
    await act(async () => first.root.unmount()); roots.splice(roots.indexOf(first.root), 1)
    await act(async () => delayed.resolve(result!)); await settled()
    expect(button(second.container, '核对并按原操作重试').disabled).toBe(false)
    expect(readPendingCommand('run-a', 'owner')).not.toBeNull()
  })
  it('retains the original confirmed receipt if a fresh open lookup changes child identity', async () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原理由')
    const receipt = commandReceipt('run-a', saved.intent.command)
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(receipt)
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />); await settled()
    get.mockResolvedValue({ ...receipt, result: { ...receipt.result, target_run_id: 'different-child' } })
    await act(async () => button(page.container, '打开已确认的新研究').click()); await settled()
    expect(confirmed).not.toHaveBeenCalled()
    const raw = page.container.querySelector<HTMLDetailsElement>('[role="alert"] details')!
    expect(raw.open).toBe(false); expect(raw.textContent).toContain('different-child')
    expect(page.container.textContent).toContain('已确认创建后续研究')
  })
  it('blocks dispatch on failed storage and never POSTs a missing legacy receipt', async () => {
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
    const post = vi.spyOn(api, 'researchCommand')
    const page = await render(<Harness />)
    const save = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    await act(async () => button(page.container, '新重试').click()); await settled()
    expect(get).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled()
    save.mockRestore()
    pendingRequest('research-command', { runId: 'run-a', command: 'retry', reason: '旧理由' })
    await page.rerender(<Harness runId="run-b" />); await page.rerender(<Harness />); await settled()
    expect(button(page.container, '核对并按原操作重试').disabled).toBe(true)
    expect(page.container.textContent).toContain('旧版凭据')
    expect(post).not.toHaveBeenCalled()
  })
  it('fresh-checks an already confirmed child before explicit open and ignores late open after route changes', async () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原理由')
    const receipt = commandReceipt('run-a', saved.intent.command)
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(receipt)
    const confirmed = vi.fn(); const page = await render(<Harness onConfirmed={confirmed} />); await settled()
    const delayed = deferred<ResearchRunCommandReceipt | null>(); get.mockReturnValue(delayed.promise)
    await act(async () => button(page.container, '打开已确认的新研究').click())
    expect(get).toHaveBeenCalledTimes(2)
    window.history.replaceState(null, '', '/#/management')
    await act(async () => delayed.resolve(receipt)); await settled()
    expect(confirmed).not.toHaveBeenCalled(); expect(window.location.hash).toBe('#/management')
  })
})


describe('confirmed command target presentation', () => {
  it('shows the current confirmed child after navigation and restores explicit GET-only open on the original Run', async () => {
    window.history.replaceState(null, '', '/#/report?run=run-a')
    let committed: ResearchRunCommandReceipt | null = null
    const get = vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, owner) => {
      committed = commandReceipt(id, command, owner)
      return committed.result
    })
    const confirmed = vi.fn((receipt: ResearchRunCommandReceipt) => {
      window.history.replaceState(null, '', `/#/report?run=${receipt.result.target_run_id}`)
    })
    const page = await render(<Harness onConfirmed={confirmed} />)
    await act(async () => button(page.container, '新重试').click()); await settled()
    expect(post).toHaveBeenCalledTimes(1); expect(confirmed).toHaveBeenCalledTimes(1)
    expect(window.location.hash).toBe('#/report?run=child-run-a')
    await page.rerender(<Harness runId="child-run-a" onConfirmed={confirmed} />); await settled()
    expect(page.container.textContent).toContain('当前正在查看已确认的后续研究')
    expect(page.container.textContent).toContain('进度与报告以本页任务记录为准')
    expect(page.container.textContent).not.toContain('可显式打开新任务')
    expect([...page.container.querySelectorAll('button')].some(item => item.textContent === '打开已确认的新研究')).toBe(false)
    const raw = page.container.querySelector<HTMLDetailsElement>('[aria-label="任务操作回执"] details')!
    expect(raw.open).toBe(false)
    expect(JSON.parse(raw.querySelector('pre')!.textContent!)).toEqual(committed)
    const priorGets = get.mock.calls.length
    window.history.replaceState(null, '', '/#/report?run=run-a')
    await page.rerender(<Harness onConfirmed={confirmed} />); await settled()
    expect(page.container.textContent).toContain('可显式打开新任务')
    expect(page.container.textContent).not.toContain('当前正在查看已确认的后续研究')
    await act(async () => button(page.container, '打开已确认的新研究').click()); await settled()
    expect(get).toHaveBeenCalledTimes(priorGets + 1)
    expect(post).toHaveBeenCalledTimes(1); expect(confirmed).toHaveBeenCalledTimes(2)
    expect(listPendingRequests('research-command')).toEqual([])
  })
  it('does not attach another child or claim completion while a verified receipt cleanup remains pending', async () => {
    window.history.replaceState(null, '', '/#/report?run=run-a')
    const saved = createPendingCommand('run-a', 'owner', 'retry', '未清理的原操作')
    const receipt = commandReceipt('run-a', saved.intent.command)
    vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(receipt)
    const post = vi.spyOn(api, 'researchCommand')
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied') })
    const confirmed = vi.fn(), page = await render(<Harness onConfirmed={confirmed} />); await settled()
    expect(confirmed).not.toHaveBeenCalled()
    window.history.replaceState(null, '', '/#/report?run=child-run-a')
    await page.rerender(<Harness runId="child-run-a" onConfirmed={confirmed} />); await settled()
    expect(page.container.textContent).toContain('当前正在查看已确认的后续研究')
    expect(page.container.textContent).toContain('本地凭据清理未完成')
    expect([...page.container.querySelectorAll('button')].some(item => item.textContent === '打开已确认的新研究')).toBe(false)
    expect(readPendingCommand('run-a', 'owner')?.record).toEqual(saved.record)
    window.history.replaceState(null, '', '/#/report?run=different-child')
    await page.rerender(<Harness runId="different-child" onConfirmed={confirmed} />); await settled()
    expect(page.container.textContent).not.toContain('当前正在查看已确认的后续研究')
    expect(page.container.querySelector('[aria-label="任务操作回执"] pre')).toBeNull()
    expect(readPendingCommand('run-a', 'owner')?.record).toEqual(saved.record)
    expect(post).not.toHaveBeenCalled(); expect(confirmed).not.toHaveBeenCalled()
  })
})


// Controlled API returns test UI recovery/identity checks; they are not backend authenticity proof.
describe('late command confirmation after real hook unmount', () => {
  it('restores a late delivered 200 by the original fresh GET and opens its unique child only explicitly', async () => {
    window.history.replaceState(null, '', '/#/report?run=run-a')
    let committed: ResearchRunCommandReceipt | null = null
    const delayed = deferred<ResearchRunCommandResult>()
    const get = vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (id, command, owner) => {
      committed = commandReceipt(id, command, owner)
      return delayed.promise
    })
    const confirmed = vi.fn((receipt: ResearchRunCommandReceipt) => {
      window.history.replaceState(null, '', `/#/report?run=${receipt.result.target_run_id}`)
    })
    const first = await render(<Harness onConfirmed={confirmed} />)
    await act(async () => button(first.container, '新重试').click())
    const saved = readPendingCommand('run-a', 'owner')!
    const originalIntent = JSON.stringify(saved)
    window.history.replaceState(null, '', '/#/management')
    await act(async () => first.root.unmount()); roots.splice(roots.indexOf(first.root), 1)
    await act(async () => delayed.resolve(committed!.result)); await settled()
    expect(window.location.hash).toBe('#/management')
    expect(confirmed).not.toHaveBeenCalled()
    expect(get).toHaveBeenCalledTimes(2)
    expect(JSON.stringify(readPendingCommand('run-a', 'owner'))).toBe(originalIntent)

    const fresh = deferred<ResearchRunCommandReceipt | null>()
    get.mockImplementationOnce(() => fresh.promise)
    window.history.replaceState(null, '', '/#/report?run=run-a')
    const returned = await render(<Harness onConfirmed={confirmed} />); await settled()
    expect(get).toHaveBeenCalledTimes(3)
    expect(get).toHaveBeenLastCalledWith('run-a', saved.record.id, 'owner')
    expect(button(returned.container, '新重试').disabled).toBe(true)
    expect(returned.container.textContent).not.toContain('打开已确认的新研究')
    expect(confirmed).not.toHaveBeenCalled()
    await act(async () => fresh.resolve(committed)); await settled()
    expect(window.location.hash).toBe('#/report?run=run-a')
    expect(confirmed).not.toHaveBeenCalled()
    expect(listPendingRequests('research-command')).toEqual([])
    const open = button(returned.container, '打开已确认的新研究')
    expect(open.disabled).toBe(false)
    await act(async () => open.click()); await settled()
    expect(get).toHaveBeenCalledTimes(4)
    expect(get.mock.calls.every(args => JSON.stringify(args) === JSON.stringify(['run-a', saved.record.id, 'owner']))).toBe(true)
    expect(post).toHaveBeenCalledTimes(1)
    expect(confirmed).toHaveBeenCalledTimes(1)
    expect(confirmed).toHaveBeenLastCalledWith(committed)
    expect(window.location.hash).toBe('#/report?run=child-run-a')
  })

  it.each(['source', 'owner', 'missing'] as const)('retains original intent and blocks a new POST after a fresh %s receipt failure', async fault => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '原操作理由')
    const receipt = commandReceipt('run-a', saved.intent.command)
    const returned = fault === 'missing' ? null : {
      ...receipt,
      ...(fault === 'source' ? { source_run_id: 'another-source' } : { owner_id: 'another-owner' }),
    }
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(returned)
    const post = vi.spyOn(api, 'researchCommand')
    const confirmed = vi.fn(), page = await render(<Harness onConfirmed={confirmed} />); await settled()
    expect(get).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenLastCalledWith('run-a', saved.record.id, 'owner')
    expect(readPendingCommand('run-a', 'owner')).toEqual(saved)
    expect(button(page.container, '新重试').disabled).toBe(true)
    expect(page.container.textContent).not.toContain('打开已确认的新研究')
    await act(async () => button(page.container, '新重试').click()); await settled()
    expect(post).not.toHaveBeenCalled()
    expect(confirmed).not.toHaveBeenCalled()
    expect(get).toHaveBeenCalledTimes(1)
  })
})

describe('durable linked receipt discovery', () => {
  function LinkedHarness({ runId = 'child-run-a', owner = 'owner', onConfirmed = vi.fn() }: { runId?: string; owner?: string; onConfirmed?: (receipt: ResearchRunCommandReceipt) => void }) {
    const state = useRunCommand(runId, owner, onConfirmed)
    return <RunCommandRecovery state={state} />
  }
  const original = () => commandReceipt('run-a', { schema_version: 'research-run-command.v1', request_id: 'research-command:durable-original', command: 'recheck', reason: '刷新后保留的原公开证据更新理由' })

  it('restores the original accepted receipt after acknowledgement and a fresh mount with empty browser storage', async () => {
    const receipt = original()
    const saved = createPendingCommand('run-a', 'owner', 'recheck', receipt.command.reason)
    const accepted = commandReceipt('run-a', saved.intent.command)
    vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(accepted)
    const post = vi.spyOn(api, 'researchCommand')
    const first = await render(<Harness />); await settled()
    expect(listPendingRequests('research-command')).toEqual([])
    await act(async () => first.root.unmount()); roots.splice(roots.indexOf(first.root), 1)
    sessionStorage.clear()
    window.history.replaceState(null, '', '/#/report?run=child-run-a')
    const discovery = vi.mocked(api.researchLinkedCommandReceipt).mockResolvedValue(accepted)
    const navigate = vi.fn(), page = await render(<LinkedHarness onConfirmed={navigate} />); await settled()
    expect(discovery).toHaveBeenCalledWith('child-run-a', 'owner', expect.any(AbortSignal))
    expect(JSON.parse(page.container.querySelector('details pre')!.textContent!)).toEqual(accepted)
    expect(page.container.textContent).toContain('当前正在查看已确认的后续研究')
    expect(listPendingRequests('research-command')).toEqual([])
    expect(post).not.toHaveBeenCalled(); expect(navigate).not.toHaveBeenCalled()
  })

  it('fresh-checks a discovered original before an explicit open, without POST', async () => {
    const receipt = original()
    vi.mocked(api.researchLinkedCommandReceipt).mockImplementation(async id => id === 'child-run-a' ? receipt : null)
    const fresh = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(receipt)
    const post = vi.spyOn(api, 'researchCommand'), navigate = vi.fn()
    const page = await render(<LinkedHarness onConfirmed={navigate} />); await settled()
    expect(fresh).not.toHaveBeenCalled(); expect(navigate).not.toHaveBeenCalled()
    await page.rerender(<LinkedHarness runId="run-a" onConfirmed={navigate} />); await settled()
    // The source retains its in-memory original after route navigation, as before.
    await act(async () => button(page.container, '打开已确认的新研究').click()); await settled()
    expect(fresh).toHaveBeenCalledWith('run-a', receipt.request_id, 'owner')
    expect(post).not.toHaveBeenCalled(); expect(navigate).toHaveBeenCalledWith(receipt)
  })

  it('preserves an uncleaned original source journal when restoring the child after a fresh mount', async () => {
    const saved = createPendingCommand('run-a', 'owner', 'retry', '未清理的原操作')
    const receipt = commandReceipt('run-a', saved.intent.command)
    vi.mocked(api.researchLinkedCommandReceipt).mockResolvedValue(receipt)
    const post = vi.spyOn(api, 'researchCommand'), navigate = vi.fn()
    const page = await render(<LinkedHarness onConfirmed={navigate} />); await settled()
    expect(page.container.textContent).toContain('当前正在查看已确认的后续研究')
    expect(page.container.textContent).toContain('本地凭据清理未完成')
    expect(readPendingCommand('run-a', 'owner')?.record).toEqual(saved.record)
    expect(post).not.toHaveBeenCalled(); expect(navigate).not.toHaveBeenCalled()
  })

  it.each(['owner', 'target', 'source', 'rejected'] as const)('rejects a mismatched %s original without POST or navigation', async field => {
    const receipt = original()
    if (field === 'owner') receipt.owner_id = 'different-owner'
    if (field === 'target') receipt.result.target_run_id = 'another-child'
    if (field === 'source') receipt.result.source_run_id = 'another-source'
    if (field === 'rejected') { receipt.result.status = 'rejected'; receipt.result.target_run_id = null }
    vi.mocked(api.researchLinkedCommandReceipt).mockResolvedValue(receipt)
    const post = vi.spyOn(api, 'researchCommand'), navigate = vi.fn()
    const page = await render(<LinkedHarness onConfirmed={navigate} />); await settled()
    expect(page.container.querySelector('[role="alert"]')).not.toBeNull()
    expect(page.container.textContent).not.toContain('当前正在查看已确认的后续研究')
    expect(post).not.toHaveBeenCalled(); expect(navigate).not.toHaveBeenCalled()
  })

  it.each(['run', 'owner', 'hash', 'unmount'] as const)('ignores a delayed discovery after %s changes', async change => {
    const held = deferred<ResearchRunCommandReceipt | null>()
    vi.mocked(api.researchLinkedCommandReceipt).mockImplementationOnce(() => held.promise).mockResolvedValue(null)
    const navigate = vi.fn(), post = vi.spyOn(api, 'researchCommand')
    const page = await render(<LinkedHarness onConfirmed={navigate} />)
    if (change === 'run') await page.rerender(<LinkedHarness runId="another-child" onConfirmed={navigate} />)
    if (change === 'owner') await page.rerender(<LinkedHarness owner="different-owner" onConfirmed={navigate} />)
    if (change === 'hash') window.history.replaceState(null, '', '/#/technical?run=another-child')
    if (change === 'unmount') { await act(async () => page.root.unmount()); roots.splice(roots.indexOf(page.root), 1) }
    await act(async () => held.resolve(original())); await settled()
    expect(page.container.textContent).not.toContain('当前正在查看已确认的后续研究')
    expect(post).not.toHaveBeenCalled(); expect(navigate).not.toHaveBeenCalled()
  })

  it('leaves a missing original empty and preserves pending GET recovery without discovery or POST', async () => {
    const discovery = vi.mocked(api.researchLinkedCommandReceipt)
    const post = vi.spyOn(api, 'researchCommand'), page = await render(<LinkedHarness />); await settled()
    expect(page.container.querySelector('details pre')).toBeNull()
    discovery.mockClear()
    const saved = createPendingCommand('run-pending', 'owner', 'retry', '原理由')
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
    await page.rerender(<LinkedHarness runId="run-pending" />); await settled()
    expect(get).toHaveBeenCalledWith('run-pending', saved.record.id, 'owner')
    expect(discovery).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled()
    expect(readPendingCommand('run-pending', 'owner')).not.toBeNull()
  })

  it('uses the encoded GET route and frozen owner header with the existing receipt schema', async () => {
    vi.mocked(api.researchLinkedCommandReceipt).mockRestore()
    const receipt = original()
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(receipt), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    expect(await api.researchLinkedCommandReceipt('child/run?one', 'frozen-owner')).toEqual(receipt)
    expect(fetch).toHaveBeenCalledTimes(1)
    const [path, init] = fetch.mock.calls[0]
    expect(path).toBe('/v1/research/runs/child%2Frun%3Fone/linked-command-receipt')
    expect(init?.method ?? 'GET').toBe('GET')
    expect(new Headers(init?.headers).get('X-Owner-Id')).toBe('frozen-owner')
    expect(init?.body).toBeUndefined()
  })
})
