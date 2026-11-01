// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { pendingRequest, pendingObservationText } from '../research/pendingRequests'
import { ObservationSubmitDialog } from './ObservationSubmitDialog'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const receipt = { event_id: 'evt-same', run_id: 'run-same', status_url: '/v1/runs/run-same' }
let container: HTMLDivElement, root: Root, client: QueryClient
const close = vi.fn(), accepted = vi.fn()
async function mount() {
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  await act(async () => root.render(<QueryClientProvider client={client}><ObservationSubmitDialog onClose={close} onReceipt={accepted} /></QueryClientProvider>))
}
async function fill(value: string) {
  await act(async () => {
    const field = container.querySelector('textarea')!
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const submit = () => container.querySelector<HTMLButtonElement>('.button.primary')!
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) }) }
afterEach(async () => {
  if (root) await act(async () => root.unmount())
  container?.remove(); client?.clear(); sessionStorage.clear(); vi.restoreAllMocks(); close.mockClear(); accepted.mockClear()
})

it('does not send empty text and bounds the editor to the API limit', async () => {
  const send = vi.spyOn(api, 'submitObservation')
  await mount(); await fill('   ')
  expect(submit().disabled).toBe(true)
  expect(container.querySelector('textarea')!.maxLength).toBe(200000)
  expect(send).not.toHaveBeenCalled()
})

it('persists before dispatch, freezes controls pending, acknowledges only a valid receipt', async () => {
  let release!: (value: typeof receipt) => void
  const send = vi.spyOn(api, 'submitObservation').mockImplementation(async (_text, key) => {
    expect(pendingRequest('observation-submit', { text: '事件正文' }).id).toBe(key)
    return new Promise(resolve => { release = resolve })
  })
  await mount(); await fill('  事件正文  ')
  await act(async () => submit().click()); await settle()
  expect(send.mock.calls[0][0]).toBe('事件正文')
  expect(container.querySelector('textarea')!.disabled).toBe(true)
  expect([...container.querySelectorAll('button')].every(button => button.disabled)).toBe(true)
  await act(async () => container.querySelector<HTMLDivElement>('.modal-backdrop')!.click())
  expect(close).not.toHaveBeenCalled()
  await act(async () => release(receipt)); await settle()
  expect(accepted).toHaveBeenCalledWith(receipt, window.location.hash)
  expect(pendingObservationText()).toBeUndefined()
})

it('retries a lost response with exactly the same payload and identity', async () => {
  const send = vi.spyOn(api, 'submitObservation').mockRejectedValueOnce(new Error('network lost')).mockResolvedValue(receipt)
  await mount(); await fill('恢复验收文本')
  await act(async () => submit().click()); await settle()
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('尚未确认提交结果')
  expect(container.querySelector('details')!.open).toBe(false)
  expect(submit().textContent).toContain('重试核对原回执')
  expect(accepted).not.toHaveBeenCalled()
  await act(async () => submit().click()); await settle()
  expect(send.mock.calls[1]).toEqual(send.mock.calls[0])
  expect(accepted).toHaveBeenCalledWith(receipt, window.location.hash)
})

it('restores pending text on reopening without replaying automatically', async () => {
  const saved = pendingRequest('observation-submit', { text: '刷新前未确认正文' })
  const send = vi.spyOn(api, 'submitObservation').mockResolvedValue(receipt)
  await mount()
  expect(container.querySelector('textarea')!.value).toBe('刷新前未确认正文')
  expect(container.textContent).toContain('系统不会自动发送')
  expect(send).not.toHaveBeenCalled()
  await act(async () => submit().click()); await settle()
  expect(send).toHaveBeenCalledWith('刷新前未确认正文', saved.id)
})

it('does not dispatch when the journal cannot save the receipt identity', async () => {
  const send = vi.spyOn(api, 'submitObservation')
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  await mount(); await fill('事件正文')
  await act(async () => submit().click()); await settle()
  expect(send).not.toHaveBeenCalled()
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('尚未发送请求')
})

it('changing intent gets a new identity while preserving the previous pending submission', async () => {
  const previous = pendingRequest('observation-submit', { text: '原正文' })
  const send = vi.spyOn(api, 'submitObservation').mockResolvedValue(receipt)
  await mount(); await fill('另一个事件正文')
  await act(async () => submit().click()); await settle()
  expect(send.mock.calls[0][1]).not.toBe(previous.id)
  expect(pendingObservationText()).toBe('原正文')
})
