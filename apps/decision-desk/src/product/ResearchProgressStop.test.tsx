// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ResearchRunDetailView, ResearchRunCommandReceipt } from '../api/types'
import { createPendingCommand, readPendingCommand } from '../research/commandRequests'
import { commandReceipt, commandResult } from '../research/commandTestFixtures'
import { ResearchProgressPage } from './ResearchProgressPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const roots: Root[] = [], clients: QueryClient[] = [], containers: HTMLElement[] = []
beforeEach(() => { sessionStorage.clear(); window.history.replaceState(null, '', '/#/queued?run=run-active') })
afterEach(async () => {
  await act(async () => roots.splice(0).forEach(root => root.unmount()))
  clients.splice(0).forEach(client => client.clear()); containers.splice(0).forEach(container => container.remove())
  sessionStorage.clear(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/')
})
const detail = (status = 'researching', runId = 'run-active') => ({ run: { run_id: runId, event_title: '美元与资金流研究', status, stage: 'acquiring_evidence', artifact_id: null, updated_at: '2026-10-05T08:00:00Z' }, evidence: [{}, {}], total_tool_calls: 4 }) as ResearchRunDetailView
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) }) }
const button = (container: HTMLElement, label: string) => [...container.querySelectorAll('button')].find(item => item.textContent === label)!
async function render(runId: string | undefined = 'run-active') {
  const container = document.createElement('div'); document.body.append(container); containers.push(container)
  const root = createRoot(container); roots.push(root)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }); clients.push(client)
  const navigate = vi.fn()
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchProgressPage runId={runId} back="#/studies?study=parent" onNavigate={navigate} /></QueryClientProvider>))
  await settle()
  return { container, root, navigate }
}

describe('stop a task from its progress page using the shared command protocol', () => {
  it.each(['admitted', 'queued', 'researching', 'retry_wait'])('stops %s once and keeps progress based on the saved run until it changes', async status => {
    const read = vi.spyOn(api, 'researchRun').mockResolvedValue(detail(status))
    let committed: ResearchRunCommandReceipt | null = null
    const get = vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (run, command, owner) => { committed = commandReceipt(run, command, owner); return committed.result })
    const submit = vi.spyOn(api, 'submitResearch'), page = await render()
    expect(button(page.container, '停止本次研究').disabled).toBe(false)
    await act(async () => { const stop = button(page.container, '停止本次研究'); stop.click(); stop.click() }); await settle()
    expect(post).toHaveBeenCalledTimes(1)
    expect(post).toHaveBeenCalledWith('run-active', expect.objectContaining({ command: 'cancel', reason: '用户在研究进度页停止本次任务。' }), 'owner')
    expect(get).toHaveBeenLastCalledWith('run-active', committed!.request_id, 'owner')
    expect(page.container.textContent).toContain('停止请求已确认')
    expect(page.container.querySelector('h1')?.textContent).not.toBe('本次研究已停止')
    expect(read).toHaveBeenCalledTimes(2); expect(page.navigate).not.toHaveBeenCalled(); expect(submit).not.toHaveBeenCalled()
    read.mockResolvedValue(detail('cancelled'))
    await act(async () => button(page.container, '刷新进度').click()); await settle()
    expect(page.container.querySelector('h1')?.textContent).toBe('本次研究已停止')
    expect(button(page.container, '停止本次研究')).toBeUndefined()
  })

  it('recovers a lost cancel response by GET after returning, preserving the same key and reason without POST', async () => {
    vi.spyOn(api, 'researchRun').mockResolvedValue(detail())
    let committed: ResearchRunCommandReceipt | null = null
    const get = vi.spyOn(api, 'researchCommandReceipt').mockImplementation(async () => committed)
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (run, command, owner) => { committed = commandReceipt(run, command, owner); throw new Error('controlled_response_loss_after_commit') })
    const first = await render()
    await act(async () => button(first.container, '停止本次研究').click()); await settle()
    const saved = readPendingCommand('run-active', 'owner')!
    expect(button(first.container, '请先核对原操作').disabled).toBe(true)
    expect(first.container.querySelector('[role="alert"] details')?.hasAttribute('open')).toBe(false)
    await act(async () => first.root.unmount()); roots.splice(roots.indexOf(first.root), 1)
    window.history.replaceState(null, '', '/#/home')
    window.history.replaceState(null, '', '/#/queued?run=run-active')
    const returned = await render()
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenLastCalledWith('run-active', saved.record.id, 'owner')
    expect(readPendingCommand('run-active', 'owner')).toBeNull()
    expect(returned.container.textContent).toContain('停止请求已确认')
    expect(committed!.command).toEqual(saved.intent.command)
    expect(returned.navigate).not.toHaveBeenCalled()
  })

  it('locks a pending original action and opens its verified retry target only after an explicit receipt check', async () => {
    vi.spyOn(api, 'researchRun').mockResolvedValue(detail())
    const saved = createPendingCommand('run-active', 'owner', 'retry', '原报告页重试理由')
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null)
    const post = vi.spyOn(api, 'researchCommand').mockImplementation(async (run, command, owner) => commandResult(run, command))
    const page = await render()
    expect(button(page.container, '请先核对原操作').disabled).toBe(true)
    expect(post).not.toHaveBeenCalled()
    expect(page.container.textContent).toContain('原报告页重试理由')
    get.mockResolvedValue(commandReceipt('run-active', saved.intent.command))
    await act(async () => button(page.container, '核对原操作回执').click()); await settle()
    expect(page.navigate).toHaveBeenCalledWith('queued', { run: 'child-run-active', back: '#/studies?study=parent' })
    expect(post).not.toHaveBeenCalled()
  })

  it('keeps recovery available while a cached active run is unconfirmed and disables a new stop', async () => {
    const read = vi.spyOn(api, 'researchRun').mockResolvedValueOnce(detail()).mockRejectedValue(new Error('controlled offline'))
    const get = vi.spyOn(api, 'researchCommandReceipt').mockResolvedValue(null), post = vi.spyOn(api, 'researchCommand')
    const page = await render()
    await act(async () => button(page.container, '刷新进度').click()); await settle()
    expect(page.container.querySelector('h1')?.textContent).toBe('最新进度暂时无法确认')
    expect(button(page.container, '停止本次研究').disabled).toBe(true)
    await act(async () => button(page.container, '停止本次研究').click())
    expect(post).not.toHaveBeenCalled(); expect(get).not.toHaveBeenCalled(); expect(read).toHaveBeenCalledTimes(2)
    const saved = createPendingCommand('run-active', 'owner', 'cancel', '原请求保留')
    await act(async () => page.root.unmount()); roots.splice(roots.indexOf(page.root), 1)
    get.mockResolvedValue(commandReceipt('run-active', saved.intent.command))
    const returned = await render()
    expect(returned.container.textContent).toContain('停止请求已确认')
    expect(post).not.toHaveBeenCalled()
  })

  it('does not stop a different run returned for the requested progress or show a stop for a terminal task', async () => {
    const read = vi.spyOn(api, 'researchRun').mockResolvedValue(detail('researching', 'run-other'))
    const post = vi.spyOn(api, 'researchCommand'), page = await render()
    expect(button(page.container, '停止本次研究').disabled).toBe(true)
    await act(async () => button(page.container, '停止本次研究').click())
    expect(post).not.toHaveBeenCalled()
    read.mockResolvedValue(detail('completed'))
    await act(async () => button(page.container, '刷新进度').click()); await settle()
    expect(button(page.container, '停止本次研究')).toBeUndefined()
  })
})
