import ELK, { type ElkNode } from 'elkjs/lib/elk-api'
import ElkWorker from 'elkjs/lib/elk-worker.min.js?worker'

// Layout runs off the UI thread and owns one short-lived worker per request.
export function layoutDecisionGraph(graph: ElkNode, signal: AbortSignal): Promise<ElkNode> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Layout cancelled', 'AbortError')); return }
    const worker = new ElkWorker()
    let timer: ReturnType<typeof setTimeout>
    const cleanup = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
      worker.terminate()
    }
    const abort = () => { cleanup(); reject(new DOMException('Layout cancelled', 'AbortError')) }
    worker.onerror = () => { cleanup(); reject(new Error('Graph layout worker unavailable')) }
    timer = setTimeout(() => { cleanup(); reject(new Error('Graph layout timed out')) }, 15_000)
    signal.addEventListener('abort', abort, { once: true })
    try {
      const elk = new ELK({ workerFactory: () => worker, algorithms: ['layered'] })
      void elk.layout(graph).then(result => { cleanup(); resolve(result) }, error => { cleanup(); reject(error) })
    } catch (error) { cleanup(); reject(error) }
  })
}
