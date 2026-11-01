// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api/client'
import type { OperationsOverview, ProductHealth } from './api/types'
import type { SourcePollAttempt } from './api/sourceAudit'
import { SourceAuditPage } from './SourceAuditPage'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const operations = {
  services: [
    { service_id: 'hub-api', role: 'api', status: 'online', heartbeat_at: '2026-09-27T01:00:00Z', last_error_code: null },
    { service_id: 'hub-research-worker', role: 'research_worker', status: 'offline', heartbeat_at: null, last_error_code: 'worker_offline' },
  ],
  sources: [{ source_id: 'fed-calendar', enabled: true, status: 'degraded', last_success_at: '2026-09-26T10:00:00Z', next_poll_at: '2026-09-27T02:00:00Z' }],
} as OperationsOverview

const health = {
  status: 'degraded', running_runs: 0, failed_runs: 1,
  sources: [{ source_id: 'fed-calendar', status: 'degraded', cursor: null, last_success_at: '2026-09-26T10:00:00Z', last_error_at: '2026-09-27T01:00:00Z', consecutive_failures: 1, latency_ms: 1200, error_code: 'source_timeout', next_poll_at: '2026-09-27T02:00:00Z' }],
} as ProductHealth

const attempt = (status: string, id: string, errorCode: string | null = null): SourcePollAttempt => ({
  attempt_id: id, source_id: 'fed-calendar', trigger_type: 'scheduled', status,
  started_at: '2026-09-27T01:00:00Z', completed_at: '2026-09-27T01:00:01Z',
  cursor_before: null, cursor_after: null, received_count: status === 'succeeded' ? 2 : 0,
  accepted_count: status === 'succeeded' ? 1 : 0, duplicate_count: 0, revision_count: 0,
  latency_ms: 1000, error_code: errorCode,
})

let container: HTMLDivElement | null = null
afterEach(() => { vi.restoreAllMocks(); container?.remove(); container = null })

describe('source audit', () => {
  it('does not present cached healthy states as current after a failed refresh', async () => {
    vi.spyOn(api, 'operations').mockRejectedValue(new Error('unavailable'))
    vi.spyOn(api, 'health').mockRejectedValue(new Error('unavailable'))
    vi.spyOn(api, 'sourcePollAttempts').mockResolvedValue([attempt('succeeded', 'historical')])
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['operations'], { ...operations, services: operations.services.map((service) => ({ ...service, status: 'online' })), sources: operations.sources.map((source) => ({ ...source, status: 'healthy' })) })
    client.setQueryData(['product-health'], { ...health, status: 'ok', sources: health.sources.map((source) => ({ ...source, status: 'healthy' })) })
    await act(async () => { root.render(<QueryClientProvider client={client}><SourceAuditPage params={new URLSearchParams({ source: 'fed-calendar' })} onNavigate={vi.fn()} /></QueryClientProvider>) })
    for (let i = 0; i < 10 && !container.textContent?.includes('来源状态未确认'); i += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
    expect(container.textContent).toContain('运行进程状态不可用')
    expect(container.textContent).toContain('来源健康状态不可用')
    expect(container.textContent).toContain('来源状态未确认')
    expect(container.textContent).not.toContain('当前没有已登记的来源')
    expect(container.textContent).not.toContain('来源未登记')
    expect(container.querySelector('.design-source-grid .status-pill.success')).toBeNull()
    expect(container.querySelector('.page-heading .status-pill.success')).toBeNull()
    expect(container.textContent).toContain('采集成功')
    await act(async () => { root.unmount() })
    client.clear()
  })

  it('shows separate process health and real success, empty, failure attempt records', async () => {
    vi.spyOn(api, 'operations').mockResolvedValue(operations)
    vi.spyOn(api, 'health').mockResolvedValue(health)
    const loadAttempts = vi.spyOn(api, 'sourcePollAttempts').mockResolvedValue([attempt('succeeded', 'one'), attempt('empty', 'two'), attempt('failed', 'three', 'source_timeout')])
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const onNavigate = vi.fn()
    await act(async () => { root.render(<QueryClientProvider client={client}><SourceAuditPage params={new URLSearchParams({ source: 'fed-calendar' })} onNavigate={onNavigate} /></QueryClientProvider>) })
    for (let i = 0; i < 10 && !container.textContent?.includes('采集失败'); i += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
    expect(container.textContent).toContain('来源发现进程')
    expect(container.textContent).toContain('未单独上报发现进程')
    expect(container.textContent).toContain('自动研究通道')
    expect(container.textContent).toContain('用户研究通道')
    expect(container.textContent).toContain('最近成功')
    expect(container.textContent).toContain('下次计划')
    expect(container.textContent).toContain('采集成功')
    expect(container.textContent).toContain('本次无新内容')
    expect(container.textContent).toContain('采集失败')
    expect(loadAttempts).toHaveBeenCalledWith('fed-calendar', 21, 0)
    const details = container.querySelectorAll('details')
    expect(details.length).toBeGreaterThan(0)
    expect([...details].every((item) => !item.open)).toBe(true)
    expect([...details].some((item) => item.textContent?.includes('source_timeout'))).toBe(true)
    expect(container.querySelector<HTMLButtonElement>('[aria-label="轮询记录分页"] button:last-child')?.disabled).toBe(true)
    await act(async () => { root.unmount() })
    client.clear()
  })

  it('keeps the selected source and offset in the page URL', async () => {
    vi.spyOn(api, 'operations').mockResolvedValue(operations)
    vi.spyOn(api, 'health').mockResolvedValue(health)
    const loadAttempts = vi.spyOn(api, 'sourcePollAttempts').mockResolvedValue(Array.from({ length: 21 }, (_, index) => attempt('empty', String(index))))
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const onNavigate = vi.fn()
    await act(async () => { root.render(<QueryClientProvider client={client}><SourceAuditPage params={new URLSearchParams({ source: 'fed-calendar', offset: '40', back: '#/market' })} onNavigate={onNavigate} /></QueryClientProvider>) })
    for (let i = 0; i < 10 && !container.textContent?.includes('第 3 页'); i += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
    expect(loadAttempts).toHaveBeenCalledWith('fed-calendar', 21, 40)
    expect(container.querySelector('a[href="#/market"]')?.textContent).toContain('返回市场与事件')
    expect(container.querySelectorAll('tbody tr')).toHaveLength(20)
    const next = container.querySelector<HTMLButtonElement>('[aria-label="轮询记录分页"] button:last-child')!
    expect(next.disabled).toBe(false)
    await act(async () => { next.click() })
    expect(onNavigate).toHaveBeenCalledWith('sources', { source: 'fed-calendar', offset: '60', back: '#/market' })
    await act(async () => { root.unmount() })
    client.clear()
  })

  it('recognizes the real-time worker as the source discovery process', async () => {
    vi.spyOn(api, 'operations').mockResolvedValue({ ...operations, services: [...operations.services, { service_id: 'hub-realtime-worker', role: 'realtime_worker', status: 'online', heartbeat_at: '2026-09-27T01:02:00Z', last_error_code: null } as OperationsOverview['services'][number]] })
    vi.spyOn(api, 'health').mockResolvedValue(health)
    vi.spyOn(api, 'sourcePollAttempts').mockResolvedValue([])
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    await act(async () => { root.render(<QueryClientProvider client={client}><SourceAuditPage params={new URLSearchParams()} onNavigate={vi.fn()} /></QueryClientProvider>) })
    for (let i = 0; i < 10 && ![...container.querySelectorAll('.design-source-row')].some((item) => item.textContent?.includes('来源发现进程') && item.textContent?.includes('在线')); i += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
    const discovery = [...container.querySelectorAll('.design-source-row')].find((item) => item.textContent?.includes('来源发现进程'))
    expect(discovery?.textContent).toContain('在线')
    expect(discovery?.textContent).toContain('最近心跳')
    expect(discovery?.textContent).not.toContain('未单独上报')
    await act(async () => { root.unmount() })
    client.clear()
  })
})


it('refreshes poll records after an error and distinguishes both research channels', async () => {
  vi.spyOn(api, 'operations').mockResolvedValue({ ...operations, services: [...operations.services, { service_id: 'hub-research-priority-worker', role: 'research_worker', status: 'online', heartbeat_at: '2026-09-27T01:02:00Z', last_error_code: null } as OperationsOverview['services'][number]] })
  vi.spyOn(api, 'health').mockResolvedValue(health)
  const requests = vi.spyOn(api, 'sourcePollAttempts').mockRejectedValueOnce(new Error('unavailable')).mockResolvedValue([attempt('failed', 'fresh-403', 'http_403')])
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['source-poll-attempts', 'fed-calendar', 0], [attempt('empty', 'cached')])
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><SourceAuditPage params={new URLSearchParams({ source: 'fed-calendar' })} onNavigate={vi.fn()} /></QueryClientProvider>))
    for (let i = 0; i < 30 && !container.textContent?.includes('轮询记录暂时无法更新'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.textContent).toContain('以下保留上次读取的记录')
    expect(container.textContent).toContain('本次无新内容')
    const rows = Array.from(container.querySelectorAll('.design-source-row'))
    expect(rows.find(row => row.textContent?.includes('自动研究通道'))?.textContent).toContain('未在线')
    expect(rows.find(row => row.textContent?.includes('用户研究通道'))?.textContent).toContain('在线')
    const refresh = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '刷新轮询记录')!
    await act(async () => refresh.click())
    for (let i = 0; i < 30 && !container.textContent?.includes('http_403'); i += 1) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(requests).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('采集失败')
    expect(container.textContent).toContain('http_403')
    expect(container.textContent).not.toContain('轮询记录暂时无法更新')
    expect(container.textContent).not.toContain('本次无新内容')
  } finally { await act(async () => root.unmount()); client.clear() }
})


it('explains access denial, preserves historical HTTP failures and refreshes current source health', async () => {
  const op = vi.spyOn(api, 'operations').mockResolvedValue({ ...operations, sources: operations.sources.map(source => ({ ...source, error_code: 'source_access_denied' })) })
  const check = vi.spyOn(api, 'health').mockResolvedValue({ ...health, sources: health.sources.map(source => ({ ...source, error_code: 'source_access_denied' })) })
  const denied = { ...attempt('failed', 'old-denial', 'source_unavailable'), content_snapshot: { http_status: 403, parser_version: 'v2', content_hash: 'hash', parse_status: 'not_attempted' as const, parsed_count: null, upcoming_count: null, first_upcoming_date: null, last_upcoming_date: null, coverage_status: 'unknown' as const } }
  vi.spyOn(api, 'sourcePollAttempts').mockResolvedValue([denied])
  container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><SourceAuditPage params={new URLSearchParams({ source: 'fed-calendar', offset: '20' })} onNavigate={vi.fn()} /></QueryClientProvider>))
    for (let i = 0; i < 30 && !container.textContent?.includes('自动采集已延后'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(container.textContent).toContain('至少间隔 24 小时')
    expect(container.textContent).toContain('来源拒绝访问（HTTP 403）')
    expect(container.textContent).toContain('刷新仅重新读取已保存的状态和记录')
    check.mockResolvedValue({ ...health, sources: health.sources.map(source => ({ ...source, error_code: null, status: 'healthy' })) })
    // The separate operations snapshot can lag a newly recovered health response.
    await act(async () => Array.from(container!.querySelectorAll('button')).find(button => button.textContent === '刷新轮询记录')!.click())
    for (let i = 0; i < 30 && container.textContent?.includes('自动采集已延后'); i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
    expect(check).toHaveBeenCalledTimes(2)
    expect(op).toHaveBeenCalledTimes(2)
    expect(container.textContent).not.toContain('自动采集已延后')
    expect(container.textContent).toContain('最近采集可用')
    expect(container.textContent).toContain('来源拒绝访问（HTTP 403）')
  } finally { await act(async () => root.unmount()); client.clear() }
})
