// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ResearchDocument, ResearchDocumentVersion } from '../api/types'
import { DesignStatePage } from '../DesignStatePage'
import { libraryResearchRequest } from './libraryRequest'
import { ResearchLibraryPanel } from './ResearchLibraryPanel'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: ReturnType<typeof createRoot> | undefined
let host: HTMLDivElement | undefined
let client: QueryClient | undefined
afterEach(async () => { await act(async () => root?.unmount()); client?.clear(); host?.remove(); vi.restoreAllMocks(); sessionStorage.clear() })
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }
function source(version: ResearchDocumentVersion): ResearchDocument {
  return { document_id: version.document_id, title: 'PC 本地质量验收', latest_version_id: version.version_id, market_scopes: ['crypto_macro'], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only' } as ResearchDocument
}
function version(status: ResearchDocumentVersion['status'], qualityStatus = 'blocked_low_quality'): ResearchDocumentVersion {
  return { version_id: 'docver_quality', document_id: 'doc_quality', status, original_file_hash: 'a'.repeat(64), correction_status: 'ready', parse_receipt_ref: 'receipt', claim_count: 0, quality: { quality_status: qualityStatus, issues: ['ocr_required', 'no_readable_text'], text_readability: 0, locator_completeness: 0, numeric_suspicion: 0 } } as unknown as ResearchDocumentVersion
}
function mockQueries(selected: ResearchDocumentVersion) {
  vi.spyOn(api, 'researchDocuments').mockResolvedValue([source(selected)])
  vi.spyOn(api, 'researchDocumentVersion').mockResolvedValue(selected)
  vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
  vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
}
async function render(selected: ResearchDocumentVersion, design = false) {
  mockQueries(selected)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const content = design ? <DesignStatePage scene="document-review" params={new URLSearchParams({ version: selected.version_id, back: '#/library' })} onNavigate={vi.fn()} /> : <ResearchLibraryPanel routeVersionId={selected.version_id} />
  await act(async () => root!.render(<QueryClientProvider client={client!}>{content}</QueryClientProvider>))
  await settle()
}
function labels() { return [...host!.querySelectorAll<HTMLButtonElement>('button')].map(button => button.textContent) }

it.each(['parsed', 'review', 'ready'] as const)('blocks correction and research for a %s version whose readable quality failed', async status => {
  const selected = version(status)
  await render(selected)
  expect(host!.textContent).toContain('这份资料的正文尚不可用')
  expect(host!.textContent).toContain('含文字的 PDF 或文本')
  expect(host!.textContent).toContain('本地 OCR')
  expect(labels()).not.toContain('请求资料整理建议')
  expect(labels()).not.toContain('确认整理并提取候选')
  expect(labels()).not.toContain('用这份资料发起 BTC 研究')
  expect(host!.textContent).not.toContain('可选择用于新研究')
  expect(host!.querySelector('a')?.getAttribute('href')).toContain('/original')
  expect(libraryResearchRequest(source(selected), selected, true).request).toBeNull()
})

it('restores a blocked quality document from a review deep link to the existing quality route', async () => {
  const selected = version('parsed')
  window.location.hash = '#/document-review?version=docver_quality&back=%23%2Flibrary'
  await render(selected, true)
  expect(window.location.hash).toBe('#/document-quality?version=docver_quality&back=%23%2Flibrary')
  expect(labels()).not.toContain('请求资料整理建议')
})

it('treats explicit failed quality as blocked without treating legacy unchecked quality as a failure', async () => {
  const selected = version('ready', 'failed')
  expect(libraryResearchRequest(source(selected), selected, true).request).toBeNull()
  const unchecked = version('ready', 'pending')
  unchecked.parse_receipt_ref = null
  expect(libraryResearchRequest(source(unchecked), unchecked, true).request).not.toBeNull()
  await render(unchecked)
  expect(labels()).toContain('用这份资料发起 BTC 研究')
  expect(host!.textContent).toContain('尚未进行解析质量检查')
  expect(host!.textContent).not.toContain('这份资料的正文尚不可用')
})
