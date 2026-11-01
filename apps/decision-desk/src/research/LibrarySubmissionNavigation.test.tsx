// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ResearchDocumentVersion } from '../api/types'
import { listPendingRequests } from './pendingRequests'
import { ResearchLibraryPanel } from './ResearchLibraryPanel'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined, host: HTMLDivElement | undefined, client: QueryClient | undefined
const queued = { schema_version: 'research-run-queued.v1' as const, event_id: 'event-library', run_id: 'run-library', status: 'queued' as const, status_url: '/v1/runs/run-library' }
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  host?.remove(); client?.clear(); root = undefined; host = undefined; client = undefined
  vi.restoreAllMocks(); sessionStorage.clear(); window.history.replaceState(null, '', '/')
})
async function pause() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) }) }
async function render(versionId = 'docver_v1') {
  if (!host) {
    host = document.createElement('div'); document.body.append(host); root = createRoot(host)
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: 2, retryDelay: 0 } } })
    const version = { version_id: 'docver_v1', document_id: 'd1', status: 'ready', parse_receipt_ref: 'receipt', claim_count: 0, quality: { quality_status: 'ready_for_correction', issues: [], text_readability: 1, locator_completeness: 1, numeric_suspicion: 0 } } as unknown as ResearchDocumentVersion
    vi.spyOn(api, 'researchDocuments').mockResolvedValue(['1', '2'].map(id => ({ document_id: `d${id}`, title: `资料${id}`, latest_version_id: `docver_v${id}`, version_count: 1, market_scopes: ['crypto_macro'], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only' })) as Awaited<ReturnType<typeof api.researchDocuments>>)
    vi.spyOn(api, 'researchDocumentVersion').mockImplementation(async id => ({ ...version, version_id: id, document_id: id === 'docver_v1' ? 'd1' : 'd2' }))
    vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
    vi.spyOn(api, 'researchClaims').mockResolvedValue([]); vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
  }
  await act(async () => root!.render(<QueryClientProvider client={client!}><ResearchLibraryPanel routeVersionId={versionId} /></QueryClientProvider>)); await pause()
}
const submit = () => [...host!.querySelectorAll('button')].find(button => button.textContent === '用这份资料发起 BTC 研究')!
const consent = () => host!.querySelector<HTMLInputElement>('.library-research-consent input')!

it('does not navigate from a late library research receipt after changing versions and returning, or inherit the prior consent', async () => {
  let release!: (value: typeof queued) => void
  const send = vi.spyOn(api, 'submitResearchWithLibrary').mockImplementation(() => new Promise(resolve => { release = resolve }))
  window.history.replaceState(null, '', '#/document-confirm?version=docver_v1')
  await render(); expect(send).not.toHaveBeenCalled()
  await act(async () => consent().click()); await act(async () => submit().click())
  const saved = listPendingRequests('library-research')[0]
  expect(send.mock.calls[0][0].selected_version_ids).toEqual(['docver_v1'])
  expect(send.mock.calls[0][0].allow_remote_library_content).toBe(true)
  await render('docver_v2'); expect(consent().checked).toBe(false)
  await render('docver_v1'); expect(consent().checked).toBe(false)
  await act(async () => release(queued)); await pause()
  expect(window.location.hash).toBe('#/document-confirm?version=docver_v1')
  expect(send).toHaveBeenCalledTimes(1); expect(host?.textContent).toContain('收到接收回执')
  const original = host!.querySelector<HTMLDetailsElement>('[aria-label="已收到的提交回执"] details')!
  expect(original.open).toBe(false); expect(original.textContent).toContain(saved.id)
  await act(async () => [...host!.querySelectorAll('button')].find(button => button.textContent === '打开已接收的研究任务')!.click())
  expect(window.location.hash).toContain(`run=${queued.run_id}`)
})

it('restores the full unconfirmed library intent without posting or restoring consent, then retries the exact identity explicitly', async () => {
  const send = vi.spyOn(api, 'submitResearchWithLibrary').mockRejectedValueOnce(new Error('withheld_after_commit_503')).mockResolvedValue(queued)
  await render(); await act(async () => consent().click()); await act(async () => submit().click()); await pause()
  const original = send.mock.calls[0]
  await act(async () => root!.unmount()); host?.remove(); client?.clear(); root = undefined; host = undefined; client = undefined
  await render()
  expect(send).toHaveBeenCalledTimes(1); expect(consent().checked).toBe(false); expect(submit().disabled).toBe(true)
  expect(document.body.textContent).toContain('不会自动创建研究或勾选 AI 授权')
  await act(async () => consent().click()); await act(async () => submit().click()); await pause()
  expect(send.mock.calls[1]).toEqual(original)
})
