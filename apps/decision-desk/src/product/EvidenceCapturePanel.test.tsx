// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { request } from '../api/client'
import { EvidenceCapturePanel } from './EvidenceCapturePanel'
vi.mock('../api/client', () => ({ request: vi.fn(), apiUrl: (s: string) => s }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals() })
it('does not label unread or failed archives as missing and permits retry', async () => {
  vi.mocked(request).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<EvidenceCapturePanel runId="run" evidenceId="evidence" />))
    expect(request).not.toHaveBeenCalled()
    expect(host.textContent).not.toContain('没有关联')
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('不能据此判断原文缺失')
    expect(host.textContent).not.toContain('没有关联')
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('没有关联的 HTTP 采集原文')
    expect(host.querySelector('[role="alert"]')).toBeNull()
  } finally { await act(async () => root.unmount()) }
})

const capture = (id: string, url: string) => ({ capture_id: id, url, body_ref: `raw://sha256/${'a'.repeat(64)}`, body_hash: 'a'.repeat(64), status_code: 200, byte_count: 7, media_type: 'application/json', started_at: '2026-10-05T15:25:53Z', completed_at: '2026-10-05T15:25:54Z' })

it('labels mixed source archives with each recorded endpoint and hash while preserving the full request set', async () => {
  const rows = [capture('gate-1', 'https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=BTC_USDT'), capture('htx-7', 'https://api.hbdm.com/linear-swap-api/v1/swap_index?contract_code=REDACTED')]
  vi.mocked(request).mockResolvedValueOnce(rows)
  const host = document.createElement('div'); const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><EvidenceCapturePanel runId="run" evidenceId="gate-evidence" /></QueryClientProvider>))
    expect(request).not.toHaveBeenCalled()
    await act(async () => host.querySelector('button')!.click())
    expect(request).toHaveBeenCalledWith('/v1/research/runs/run/evidence/gate-evidence/captures', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(host.textContent).toContain('可能包含其他来源')
    expect([...host.querySelectorAll('li strong')].map(item => item.textContent)).toEqual(['api.gateio.ws', 'api.hbdm.com'])
    for (const row of rows) {
      expect(host.textContent).toContain(row.url)
      expect(host.textContent).toContain(row.body_hash)
    }
    expect(host.querySelectorAll('li')).toHaveLength(2)
    expect([...host.querySelectorAll('li button')].map(item => item.textContent)).toEqual(['查看原始响应 1', '查看原始响应 2'])
  } finally { await act(async () => root.unmount()); client.clear() }
})

it('renders endpoint text and raw response markup as escaped text without creating executable source elements', async () => {
  const malicious = 'https://api.hbdm.com/path?symbol=<img src=x onerror=alert(1)>'
  vi.mocked(request).mockResolvedValueOnce([capture('raw-html', malicious)])
  const raw = '<script>window.__captureExecuted=true</script><img src=x onerror=alert(1)>'
  const fetchMock = vi.fn().mockResolvedValue(new Response(raw))
  vi.stubGlobal('fetch', fetchMock)
  const host = document.createElement('div'); const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><EvidenceCapturePanel runId="run" evidenceId="evidence" /></QueryClientProvider>))
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain(malicious)
    expect(host.querySelector('script, img')).toBeNull()
    await act(async () => { host.querySelector<HTMLButtonElement>('li button')!.click(); await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(host.querySelector('pre')?.textContent).toBe(raw)
    expect(host.querySelector('script, img')).toBeNull()
    expect(host.querySelector('li a')?.getAttribute('href')).toBe('/v1/raw-captures/raw-html/body')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  } finally { await act(async () => root.unmount()); client.clear() }
})

it('fails visibly on the old incomplete capture shape instead of inventing endpoint provenance', async () => {
  vi.mocked(request).mockResolvedValueOnce([{ capture_id: 'legacy', completed_at: '2026-10-05T15:25:54Z', status_code: 200, body_ref: null }])
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<EvidenceCapturePanel runId="run" evidenceId="evidence" />))
    await act(async () => host.querySelector('button')!.click())
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('不能据此判断原文缺失')
    expect(host.querySelector('li')).toBeNull()
  } finally { await act(async () => root.unmount()) }
})

it('shows the total and reveals all archived pages in batches without issuing a new lookup', async () => {
  vi.mocked(request).mockResolvedValueOnce(Array.from({ length: 43 }, (_, i) => capture(`raw-${i}`, `https://www.okx.com/raw?after=${i}`)))
  const host = document.createElement('div'); const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><EvidenceCapturePanel runId="run" evidenceId="spot" /></QueryClientProvider>))
    await act(async () => host.querySelector('button')!.click())
    expect(host.textContent).toContain('共 43 份原始响应，已显示 20 份')
    expect(host.querySelectorAll('li')).toHaveLength(20)
    const more = () => [...host.querySelectorAll('button')].find(button => button.textContent?.startsWith('再显示'))!
    await act(async () => more().click())
    expect(host.querySelectorAll('li')).toHaveLength(40)
    expect(more().textContent).toBe('再显示 3 份')
    await act(async () => more().click())
    expect(host.querySelectorAll('li')).toHaveLength(43)
    expect(more()).toBeUndefined()
    expect(request).toHaveBeenCalledTimes(1)
    await act(async () => root.render(<QueryClientProvider client={client}><EvidenceCapturePanel runId="run" evidenceId="another" /></QueryClientProvider>))
    expect(host.querySelectorAll('li')).toHaveLength(0)
    vi.mocked(request).mockResolvedValueOnce(Array.from({ length: 21 }, (_, i) => capture(`next-${i}`, 'https://www.okx.com/raw')))
    await act(async () => host.querySelector('button')!.click())
    expect(host.querySelectorAll('li')).toHaveLength(20)
  } finally { await act(async () => root.unmount()); client.clear() }
})

it('describes a historical original mismatch without claiming missing HTTP captures', async () => {
  vi.mocked(request).mockRejectedValueOnce(new Error('research_spot_capture_window_manifest_mismatch'))
  const host = document.createElement('div'); const root = createRoot(host)
  try {
    await act(async () => root.render(<EvidenceCapturePanel runId="run" evidenceId="spot" />))
    await act(async () => host.querySelector('button')!.click())
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('未用当前行情替代')
    expect(host.textContent).not.toContain('没有关联的 HTTP')
  } finally { await act(async () => root.unmount()) }
})
