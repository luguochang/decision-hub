// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { researchDocumentVersionSchema } from '../api/schemas'
import type { ResearchDocumentVersion } from '../api/types'
import { ResearchLibraryPanel } from './ResearchLibraryPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })
const stamp = '2026-10-01T06:00:00Z'
function version(status: ResearchDocumentVersion['status'], parsed = false) {
  return researchDocumentVersionSchema.parse({
    schema_version: 'research-document-version.v1', version_id: 'docver_stage', document_id: 'doc_stage',
    original_file_ref: 'docbundle://source/original.pdf', original_file_hash: 'a'.repeat(64), status,
    correction_status: 'not_requested', published_at: null, observed_at: stamp, received_at: stamp, created_at: stamp,
    supersedes_version_id: null, parse_receipt_ref: parsed ? 'docbundle://parse/receipt.json' : null,
    corrected_content_ref: null, block_count: 0, claim_count: 0,
    quality: { layout_coverage: 0, text_readability: 0, table_integrity: 0, image_coverage: 0, numeric_suspicion: 0,
      encoding_errors: 0, locator_completeness: 0, quality_status: 'blocked_low_quality',
      issues: parsed ? ['ocr_required', 'no_readable_text'] : status === 'failed' ? ['not_parsed', 'document_processing_failed:parse:PdfStreamError'] : ['not_parsed'] },
  })
}
async function inspect(selected: ResearchDocumentVersion, assertions: (host: HTMLDivElement) => Promise<void> | void) {
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(api, 'researchDocuments').mockResolvedValue([])
  vi.spyOn(api, 'researchDocumentVersion').mockResolvedValue(selected)
  vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
  vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><ResearchLibraryPanel routeVersionId={selected.version_id} /></QueryClientProvider>))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) })
    await assertions(host)
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
}

it.each([['received', '已接收'], ['parsing', '读取原文中']] as const)('does not call unparsed %s a failed quality check or an OCR result', async (status, label) => {
  await inspect(version(status), host => {
    expect(host.querySelector('.library-detail .status-pill')?.textContent).toBe(label)
    expect(host.textContent).toContain('解析质量尚未检查')
    expect(host.textContent).toContain('暂不能整理或加入研究')
    expect(host.textContent).not.toContain('质量未通过')
    expect(host.textContent).not.toContain('扫描 PDF 和图片需要')
    expect(host.textContent).not.toContain('文本 0%')
    expect(host.textContent).not.toContain('请求资料整理建议')
    expect(api.researchDocumentBlocks).not.toHaveBeenCalled()
  })
})

it('keeps a parse failure and archived original explicit without presenting an OCR diagnosis', async () => {
  await inspect(version('failed'), host => {
    expect(host.textContent).toContain('原文解析未完成')
    expect(host.textContent).toContain('原版本与归档原件已保留')
    expect(host.textContent).toContain('尚未进行解析质量检查')
    expect(host.textContent).not.toContain('扫描 PDF 和图片需要')
    expect(host.textContent).not.toContain('文本 0%')
    const audit = [...host.querySelectorAll('details')].find(item => item.textContent?.includes('document_processing_failed:parse:PdfStreamError'))!
    expect(audit.open).toBe(false)
    expect(host.querySelector('a[href*="/original"]')).not.toBeNull()
  })
})

it('continues to block a genuinely parsed scanned document with its actual OCR quality receipt', async () => {
  await inspect(version('parsed', true), host => {
    expect(host.textContent).toContain('质量未通过')
    expect(host.textContent).toContain('扫描 PDF 和图片需要')
    expect(host.textContent).toContain('文本 0%')
    expect(host.textContent).not.toContain('请求资料整理建议')
    expect(api.researchDocumentBlocks).toHaveBeenCalledWith('docver_stage')
  })
})

it('labels initial not_parsed as processing records and only labels an actual failed version as failure records', async () => {
  await inspect(version('received'), host => {
    const record = host.querySelector<HTMLDetailsElement>('.quality-card details')!
    expect(record.querySelector('summary')?.textContent).toBe('查看处理记录')
    expect(record.open).toBe(false)
    expect(record.textContent).toContain('not_parsed')
    expect(host.textContent).not.toContain('查看处理失败记录')
  })
  await inspect(version('failed'), host => {
    expect(host.querySelector('.quality-card details summary')?.textContent).toBe('查看处理失败记录')
  })
})
