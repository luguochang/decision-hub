// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { settingsApi } from '../api/decisionSettings'
import { ProductPages } from './ProductPages'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
it('opens settings and explicit paper account controls in the existing product route', async () => {
  vi.spyOn(api, 'operations').mockImplementation(() => new Promise<never>(() => undefined))
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const navigate = vi.fn()
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><Suspense fallback={<p>读取中</p>}><ProductPages scene="settings" onNavigate={navigate} /></Suspense></QueryClientProvider>))
    for (let index = 0; index < 200 && !host.querySelector('section[aria-label="我的仓位与风险设置"]'); index++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(host.querySelector('section[aria-label="我的仓位与风险设置"]')).not.toBeNull()
    expect(host.querySelector('section[aria-label="永续模拟账户"]')).not.toBeNull()
    expect(host.querySelector('h1')?.textContent).toBe('仓位与风险设置')
    await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '返回今日研究')!.click())
    expect(navigate).toHaveBeenCalledWith('home')
  } finally { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); sessionStorage.clear() }
})
