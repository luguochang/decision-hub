// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { ProductPages } from './ProductPages'

vi.mock('./EventPrevisionPanel', () => ({ EventPrevisionPanel: () => <section>原事件观察</section> }))
vi.mock('./RiskDashboard', () => ({ RiskDashboard: () => <section>原现货观察</section> }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

it('puts the perpetual entry before existing home observations and navigates to saved settings without a question form', async () => {
  for (const name of ['operations', 'researchHome', 'macroEvents'] as const) vi.spyOn(api, name).mockImplementation(() => new Promise<never>(() => undefined))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), navigate = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ProductPages scene="home" onNavigate={navigate} /></QueryClientProvider>))
    const entry = host.querySelector('section[aria-label="BTC/ETH 永续事前建议"]')!
    expect(entry).not.toBeNull()
    expect(host.querySelector('section')).toBe(entry)
    expect(entry.textContent).toContain('已保存')
    expect(entry.textContent).toContain('4 小时')
    expect(entry.querySelector('input,textarea')).toBeNull()
    expect(host.textContent).toContain('原现货观察')
    expect(host.textContent).toContain('原事件观察')
    await act(async () => entry.querySelector('button')!.click())
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith('settings')
  } finally { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks() }
})
