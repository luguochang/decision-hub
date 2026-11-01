// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DesignStatePage, designCatalog } from './DesignStatePage'
import { ResearchLibraryPanel } from './research/ResearchLibraryPanel'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement | null = null

afterEach(() => {
  container?.remove()
  container = null
})

describe('Decision Hub design route matrix', () => {
  it('mounts every production-owned catalog state without a render exception', async () => {
    const apiFailure = vi.fn(async () => new Response(JSON.stringify({ detail: 'smoke unavailable' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', apiFailure)
    const productionScenes = designCatalog
      .map((item) => item.id)
      .filter((id) => !['report', 'report-incomplete', 'technical', 'evidence', 'graph', 'graph-node'].includes(id))
    for (const scene of productionScenes) {
      container = document.createElement('div')
      document.body.append(container)
      const root = createRoot(container)
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      await act(async () => {
        root.render(<QueryClientProvider client={queryClient}><DesignStatePage scene={scene as Parameters<typeof DesignStatePage>[0]['scene']} params={new URLSearchParams()} onNavigate={vi.fn()} /></QueryClientProvider>)
      })
      expect(container.textContent).toBeTruthy()
      await act(async () => { root.unmount() })
      queryClient.clear()
      container.remove()
      container = null
    }
    expect(apiFailure).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })

  it('surfaces a library API failure instead of showing an empty library', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'library unavailable' }), { status: 503, headers: { 'Content-Type': 'application/json' } })))
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><ResearchLibraryPanel /></QueryClientProvider>) })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)) })
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('资料列表暂时不可用')
    expect(container.querySelector('[role="alert"] button')?.textContent).toBe('重新读取记录')
    expect(container.textContent).toContain('library unavailable')
    await act(async () => { root.unmount() })
    queryClient.clear()
    vi.unstubAllGlobals()
  })
})
