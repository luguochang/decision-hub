// @vitest-environment jsdom
import { reportDeliveryPageSchema } from '@decision-hub/contracts-ts'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createHash, webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { delivery, productOwner, type RevisionReadRequest } from '../api/delivery'
import { persistReadCommand } from '../research/readCommandRequests'
import { DeliveryPage } from './DeliveryPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const stamp = '2026-10-03T04:00:00Z'
const item = {
  revision_id: 'revision-read-1', study_id: 'study-read-1', source_run_id: 'run-read-1',
  title: '已保存报告的阅读标记', instrument: 'BTC-USDT-SWAP', strategy_version: 'research.v1',
  publication_status: 'research_only', created_at: stamp, local_written_at: stamp,
  external_dispatch_at: null, read_at: null, latest_feedback: null,
}
function page(readAt: string | null = null) {
  return reportDeliveryPageSchema.parse({ items: [{ ...item, read_at: readAt }], total: 1, limit: 12, offset: 0 })
}
function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
}
let root: Root, container: HTMLDivElement, client: QueryClient
beforeEach(() => {
  sessionStorage.clear()
  vi.stubGlobal('crypto', webcrypto)
  client = newClient()
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
  vi.spyOn(delivery, 'readCommandReceipt').mockResolvedValue(null)
})
afterEach(async () => {
  await act(async () => root.unmount())
  container.remove(); client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear()
})
async function settle() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
}
async function mount() {
  await act(async () => root.render(<QueryClientProvider client={client}><DeliveryPage params={new URLSearchParams()} onNavigate={vi.fn()} /></QueryClientProvider>))
  await settle()
}
function findButton(text: string) {
  return [...container.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === text)
}
async function click(button: HTMLButtonElement) {
  await act(async () => button.click())
  await settle()
}

function exactReceipt(value: RevisionReadRequest) {
  return {
    schema_version: 'read-command-receipt.v1' as const, request_id: value.id, revision_id: value.revision,
    owner_id: value.owner, command: { request_id: value.id, read: value.read },
    result: { revision_id: value.revision, owner_id: value.owner, read_at: value.read ? stamp : null, updated_at: stamp },
    payload_hash: createHash('sha256').update(JSON.stringify([value.owner, value.revision, 'read_receipt', { read: value.read, request_id: value.id }])).digest('hex'),
    confirmation_basis: 'stored_command_hash' as const,
  }
}

it('restores the original key and read intent after a lost response and a new root/client', async () => {
  const reports = vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read').mockRejectedValue(new Error('controlled response unavailable'))
  await mount()
  const firstButton = findButton('标记已读')
  expect(firstButton).toBeDefined()
  await click(firstButton!)
  expect(mark).toHaveBeenCalledTimes(1)
  const original = mark.mock.calls[0]
  expect(original).toEqual([item.revision_id, true, expect.any(String)])
  expect(container.textContent).toContain('标记未确认')

  // A controlled list projection now shows read, as it could after a committed
  // response was lost. This component test does not claim a real server commit.
  reports.mockResolvedValue(page(stamp))
  await act(async () => root.unmount())
  client.clear(); client = newClient(); root = createRoot(container)
  await mount()
  expect(mark).toHaveBeenCalledTimes(1) // Mount/reload must never replay a POST.
  const retry = findButton('核对原标记回执') ?? findButton('重试标记') ?? findButton('恢复未读')
  expect(retry).toBeDefined()
  await click(retry!)
  console.info('read_command_recovery_observation', JSON.stringify({
    scenario: 'new root/client with saved read projection', original, calls: mark.mock.calls,
    automatic_posts_on_remount: mark.mock.calls.length - 2,
  }))
  expect(mark).toHaveBeenCalledTimes(2)
  expect(mark.mock.calls[1]).toEqual(original)
})

it('does not replace an unconfirmed key through the ordinary mark button', async () => {
  vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read').mockRejectedValue(new Error('controlled response unavailable'))
  await mount()
  const firstButton = findButton('标记已读')
  expect(firstButton).toBeDefined()
  await click(firstButton!)
  expect(mark).toHaveBeenCalledTimes(1)
  const original = mark.mock.calls[0]
  expect(container.textContent).toContain('标记未确认')

  const ordinary = findButton('标记已读')
  if (ordinary && !ordinary.disabled) await click(ordinary)
  console.info('read_command_recovery_observation', JSON.stringify({
    scenario: 'ordinary button after an unconfirmed response', original, calls: mark.mock.calls,
  }))
  expect(mark.mock.calls.every(call => JSON.stringify(call) === JSON.stringify(original))).toBe(true)
  if (mark.mock.calls.length === 1) {
    const retry = findButton('核对原标记回执') ?? findButton('重试标记')
    expect(retry).toBeDefined()
    await click(retry!)
    expect(mark.mock.calls[1]).toEqual(original)
  }
})

it('retains a wrong full ACK and does not project an old confirmed read over the current list', async () => {
  vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read').mockResolvedValue({ revision_id: item.revision_id, owner_id: productOwner, read_at: stamp, updated_at: stamp })
  const lookup = vi.mocked(delivery.readCommandReceipt)
  lookup.mockImplementation(async value => ({ ...exactReceipt(value), request_id: 'another-command' }))
  await mount(); await click(findButton('标记已读')!)
  const original = mark.mock.calls[0]
  expect(mark).toHaveBeenCalledTimes(1)
  expect(sessionStorage.length).toBe(1)
  expect(findButton('标记已读')?.disabled).toBe(true)
  lookup.mockImplementation(async value => exactReceipt(value))
  await click(findButton('核对原标记回执')!)
  await vi.waitFor(() => expect(sessionStorage.length).toBe(0))
  expect(mark).toHaveBeenCalledTimes(1) // Existing original ACK needs no POST.
  expect(lookup.mock.calls[1][0]).toEqual({ revision: original[0], read: original[1], id: original[2], owner: productOwner })
  expect(container.textContent).toContain('原阅读请求已确认')
  expect(container.textContent).toContain('未标记已读')
  expect(findButton('恢复未读')).toBeUndefined()
})

it('does not turn a failed receipt lookup into permission to POST again', async () => {
  vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read').mockRejectedValue(new Error('controlled lost response'))
  await mount(); await click(findButton('标记已读')!)
  vi.mocked(delivery.readCommandReceipt).mockRejectedValue(new Error('HTTP 404'))
  await click(findButton('核对原标记回执')!)
  expect(mark).toHaveBeenCalledTimes(1)
  expect(sessionStorage.length).toBe(1)
  expect(findButton('标记已读')?.disabled).toBe(true)
})

it('restores the exact cleanup backup after a post-delete storage failure and an empty-list remount', async () => {
  const reports = vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read').mockResolvedValue({ revision_id: item.revision_id, owner_id: productOwner, read_at: stamp, updated_at: stamp })
  const lookup = vi.mocked(delivery.readCommandReceipt).mockImplementation(async value => exactReceipt(value))
  const originalGet = Storage.prototype.getItem, originalRemove = Storage.prototype.removeItem
  let inaccessible = false, injected = false
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key: string) {
    if (inaccessible) throw new Error('controlled storage unreadable after removal')
    return originalGet.call(this, key)
  })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (this: Storage, key: string) {
    originalRemove.call(this, key)
    if (!injected && key.startsWith('decision-hub.read-command.v1:') && !key.endsWith(':cleanup')) { inaccessible = true; injected = true }
  })
  await mount(); await click(findButton('标记已读')!)
  await vi.waitFor(() => expect(container.textContent).toContain('阅读标记尚未确认'))
  const original = mark.mock.calls[0]
  expect(mark).toHaveBeenCalledTimes(1)
  inaccessible = false
  expect(sessionStorage.length).toBe(1) // Primary is gone; durable backup survives.
  reports.mockResolvedValue(reportDeliveryPageSchema.parse({ items: [], total: 0, limit: 12, offset: 0 }))
  await act(async () => root.unmount())
  client.clear(); client = newClient(); root = createRoot(container)
  await mount()
  expect(mark).toHaveBeenCalledTimes(1)
  expect(container.textContent).toContain('当前没有符合筛选的报告')
  expect(findButton('核对原标记回执')).toBeDefined()
  await click(findButton('核对原标记回执')!)
  await vi.waitFor(() => expect(sessionStorage.length).toBe(0))
  expect(mark).toHaveBeenCalledTimes(1)
  expect(lookup.mock.calls[1][0]).toEqual({ revision: original[0], read: original[1], id: original[2], owner: productOwner })
})

it('blocks a second immediate click before React can rerender', async () => {
  vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const weak = { revision_id: item.revision_id, owner_id: productOwner, read_at: stamp, updated_at: stamp }
  let resolveMark: ((value: typeof weak) => void) | undefined
  const mark = vi.spyOn(delivery, 'read').mockImplementation(() => new Promise<typeof weak>(resolve => { resolveMark = resolve }))
  vi.mocked(delivery.readCommandReceipt).mockImplementation(async value => exactReceipt(value))
  await mount()
  const button = findButton('标记已读')!
  await act(async () => { button.click(); button.click() })
  expect(mark).toHaveBeenCalledTimes(1)
  await act(async () => resolveMark!(weak))
  await vi.waitFor(() => expect(sessionStorage.length).toBe(0))
  expect(mark).toHaveBeenCalledTimes(1)
})

it('refuses a readable journal belonging to another owner without dispatching', async () => {
  vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read')
  persistReadCommand({ revision: item.revision_id, read: true, id: 'read-owner-control', owner: productOwner })
  const key = sessionStorage.key(0)!
  const raw = sessionStorage.getItem(key)!
  const corrupted = JSON.parse(raw)
  corrupted.payload.owner = 'other-owner'
  corrupted.signature = JSON.stringify(corrupted.payload)
  sessionStorage.setItem(key, JSON.stringify(corrupted))
  await mount()
  expect(findButton('标记已读')?.disabled).toBe(true)
  expect(container.textContent).toContain('无法核对浏览器中的阅读凭据')
  expect(mark).not.toHaveBeenCalled()
  expect(delivery.readCommandReceipt).not.toHaveBeenCalled()
  expect(sessionStorage.getItem(key)).toBe(JSON.stringify(corrupted))
})

it('refuses conflicting main and cleanup tuples without removing or overwriting either', async () => {
  vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read')
  persistReadCommand({ revision: item.revision_id, read: true, id: 'read-main-control', owner: productOwner })
  const key = sessionStorage.key(0)!, original = sessionStorage.getItem(key)!
  const backup = JSON.stringify({ ...JSON.parse(original), id: 'different-cleanup-key' })
  sessionStorage.setItem(`${key}:cleanup`, backup)
  await mount()
  expect(findButton('标记已读')?.disabled).toBe(true)
  expect(mark).not.toHaveBeenCalled()
  expect(delivery.readCommandReceipt).not.toHaveBeenCalled()
  expect(sessionStorage.getItem(key)).toBe(original)
  expect(sessionStorage.getItem(`${key}:cleanup`)).toBe(backup)
})

it('retains a failed native backup removal and retries only the exact receipt', async () => {
  vi.spyOn(delivery, 'reports').mockResolvedValue(page())
  const mark = vi.spyOn(delivery, 'read').mockResolvedValue({ revision_id: item.revision_id, owner_id: productOwner, read_at: stamp, updated_at: stamp })
  const lookup = vi.mocked(delivery.readCommandReceipt).mockImplementation(async value => exactReceipt(value))
  const originalRemove = Storage.prototype.removeItem
  let failBackup = true
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(function (this: Storage, key: string) {
    if (failBackup && key.endsWith(':cleanup')) throw new Error('controlled native backup removal refused')
    originalRemove.call(this, key)
  })
  await mount(); await click(findButton('标记已读')!)
  await vi.waitFor(() => expect(container.textContent).toContain('浏览器凭据清理尚未确认'))
  const original = mark.mock.calls[0]
  expect(sessionStorage.length).toBe(1)
  expect(findButton('标记已读')?.disabled).toBe(true)
  failBackup = false
  await click(findButton('核对原标记回执')!)
  await vi.waitFor(() => expect(sessionStorage.length).toBe(0))
  expect(mark).toHaveBeenCalledTimes(1)
  expect(lookup.mock.calls[1][0]).toEqual({ revision: original[0], read: original[1], id: original[2], owner: productOwner })
})
