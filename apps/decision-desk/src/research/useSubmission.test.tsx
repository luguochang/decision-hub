// @vitest-environment jsdom
import { act, StrictMode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { useSubmission } from './useSubmission'
import { SubmissionError, SubmissionRecovery } from './SubmissionRecovery'
import { listPendingRequests } from './pendingRequests'
import { receivedSubmissions } from './submissionReceipts'
import { api } from '../api/client'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const queued = { schema_version: 'research-run-queued.v1' as const, event_id: 'event', run_id: 'run-first', status: 'queued' as const, status_url: '/v1/runs/run-first' }
let root: Root | undefined, host: HTMLDivElement | undefined
const opened = vi.fn(), send = vi.fn<(payload: unknown, key: string) => Promise<typeof queued>>()
function Harness({ text = '原关注点', context = 'original-run' }: { text?: string; context?: string }) {
  const state = useSubmission('research-submit', { text }, context, send, receipt => opened(receipt))
  return <><button disabled={state.busy || state.blocked} onClick={state.execute}>提交</button><span>{state.busy ? '等待回执' : '可提交'}</span><SubmissionError error={state.error} /><SubmissionRecovery state={state} /></>
}
async function render(text = '原关注点', context = 'original-run') {
  if (!host) { host = document.createElement('div'); document.body.append(host); root = createRoot(host) }
  await act(async () => root!.render(<StrictMode><Harness text={text} context={context} /></StrictMode>))
}
async function pause() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) }) }
const submit = () => host!.querySelector('button')!
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  host?.remove(); root = undefined; host = undefined; vi.restoreAllMocks(); send.mockReset(); opened.mockReset(); sessionStorage.clear()
  receivedSubmissions('research-submit'); window.history.replaceState(null, '', '/')
})

it('has no dispatch on StrictMode mount and synchronously limits a double click to one POST', async () => {
  let release!: (receipt: typeof queued) => void
  send.mockImplementation(() => new Promise(resolve => { release = resolve }))
  await render(); expect(send).not.toHaveBeenCalled()
  await act(async () => { submit().click(); submit().click() }); await pause()
  expect(send).toHaveBeenCalledTimes(1)
  await act(async () => release(queued)); await pause()
  expect(opened).toHaveBeenCalledTimes(1)
})

it.each(['hashchange', 'popstate'])('does not resume old automatic navigation after leaving and returning through %s', async event => {
  let release!: (receipt: typeof queued) => void
  send.mockImplementation(() => new Promise(resolve => { release = resolve }))
  window.history.replaceState(null, '', '#/compose?focus=original&back=%23%2Fstudies')
  await render(); await act(async () => submit().click())
  await act(async () => {
    window.history.replaceState(null, '', '#/compose?focus=changed&back=%23%2Fruns')
    window.dispatchEvent(new Event(event))
    window.history.replaceState(null, '', '#/compose?focus=original&back=%23%2Fstudies')
    window.dispatchEvent(new Event(event))
  })
  await act(async () => release(queued)); await pause()
  expect(opened).not.toHaveBeenCalled(); expect(host?.textContent).toContain('收到接收回执')
  await act(async () => [...host!.querySelectorAll('button')].find(button => button.textContent === '打开已接收的研究任务')!.click())
  expect(opened).toHaveBeenCalledWith(queued)
})

it.each(['success', 'error'])('does not let an old %s clear or override a newer submission', async outcome => {
  let releaseOld!: (receipt: typeof queued) => void, rejectOld!: (error: Error) => void
  send.mockImplementationOnce(() => new Promise((resolve, reject) => { releaseOld = resolve; rejectOld = reject }))
    .mockResolvedValue({ ...queued, run_id: 'run-new', status_url: '/v1/runs/run-new' })
  await render(); await act(async () => submit().click()); await render('新关注点', 'different-run')
  await act(async () => submit().click()); await pause()
  expect(opened).toHaveBeenCalledTimes(1); expect(opened.mock.calls[0][0].run_id).toBe('run-new')
  await act(async () => { if (outcome === 'success') releaseOld(queued); else rejectOld(new Error('old_transport_500')) }); await pause()
  expect(opened).toHaveBeenCalledTimes(1)
  expect(host?.querySelector('[role="alert"]')).toBeNull()
  expect(send.mock.calls[0][1]).not.toBe(send.mock.calls[1][1])
})

it.each(['write', 'read-back'])('shows a late confirmed receipt across unmount even when receipt storage %s fails, with no replay', async failure => {
  let release!: (receipt: typeof queued) => void
  send.mockImplementation(() => new Promise(resolve => { release = resolve }))
  await render(); await act(async () => submit().click())
  const saved = listPendingRequests('research-submit')[0]
  await act(async () => root!.unmount()); host?.remove(); root = undefined; host = undefined
  const get = Storage.prototype.getItem
  let receiptReads = 0
  const fail = failure === 'write' ? vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    : vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, name: string) {
      if (name.includes('received-submissions') && ++receiptReads > 1) throw new Error('readback_denied')
      return get.call(this, name)
    })
  await act(async () => release(queued)); await pause()
  if (failure === 'write') fail.mockRestore()
  await render()
  expect(opened).not.toHaveBeenCalled(); expect(send).toHaveBeenCalledTimes(1)
  expect(document.body.textContent).toContain('本标签页可打开该任务')
  expect(listPendingRequests('research-submit')[0]).toEqual(saved)
  await act(async () => [...host!.querySelectorAll('button')].find(button => button.textContent === '打开已接收的研究任务')!.click())
  expect(opened).toHaveBeenCalledWith(queued)
})

it('shows verified receipt cleanup failure and does not resubmit the acknowledged original key', async () => {
  send.mockResolvedValue(queued)
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('keep_pending') })
  await render(); await act(async () => submit().click()); await pause()
  expect(opened).not.toHaveBeenCalled()
  expect(host?.textContent).toContain('原本地凭据尚未清理')
  await act(async () => submit().click()); await pause()
  expect(send).toHaveBeenCalledTimes(1)
})

it('allows a new explicitly requested research with the same focus after the previous key was acknowledged', async () => {
  send.mockResolvedValue(queued)
  await render(); await act(async () => submit().click()); await pause()
  await act(async () => submit().click()); await pause()
  expect(send).toHaveBeenCalledTimes(2)
  expect(send.mock.calls[0][1]).not.toBe(send.mock.calls[1][1])
})

it('keeps technical failure text closed and retries an unconfirmed request only after an explicit click', async () => {
  send.mockRejectedValueOnce(new Error('controlled_response_withheld HTTP 503')).mockResolvedValue(queued)
  await render(); await act(async () => submit().click()); await pause()
  const key = send.mock.calls[0][1]
  expect(send).toHaveBeenCalledTimes(1)
  const details = host!.querySelector<HTMLDetailsElement>('[role="alert"] details')!
  expect(details.open).toBe(false); expect(details.textContent).toContain('controlled_response_withheld HTTP 503')
  expect(host!.querySelector('[role="alert"] p')?.textContent).not.toContain('controlled_response_withheld')
  await act(async () => submit().click()); await pause()
  expect(send.mock.calls[1]).toEqual([{ text: '原关注点' }, key])
})

it('refuses to claim a receipt when the pending key was rebound while awaiting it and keeps the original response only for audit', async () => {
  let release!: (receipt: typeof queued) => void
  send.mockImplementation(() => new Promise(resolve => { release = resolve }))
  await render(); await act(async () => submit().click())
  const original = listPendingRequests('research-submit')[0]
  const key = Object.keys(sessionStorage).find(item => item.includes('pending.v1'))!
  const changed = { ...original, payload: { text: '重签的不同正文' }, signature: JSON.stringify({ text: '重签的不同正文' }) }
  sessionStorage.setItem(key, JSON.stringify([changed]))
  await act(async () => release(queued)); await pause()
  expect(opened).not.toHaveBeenCalled()
  expect(host?.querySelector('[aria-label="已收到的提交回执"]')).toBeNull()
  expect(host?.textContent).toContain('提交凭据发生冲突')
  const audit = host!.querySelector<HTMLDetailsElement>('[aria-label="提交凭据冲突"] details')!
  expect(audit.open).toBe(false); expect(audit.textContent).toContain(queued.run_id); expect(audit.textContent).toContain('原关注点')
  expect(listPendingRequests('research-submit')).toEqual([changed])
})

it.each(['research_submission_legacy_binding_unavailable', 'research_submission_intent_conflict', 'research_submission_receipt_corrupt'])('retains the original pending intent after real HTTP409 %s and offers manual record review without a retry promise', async code => {
  const request = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: code }), { status: 409 }))
  send.mockImplementation((payload, key) => api.submitResearch((payload as { text: string }).text, key))
  await render(); await act(async () => submit().click()); await pause()
  expect(request).toHaveBeenCalledTimes(1)
  expect(opened).not.toHaveBeenCalled(); expect(listPendingRequests('research-submit')).toHaveLength(1)
  expect(listPendingRequests('research-submit')[0].payload).toEqual({ text: '原关注点' })
  expect(host?.querySelector('[aria-label="已收到的提交回执"]')).toBeNull()
  const alert = host!.querySelector('[role="alert"]')!
  expect(alert.querySelector('a')?.getAttribute('href')).toBe('#/runs')
  expect(alert.querySelector('p')?.textContent).toContain('凭据已保留')
  expect(alert.querySelector('p')?.textContent).not.toContain('稍后按原内容重试')
  expect(alert.querySelector('p')?.textContent).not.toContain(code)
  expect(alert.querySelector<HTMLDetailsElement>('details')?.open).toBe(false)
  expect(alert.querySelector('details')?.textContent).toContain(code)
  expect(submit().disabled).toBe(true)
  await act(async () => submit().click()); await pause()
  expect(request).toHaveBeenCalledTimes(1)
})

it.each(['receipt', 'request'])('quarantines a stored %s that drifts from the known volatile binding instead of silently keeping an openable result', async field => {
  send.mockResolvedValue(queued)
  await render(); await act(async () => submit().click()); await pause()
  expect(opened).toHaveBeenCalledTimes(1)
  const key = Object.keys(sessionStorage).find(item => item.includes('received-submissions'))!
  const rows = JSON.parse(sessionStorage.getItem(key)!)
  if (field === 'receipt') rows[0].receipt = { ...queued, run_id: 'stored-different-run', status_url: '/v1/runs/stored-different-run' }
  else rows[0].request = { ...rows[0].request, payload: { text: 'stored-different-intent' }, signature: JSON.stringify({ text: 'stored-different-intent' }) }
  const changed = JSON.stringify(rows)
  sessionStorage.setItem(key, changed)
  await render()
  expect(host?.querySelector('[aria-label="已收到的提交回执"]')).toBeNull()
  expect(host?.textContent).toContain('提交凭据发生冲突')
  const audit = host!.querySelector<HTMLDetailsElement>('[aria-label="提交凭据冲突"] details')!
  expect(audit.open).toBe(false); expect(audit.textContent).toContain('run-first'); expect(audit.textContent).toContain(field === 'receipt' ? 'stored-different-run' : 'stored-different-intent')
  expect(sessionStorage.getItem(key)).toBe(changed)
  expect(opened).toHaveBeenCalledTimes(1); expect(send).toHaveBeenCalledTimes(1)
})
