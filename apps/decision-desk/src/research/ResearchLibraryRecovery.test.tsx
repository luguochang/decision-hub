// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { AnalystClaim, ResearchDocumentVersion } from '../api/types'
import { libraryCommands } from './libraryApi'
import { ResearchLibraryPanel } from './ResearchLibraryPanel'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: ReturnType<typeof createRoot>
let host: HTMLDivElement
let client: QueryClient
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); sessionStorage.clear() })
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }
function version(status: ResearchDocumentVersion['status']): ResearchDocumentVersion {
  return { version_id: 'docver_original', document_id: 'doc_example', status, original_file_hash: 'a'.repeat(64), correction_status: status === 'failed' ? 'failed' : 'accepted', parse_receipt_ref: 'receipt', claim_count: 1, quality: { quality_status: 'ready_for_correction', issues: [], text_readability: 1, locator_completeness: 1, numeric_suspicion: 0 } } as unknown as ResearchDocumentVersion
}
function setupQueries(selected: ResearchDocumentVersion) {
  vi.spyOn(api, 'researchDocuments').mockResolvedValue([{ document_id: selected.document_id, title: 'Local source', latest_version_id: selected.version_id, version_count: 1, market_scopes: ['crypto_macro'], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only' }] as Awaited<ReturnType<typeof api.researchDocuments>>)
  vi.spyOn(api, 'researchDocumentVersion').mockResolvedValue(selected)
  vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
  vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
}
async function render(versionId: string, onVersionState = vi.fn()) {
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchLibraryPanel routeVersionId={versionId} onVersionState={onVersionState} /></QueryClientProvider>))
  await settle()
}
const button = (text: string) => [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === text)!

it('restores an unconfirmed document recovery after remount and confirms the same command without automatic replay', async () => {
  const failed = version('failed')
  const recovered = { ...failed, version_id: 'docver_recovered', status: 'received' as const, supersedes_version_id: failed.version_id }
  setupQueries(failed)
  vi.mocked(api.researchDocumentVersion).mockImplementation(async id => id === recovered.version_id ? recovered : failed)
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
  const onState = vi.fn()
  const recover = vi.spyOn(libraryCommands, 'recover').mockRejectedValueOnce(new Error('response interrupted')).mockResolvedValue(recovered)
  await render(failed.version_id, onState)
  expect(host.textContent).toContain('资料整理未完成')
  expect(host.textContent).toContain('原版本与归档原件已保留')
  await act(async () => button('重新处理这份资料').click()); await settle()
  expect(host.textContent).toContain('重新处理尚未确认')
  expect(onState).not.toHaveBeenCalled()
  await act(async () => root.unmount()); client.clear(); host.remove()
  await render(failed.version_id, onState)
  expect(recover).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('上次重新处理尚未确认')
  await act(async () => button('重新处理这份资料').click()); await settle()
  expect(recover.mock.calls).toHaveLength(2)
  expect(recover.mock.calls[0]).toEqual(recover.mock.calls[1])
  expect(recover.mock.calls[0][2]).toMatch(/^document-recovery:/)
  expect(onState).toHaveBeenCalledWith(recovered)
  expect(button('重新处理这份资料')).toBeUndefined()
  expect(button('查看上次处理记录')).not.toBeUndefined()
})

it.each([['accept', '接受', 'accepted', '已接受'], ['reject', '拒绝', 'rejected', '已拒绝']] as const)('keeps the %s command identity after an interrupted response and blocks an opposite decision', async (decision, label, finalStatus, statusLabel) => {
  const selected = version('ready')
  setupQueries(selected)
  let claim = { claim_id: 'claim_test', version_id: selected.version_id, stance: 'bullish', status: 'candidate', market_scope: 'crypto_macro', claim_text: 'BTC看多', supporting_quote: 'BTC看多原句', requires_independent_evidence: true } as unknown as AnalystClaim
  vi.spyOn(api, 'researchClaims').mockImplementation(async () => [claim])
  const oldReview = vi.spyOn(api, 'reviewResearchClaim')
  const review = vi.spyOn(libraryCommands, 'reviewClaim').mockImplementation(async () => { claim = { ...claim, status: finalStatus }; if (review.mock.calls.length === 1) throw new Error('response interrupted'); return claim })
  await render(selected.version_id)
  await act(async () => button(label).click()); await settle()
  expect(host.textContent).toContain('观点审核结果尚未确认')
  expect(button('接受')).toBeUndefined()
  expect(button('拒绝')).toBeUndefined()
  await act(async () => root.unmount()); client.clear(); host.remove()
  await render(selected.version_id)
  expect(review).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain(statusLabel)
  expect(button(`核对上次${label}结果`)).not.toBeUndefined()
  await act(async () => button(`核对上次${label}结果`).click()); await settle()
  expect(review.mock.calls).toHaveLength(2)
  expect(review.mock.calls[0]).toEqual(review.mock.calls[1])
  expect(review.mock.calls[0][3]).toMatch(/^claim-review:/)
  expect(oldReview).not.toHaveBeenCalled()
  expect(button(`核对上次${label}结果`)).toBeUndefined()
  expect(host.textContent).toContain('接受候选也不代表其事实已经核实')
  expect(claim.requires_independent_evidence).toBe(true)
})

it('offers a scoped read retry instead of turning a claims read failure into zero candidates', async () => {
  const selected = version('ready')
  setupQueries(selected)
  const claims = vi.spyOn(api, 'researchClaims').mockRejectedValueOnce(new Error('read interrupted')).mockResolvedValue([])
  await render(selected.version_id)
  expect(host.textContent).toContain('观点候选暂时不可用')
  expect(host.textContent).not.toContain('本次未识别出观点候选')
  const alert = [...host.querySelectorAll('[role="alert"]')].find(item => item.textContent?.includes('观点候选暂时不可用'))!
  await act(async () => alert.querySelector<HTMLButtonElement>('button')!.click()); await settle()
  expect(claims.mock.calls).toEqual([[selected.version_id], [selected.version_id]])
  expect(host.textContent).toContain('本次未识别出观点候选')
})

it('keeps the old failure reachable and opens its already-created processing version without creating another one', async () => {
  const failed = { ...version('failed'), correction_status: 'not_requested' as const, parse_receipt_ref: null }
  const current = { ...version('parsed'), version_id: 'docver_current', supersedes_version_id: failed.version_id }
  setupQueries(failed)
  vi.mocked(api.researchDocumentVersion).mockImplementation(async id => id === current.version_id ? current : failed)
  vi.mocked(api.researchDocuments).mockResolvedValue([{ document_id: failed.document_id, title: 'PC isolated source', latest_version_id: current.version_id, version_count: 2, market_scopes: ['crypto_macro'], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only' }] as Awaited<ReturnType<typeof api.researchDocuments>>)
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
  const recover = vi.spyOn(libraryCommands, 'recover')
  await render(failed.version_id)
  expect(host.textContent).toContain('原文解析未完成')
  expect(button('重新处理这份资料')).toBeUndefined()
  expect(host.querySelector('.research-library-stepper .current')?.textContent).toContain('解析')
  await act(async () => button('查看最新处理版本').click()); await settle()
  expect(host.textContent).not.toContain('原文解析未完成')
  expect(button('查看上次处理记录')).not.toBeUndefined()
  await act(async () => button('查看上次处理记录').click()); await settle()
  expect(host.textContent).toContain('原文解析未完成')
  expect(recover).not.toHaveBeenCalled()
})
