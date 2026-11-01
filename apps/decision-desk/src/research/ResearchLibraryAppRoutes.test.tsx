// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../api/client'
import type { ResearchDocumentVersion } from '../api/types'
import { libraryCommands } from './libraryApi'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: ReturnType<typeof createRoot> | undefined
let host: HTMLDivElement
let client: QueryClient
afterEach(async () => { if (root) await act(async () => root!.unmount()); client?.clear(); host?.remove(); vi.restoreAllMocks(); sessionStorage.clear(); window.history.replaceState(null, '', '#/home'); root = undefined })
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }
function version(id: string, status: ResearchDocumentVersion['status']): ResearchDocumentVersion {
  return { version_id: id, document_id: `doc_${id}`, status, original_file_hash: 'a'.repeat(64), correction_status: status === 'failed' ? 'not_requested' : 'ready', parse_receipt_ref: status === 'failed' || status === 'received' ? null : `receipt://${id}`, claim_count: 0, quality: { quality_status: 'needs_review', issues: [], text_readability: 0, locator_completeness: 0, numeric_suspicion: 0 } } as unknown as ResearchDocumentVersion
}
function setup(versions: Map<string, ResearchDocumentVersion>) {
  vi.spyOn(api, 'operations').mockResolvedValue({ services: [] } as unknown as Awaited<ReturnType<typeof api.operations>>)
  vi.spyOn(api, 'researchDocuments').mockImplementation(async () => [...versions.values()].filter(row => !row.supersedes_version_id).map(row => ({ document_id: row.document_id, title: `PC验收 ${row.version_id}`, latest_version_id: [...versions.values()].find(next => next.supersedes_version_id === row.version_id)?.version_id ?? row.version_id, version_count: 1, market_scopes: ['crypto_macro'], instrument_scopes: ['BTC-USDT'], privacy_mode: 'local_only' })) as Awaited<ReturnType<typeof api.researchDocuments>>)
  vi.spyOn(api, 'researchDocumentVersion').mockImplementation(async id => versions.get(id)!)
  vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
  vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
}
async function render(hash: string) {
  window.history.replaceState(null, '', hash)
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root!.render(<QueryClientProvider client={client}><App /></QueryClientProvider>))
  for (let count = 0; count < 30 && !host.querySelector('.library-detail'); count += 1) await settle()
  await settle()
}
const button = (label: string) => [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === label)!
async function select(id: string) {
  await act(async () => {
    host.querySelector<HTMLDetailsElement>('.library-picker')!.open = true
    ;[...host.querySelectorAll<HTMLButtonElement>('.library-row')].find(item => item.textContent?.includes(id))!.click()
  })
  await settle()
}

it.each(['version', 'version_id'])('opens a real App library deep link using %s without silently dropping the selected document', async parameter => {
  const failed = version('docver_failed', 'failed')
  const read = new Map([[failed.version_id, failed]])
  setup(read)
  await render(`#/library?${parameter}=${failed.version_id}`)
  expect(api.researchDocumentVersion).toHaveBeenCalledWith(failed.version_id)
  expect(host.querySelector('.library-detail')?.textContent).toContain('原文解析未完成')
  expect(host.querySelector('.quality-card')?.textContent).toContain('尚未进行解析质量检查')
  expect(host.querySelector('.quality-card')?.textContent).not.toContain('0%')
  expect(host.querySelector('.library-facts')?.textContent).toContain('尚未检查')
})

it('routes failed → recovered → parsed through the actual App and retains the prior failure return path', async () => {
  const failed = version('docver_failed', 'failed')
  const current = { ...version('docver_recovered', 'parsed'), document_id: failed.document_id, supersedes_version_id: failed.version_id }
  const rows = new Map([[failed.version_id, failed]])
  setup(rows)
  vi.spyOn(libraryCommands, 'recover').mockImplementation(async () => { rows.set(current.version_id, current); return { ...current, status: 'received' } })
  await render(`#/document-failed?version=${failed.version_id}`)
  await act(async () => button('重新处理这份资料').click())
  for (let count = 0; count < 20 && !window.location.hash.includes('document-review'); count += 1) await settle()
  expect(window.location.hash).toContain(`#/document-review?version=${current.version_id}`)
  expect(host.querySelector('.library-detail')?.textContent).not.toContain('原文解析未完成')
  await act(async () => button('查看上次处理记录').click()); await settle()
  expect(window.location.hash).toContain(`#/document-failed?version=${failed.version_id}`)
  expect(button('查看最新处理版本')).not.toBeUndefined()
  await act(async () => button('查看最新处理版本').click()); await settle()
  expect(window.location.hash).toContain(`#/document-review?version=${current.version_id}`)
  expect(libraryCommands.recover).toHaveBeenCalledTimes(1)
})

it.each(['request', 'accept'] as const)('keeps a pending %s correction receipt scoped to its version while the actual App switches documents', async action => {
  const first = version('docver_first', action === 'request' ? 'parsed' : 'review')
  const second = version('docver_second', first.status)
  const rows = new Map([[first.version_id, first], [second.version_id, second]])
  setup(rows)
  let confirm: (result: ResearchDocumentVersion) => void = () => undefined
  const command = vi.spyOn(api, action === 'request' ? 'requestResearchCorrection' : 'acceptResearchCorrection').mockReturnValue(new Promise(resolve => { confirm = resolve }))
  await render(`#/document-${action === 'request' ? 'review' : 'confirm'}?version=${first.version_id}`)
  await act(async () => button(action === 'request' ? '请求资料整理建议' : '确认整理并提取候选').click())
  await select(second.version_id)
  for (let count = 0; count < 5; count += 1) await settle()
  expect(new URLSearchParams(window.location.hash.split('?')[1]).get('version')).toBe(second.version_id)
  await act(async () => { const result = { ...first, status: action === 'request' ? 'correcting' as const : 'ready' as const }; rows.set(first.version_id, result); confirm(result) })
  for (let count = 0; count < 5; count += 1) await settle()
  expect(command).toHaveBeenCalledWith(first.version_id)
  expect(new URLSearchParams(window.location.hash.split('?')[1]).get('version')).toBe(second.version_id)
  expect(host.querySelector('.library-version-audit')?.textContent).toContain(second.version_id)
})

it('shows a known correction reason in Chinese and preserves its raw code in a closed audit', async () => {
  const selected = version('docver_review', 'review')
  setup(new Map([[selected.version_id, selected]]))
  vi.mocked(api.researchDocumentCorrection).mockResolvedValue({ schema_version: 'document-correction.v1', correction_id: 'corr_review', version_id: selected.version_id, correction_model_ref: 'financial-correction.deterministic.v1', prompt_ref: 'rules.v1', corrected_content_ref: null, quality_status: 'ready_for_correction', created_at: '2026-10-01T00:00:00Z', accepted_at: null, unresolved_fragments: [], changes: [{ schema_version: 'document-correction-change.v1', change_id: 'change_format', page_no: 1, block_id: 'block_review', original: 'BTC  看多', corrected: 'BTC 看多', reason: 'unicode_and_whitespace_normalization', confidence: 'high', requires_user_confirmation: false, accepted: false }] })
  await render(`#/document-confirm?version=${selected.version_id}`)
  expect(host.querySelector('.diff-list small')?.textContent).toContain('格式与空白规范化')
  expect(host.querySelector('.diff-list small')?.textContent).not.toContain('unicode_and_whitespace_normalization')
  const audit = host.querySelector<HTMLDetailsElement>('.diff-list details')!
  expect(audit.open).toBe(false)
  expect(audit.textContent).toContain('unicode_and_whitespace_normalization')
})
