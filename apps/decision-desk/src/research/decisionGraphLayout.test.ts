// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { layoutDecisionGraph } from './decisionGraphLayout'

vi.mock('elkjs/lib/elk-worker.min.js?worker', () => ({ default: function () { return worker } }))

let worker: {
  onmessage?: (event: { data: unknown }) => void
  onerror?: () => void
  postMessage: ReturnType<typeof vi.fn>
  terminate: ReturnType<typeof vi.fn>
}
function installWorker() {
  worker = { postMessage: vi.fn(), terminate: vi.fn() }

}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('returns positions from the worker and releases it', async () => {
  installWorker()
  const input = { id: 'graph', children: [{ id: 'a' }] }
  const promise = layoutDecisionGraph(input, new AbortController().signal)
  expect(worker.postMessage).toHaveBeenCalledWith(expect.objectContaining({ cmd: 'layout', graph: input }))
  const output = { ...input, children: [{ id: 'a', x: 10, y: 20 }] }
  worker.onmessage!({ data: { id: 1, data: output } })
  expect(await promise).toEqual(output)
  expect(worker.terminate).toHaveBeenCalledTimes(1)
})

it.each(['worker', 'layout', 'timeout', 'cancel'] as const)('releases the worker and rejects on %s failure', async (reason) => {
  vi.useFakeTimers()
  installWorker()
  const controller = new AbortController()
  const promise = layoutDecisionGraph({ id: 'graph' }, controller.signal)
  const failure = expect(promise).rejects.toThrow()
  if (reason === 'worker') worker.onerror!()
  if (reason === 'layout') { worker.onmessage!({ data: { id: 1, error: new Error('layout_failed') } }); await vi.advanceTimersByTimeAsync(1) }
  if (reason === 'cancel') controller.abort()
  if (reason === 'timeout') await vi.advanceTimersByTimeAsync(15_000)
  await failure
  expect(worker.terminate).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})
