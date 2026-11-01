// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { actionMethodsApi, type MethodView } from '../api/actionMethods'
import { ApiRequestError } from '../api/client'
import { ActionMethodPanel } from './ActionMethodPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const authenticated: MethodView = {
  schema_version: 'research-method-view.v1',
  run_id: 'run-a',
  revision_id: 'revision-a',
  run_status: 'succeeded',
  current_generation: 1,
  producer_generation: 1,
  status: 'authenticated',
  adoption: 'adopted',
  methods: [{
    name: '事前研究方法', purpose: '核对方法轮次', skill_version_id: 'skill-a', version_hash: 'a'.repeat(64),
    origin: 'baseline_registered', financial_effect: 'unverified', generation: 1,
    selected: true, loaded: true, executed: true, consumed: true,
    required_nodes: ['observe'], executed_nodes: ['observe'], receipts: [],
  }],
  gaps: [],
}

describe('ActionMethodPanel method projection polling', () => {
  let container: HTMLDivElement
  let root: Root
  let client: QueryClient

  beforeEach(() => {
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  })

  afterEach(async () => {
    await act(async () => root.unmount())
    client.clear()
    container.remove()
    vi.restoreAllMocks()
  })

  async function renderPanel() {
    await act(async () => {
      root.render(<QueryClientProvider client={client}>
        <ActionMethodPanel runId="run-a" revisionId="revision-a" />
      </QueryClientProvider>)
      await new Promise(resolve => setTimeout(resolve, 75))
    })
  }

  const wait = (milliseconds: number) => act(async () => {
    await new Promise(resolve => setTimeout(resolve, milliseconds))
  })

  it('retries only the pending 503 after two seconds and displays the completed projection', async () => {
    const read = vi.spyOn(actionMethodsApi, 'read')
      .mockRejectedValueOnce(new ApiRequestError('method_projection_pending', 'http', 503))
      .mockResolvedValueOnce(authenticated)

    await renderPanel()
    expect(read).toHaveBeenCalledOnce()
    expect(container.textContent).toContain('正在核对方法使用记录')

    await wait(1_900)
    expect(read).toHaveBeenCalledOnce()
    await wait(250)

    expect(read).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('事前研究方法')
    expect(container.textContent).not.toContain('暂时无法核对方法原始记录')

    await wait(2_100)
    expect(read).toHaveBeenCalledTimes(2)
  })

  it('keeps an ordinary error on the existing manual retry path', async () => {
    const read = vi.spyOn(actionMethodsApi, 'read')
      .mockRejectedValue(new ApiRequestError('service_unavailable', 'http', 503))

    await renderPanel()
    expect(read).toHaveBeenCalledOnce()
    await wait(2_100)
    expect(read).toHaveBeenCalledOnce()
    expect(container.textContent).toContain('暂时无法核对方法原始记录')
    expect(container.textContent).toContain('重新读取方法记录')
  })

  it('does not keep polling after the panel unmounts', async () => {
    const read = vi.spyOn(actionMethodsApi, 'read')
      .mockRejectedValue(new ApiRequestError('method_projection_pending', 'http', 503))

    await renderPanel()
    expect(read).toHaveBeenCalledOnce()
    await act(async () => {
      root.render(<QueryClientProvider client={client}><div>已离开方法记录</div></QueryClientProvider>)
      await new Promise(resolve => setTimeout(resolve, 20))
    })

    await wait(2_100)
    expect(read).toHaveBeenCalledOnce()
  })
})
