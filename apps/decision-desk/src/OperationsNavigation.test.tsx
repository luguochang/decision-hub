// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OperationsPage } from './OperationsPage'
import { SourceAuditPage } from './SourceAuditPage'
import { DesignStatePage } from './DesignStatePage'
import { api } from './api/client'
import { assets } from './api/assets'
import type { OperationsOverview } from './api/types'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | null = null
let container: HTMLDivElement | null = null
let client: QueryClient | null = null
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  container?.remove(); container = null
  client?.clear(); client = null
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '/')
})
async function mount(element: React.ReactNode) {
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root!.render(<QueryClientProvider client={client!}>{element}</QueryClientProvider>))
}
const ledgerBack = '#/Inbox?kind=baseline&status=failed&offset=20&before=2026-10-01T01%3A00%3A00Z'
const auditBack = `#/Operations?${new URLSearchParams({ back: ledgerBack })}`

describe('operations navigation context', () => {
  it('passes the complete current audit route to both source and research history actions', async () => {
    window.history.replaceState(null, '', auditBack)
    const data = { checked_at: '2026-10-01T01:00:00Z', services: [], sources: [{ source_id: 'bls-calendar', enabled: true, status: 'degraded', consecutive_failures: 4, last_success_at: null, next_poll_at: null, error_code: 'source_access_denied' }], capabilities: [], jobs: { pending_owner_review: 0 }, runtime: { mode: 'provider', live_canary_status: 'not_run', provider_configured: true, model: null } } as unknown as OperationsOverview
    const navigate = vi.fn()
    await mount(<OperationsPage data={data} loading={false} failed={false} onNavigate={navigate} back={ledgerBack} />)
    const source = [...container!.querySelectorAll('button')].find(button => button.textContent === '核对采集记录')!
    await act(async () => source.click())
    expect(navigate).toHaveBeenLastCalledWith('sources', { source: 'bls-calendar', back: auditBack })
    const runs = [...container!.querySelectorAll('button')].find(button => button.textContent === '查看研究执行记录')!
    await act(async () => runs.click())
    expect(navigate).toHaveBeenLastCalledWith('runs', { back: auditBack })
    expect(container!.querySelector('a')!.getAttribute('href')).toBe(ledgerBack)
  })
  it('keeps the exact audit return link when source state and poll reads fail', async () => {
    vi.spyOn(api, 'operations').mockRejectedValue(new Error('read_timeout'))
    vi.spyOn(api, 'health').mockRejectedValue(new Error('read_timeout'))
    vi.spyOn(api, 'sourcePollAttempts').mockRejectedValue(new Error('read_timeout'))
    vi.spyOn(assets, 'sourceHistory').mockRejectedValue(new Error('read_timeout'))
    await mount(<SourceAuditPage params={new URLSearchParams({ source: 'bls-calendar', offset: '40', back: auditBack })} onNavigate={vi.fn()} />)
    const back = container!.querySelector<HTMLAnchorElement>('.asset-back-link')!
    expect(back.textContent).toContain('返回运行审计')
    expect(back.getAttribute('href')).toBe(auditBack)
  })
  it('keeps the audit return link through research history failure and filter reset', async () => {
    vi.spyOn(api, 'researchRunsPage').mockRejectedValue(new Error('read_timeout'))
    const navigate = vi.fn()
    await mount(<DesignStatePage scene="runs" params={new URLSearchParams({ q: 'old-run', status: 'failed', offset: '50', back: auditBack })} onNavigate={navigate} />)
    const back = [...container!.querySelectorAll('a')].find(link => link.textContent?.includes('返回运行审计'))!
    expect(back.getAttribute('href')).toBe(auditBack)
    await act(async () => [...container!.querySelectorAll('button')].find(button => button.textContent === '重置')!.click())
    expect(navigate).toHaveBeenLastCalledWith('runs', { back: auditBack })
  })
})
