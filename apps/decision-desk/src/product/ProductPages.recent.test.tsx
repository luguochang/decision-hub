// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ResearchRunView } from '../api/types'
import { ProductPages } from './ProductPages'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('reads eight recent tasks through the bounded endpoint and keeps full-record and task navigation', async () => {
  const items = Array.from({ length: 8 }, (_, i) => ({ run_id: `recent-${i}`, event_title: `新近研究 ${i}`, artifact_id: null, status: 'failed', admission_origin: 'automatic', updated_at: `2026-10-11T0${8-i}:00:00Z` } as ResearchRunView))
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  const full = vi.spyOn(api, 'researchRuns').mockImplementation(() => new Promise<never>(() => undefined))
  const recent = vi.spyOn(api, 'researchRecentRuns').mockResolvedValue(items)
  const navigate = vi.fn()
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="research" onNavigate={navigate} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !container.textContent?.includes('新近研究 0'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(recent).toHaveBeenCalledOnce()
    expect(full).not.toHaveBeenCalled()
    expect(container.querySelectorAll('.product-row')).toHaveLength(8)
    expect(container.querySelector('.product-row strong')?.textContent).toContain('新近研究 0')
    await act(async () => (container.querySelector('.product-row') as HTMLButtonElement).click())
    expect(navigate).toHaveBeenCalledWith('failed', { run: 'recent-0', back: '#/research' })
    const records = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('完整任务记录'))!
    await act(async () => records.click())
    expect(navigate).toHaveBeenCalledWith('runs', { back: '#/research' })
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks() }
})

it('keeps list errors visible rather than claiming an empty history', async () => {
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(api, 'researchRecentRuns').mockRejectedValue(new Error('synthetic unavailable'))
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="research" onNavigate={vi.fn()} /></QueryClientProvider>))
    for (let i = 0; i < 50 && !container.textContent?.includes('研究账本暂不可用'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.textContent).toContain('研究账本暂不可用')
    expect(container.textContent).not.toContain('目前没有可查看的研究记录')
  } finally { await act(async () => root.unmount()); client.clear(); container.remove(); vi.restoreAllMocks() }
})
