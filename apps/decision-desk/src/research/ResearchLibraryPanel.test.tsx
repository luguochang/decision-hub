// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ResearchDocumentVersion } from '../api/types'
import { ResearchLibraryPanel } from './ResearchLibraryPanel'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: ReturnType<typeof createRoot>
let host: HTMLDivElement
let client: QueryClient
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })
async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 30)) }) }
it('waits for completed parsing, retrieves real blocks, paginates, and resets on version selection', async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const version = { version_id: 'v1', document_id: 'd1', status: 'parsing', parse_receipt_ref: null, claim_count: 0, quality: { quality_status: 'pending', issues: [], text_readability: 0, locator_completeness: 0, numeric_suspicion: 0 } } as unknown as ResearchDocumentVersion
  vi.spyOn(api, 'researchDocuments').mockResolvedValue([])
  vi.spyOn(api, 'researchDocumentVersion').mockImplementation(async (id) => ({ ...version, version_id: id }))
  vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
  const readBlocks = vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue(Array.from({ length: 770 }, (_, i) => ({ block_id: `v1:block:${i + 1}`, content: `Source paragraph ${i + 1}`, page_no: 1, confidence: 1, needs_review: false })) as Awaited<ReturnType<typeof api.researchDocumentBlocks>>)
  const render = async (id: string) => { await act(async () => root.render(<QueryClientProvider client={client}><ResearchLibraryPanel routeVersionId={id} /></QueryClientProvider>)); await settle() }
  await render('v1')
  expect(readBlocks).not.toHaveBeenCalled()
  expect(host.textContent).not.toContain('原始解析块（0）')
  version.status = 'parsed'; version.parse_receipt_ref = 'receipt://v1'
  await act(async () => { client.setQueryData(['research-library-version', 'v1'], { ...version }) }); await settle()
  for (let i = 0; i < 50 && !host.textContent?.includes('原始解析块（770）'); i += 1) await settle()
  expect(readBlocks).toHaveBeenCalledWith('v1')
  expect(host.textContent).toContain('原始解析块（770）')
  expect(host.querySelectorAll('.document-block')).toHaveLength(25)
  expect(host.querySelector('.document-block p')?.textContent).toBe('Source paragraph 1')
  await act(async () => { Array.from(host.querySelectorAll('button')).find((b) => b.textContent === '下一页原文')!.click() })
  expect(host.textContent).toContain('26–50 / 共 770 段')
  expect(host.querySelector('.document-block p')?.textContent).toBe('Source paragraph 26')
  await render('v2'); await settle()
  expect(host.textContent).toContain('1–25 / 共 770 段')
  expect(readBlocks).toHaveBeenCalledWith('v2')
})

it('requires per-run consent for local material and retries the same submission key', async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const version = { version_id: 'docver_ready', document_id: 'doc_ready', status: 'ready', parse_receipt_ref: 'receipt', claim_count: 0, quality: { quality_status: 'ready_for_correction', issues: [], text_readability: 1, locator_completeness: 1, numeric_suspicion: 0 } } as unknown as ResearchDocumentVersion
  vi.spyOn(api, 'researchDocuments').mockResolvedValue([{ document_id: 'doc_ready', title: 'Calendar', latest_version_id: version.version_id, version_count: 1, market_scopes: ['crypto_macro'], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only' }] as Awaited<ReturnType<typeof api.researchDocuments>>)
  vi.spyOn(api, 'researchDocumentVersion').mockResolvedValue(version)
  vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
  vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
  const submit = vi.spyOn(api, 'submitResearchWithLibrary').mockRejectedValueOnce(new Error('connection lost')).mockResolvedValue({ schema_version: 'research-run-queued.v1', run_id: 'run_retry', event_id: 'event_retry', status: 'queued', status_url: '/v1/runs/run_retry' })
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchLibraryPanel routeVersionId={version.version_id} /></QueryClientProvider>)); await settle()
  const button = () => Array.from(host.querySelectorAll('button')).find((item) => item.textContent === '用这份资料发起 BTC 研究')!
  expect(button().disabled).toBe(true)
  const entry = host.querySelector('[aria-label="研究使用"]')!
  const source = host.querySelector('[aria-label="原始解析内容"]')!
  expect(entry.compareDocumentPosition(source) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(host.querySelector<HTMLDetailsElement>('.library-picker')?.open).toBe(false)
  expect(host.querySelector<HTMLDetailsElement>('.library-version-audit')?.open).toBe(false)
  expect(host.querySelector('.library-facts')?.textContent).not.toContain('docver_ready')
  expect(submit).not.toHaveBeenCalled()
  await act(async () => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click())
  expect(button().disabled).toBe(false)
  await act(async () => button().click()); await settle()
  expect(host.textContent).toContain('提交未确认')
  await act(async () => root.unmount()); client.clear()
  root = createRoot(host); client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchLibraryPanel routeVersionId={version.version_id} /></QueryClientProvider>)); await settle()
  expect(button().disabled).toBe(true)
  expect(host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(false)
  expect(submit).toHaveBeenCalledTimes(1)
  await act(async () => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click())
  await act(async () => button().click()); await settle()
  expect(submit.mock.calls).toHaveLength(2)
  expect(submit.mock.calls[0]).toEqual(submit.mock.calls[1])
  expect(submit.mock.calls[0][0].allow_remote_library_content).toBe(true)
  expect(window.location.hash).toContain('run=run_retry')
})


it('rejects an empty file and retains original-file controls after an unconfirmed network result', async () => {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(api, 'researchDocuments').mockResolvedValue([])
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(api, 'researchUploadPolicy').mockResolvedValue({ schema_version: 'document-upload-policy.v1', max_file_size_bytes: 20_971_520 })
  vi.spyOn(api, 'researchUploadReceipt').mockResolvedValue(null)
  const upload = vi.spyOn(api, 'uploadResearchDocumentReceipt').mockRejectedValue(new Error('connection lost'))
  const opened = vi.fn()
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchLibraryPanel onOpenVersion={opened} /></QueryClientProvider>)); await settle()
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!
  const select = async (file: File) => {
    Object.defineProperty(input, 'files', { configurable: true, value: [file] })
    await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
    await settle()
  }
  await select(new File([], 'empty.txt'))
  expect(upload).not.toHaveBeenCalled()
  expect(host.textContent).toContain('所选文件没有内容')
  expect(input.value).toBe('')
  await select(new File(['public acceptance text'], 'source.txt'))
  expect(upload).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('尚未确认上传结果')
  expect(host.querySelector<HTMLDetailsElement>('.error-callout details')?.open).toBe(false)
  expect([...host.querySelectorAll('button')].find(b => b.textContent === '重新选择原文件')!.disabled).toBe(false)
  expect(opened).not.toHaveBeenCalled()
})
