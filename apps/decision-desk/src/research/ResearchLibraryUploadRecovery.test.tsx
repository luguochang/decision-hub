// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { DocumentUploadReceipt } from '../api/types'
import { listPendingRequests, pendingRequest } from './pendingRequests'
import { ResearchLibraryPanel } from './ResearchLibraryPanel'
import { createUploadIntent, uploadPolicy } from './uploadRequests'
import { uploadMetadata, uploadReceipt, uploadVersion } from './uploadTestFixtures'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root, host: HTMLDivElement, client: QueryClient
const originalFile = () => new File(['PC original content'], 'source.pdf')
beforeEach(() => {
  sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); window.location.hash = '#/library'
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  vi.spyOn(api, 'researchDocuments').mockResolvedValue([])
  vi.spyOn(api, 'researchUploadPolicy').mockResolvedValue(uploadPolicy)
  vi.spyOn(api, 'researchUploadReceipt').mockResolvedValue(null)
  vi.spyOn(api, 'researchDocumentVersion').mockImplementation(async id => ({ ...uploadVersion, version_id: id, original_file_hash: (listPendingRequests('document-upload')[0]?.payload as { file_sha256: string } | undefined)?.file_sha256 ?? 'a'.repeat(64) }))
  vi.spyOn(api, 'researchDocumentCorrection').mockResolvedValue(null)
  vi.spyOn(api, 'researchDocumentBlocks').mockResolvedValue([])
  vi.spyOn(api, 'researchClaims').mockResolvedValue([])
})
afterEach(async () => { await act(async () => root.unmount()); client.clear(); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)) }) }
async function mount(props: Parameters<typeof ResearchLibraryPanel>[0] = {}) {
  await act(async () => root.render(<QueryClientProvider client={client}><ResearchLibraryPanel {...props} /></QueryClientProvider>)); await settle()
}
const button = (label: string) => [...host.querySelectorAll('button')].find(item => item.textContent === label)!
async function select(file: File) {
  const input = host.querySelector<HTMLInputElement>('input[type="file"]')!
  Object.defineProperty(input, 'files', { configurable: true, value: [file] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true }))); await settle(); await settle()
}
async function storedUpload() {
  const intent = await createUploadIntent(originalFile(), uploadMetadata)
  return { intent, record: pendingRequest('document-upload', intent) }
}

it('rejects empty and oversize before policy requests, hashing, journal or POST', async () => {
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt')
  await mount(); await select(new File([], 'empty.pdf'))
  expect(host.textContent).toContain('所选文件没有内容')
  expect(host.querySelector('[role="alert"] strong')?.textContent).toBe('未发送上传')
  expect(host.textContent).toContain('尚未发送新请求')
  const large = new File(['x'], 'large.txt'); Object.defineProperty(large, 'size', { value: 20_971_521 })
  await select(large)
  expect(host.textContent).toContain('20,971,520 字节')
  expect(host.querySelector('[role="alert"] strong')?.textContent).toBe('未发送上传')
  expect(api.researchUploadPolicy).not.toHaveBeenCalled()
  expect(post).not.toHaveBeenCalled()
  expect(listPendingRequests('document-upload')).toEqual([])
  expect(button('上传资料').disabled).toBe(false)
})

it('persists full intent before POST, keeps a lost response fixed and restores GET-only after leaving', async () => {
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt').mockImplementation(async (_file, intent, key) => {
    expect(listPendingRequests('document-upload')).toMatchObject([{ id: key, payload: intent }])
    throw new Error('pc_probe_response_withheld_after_commit')
  })
  const opened = vi.fn()
  await mount({ onOpenVersion: opened }); await select(originalFile())
  expect(post).toHaveBeenCalledTimes(1)
  const [_file, intent, key] = post.mock.calls[0]
  expect(intent.metadata.title).toBe('source.pdf')
  expect(host.querySelector<HTMLInputElement>('[aria-label="资料标题"]')!.disabled).toBe(true)
  expect(host.querySelector<HTMLSelectElement>('[aria-label="资料适用领域"]')!.disabled).toBe(true)
  expect(host.querySelector('[role="alert"] > div > p')?.textContent).not.toContain('pc_probe')
  const audit = host.querySelector<HTMLDetailsElement>('[role="alert"] details')!
  expect(audit.open).toBe(false); expect(audit.textContent).toContain('pc_probe_response_withheld_after_commit')
  await act(async () => root.render(<div>已离开</div>)); await mount({ onOpenVersion: opened })
  expect(post).toHaveBeenCalledTimes(1)
  expect(api.researchUploadReceipt).toHaveBeenCalledWith(key)
  expect(host.textContent).toContain('返回此页只查询回执，不会自动上传')
  expect(host.querySelector<HTMLInputElement>('[aria-label="资料标题"]')!.value).toBe('source.pdf')
  expect(opened).not.toHaveBeenCalled()
  expect(button('按原内容重试上传').disabled).toBe(true)
  expect(listPendingRequests('document-upload')[0].id).toBe(key)
})

it('recovers the exact committed receipt using current version, clears the journal and only opens explicitly', async () => {
  const saved = await storedUpload(), received = uploadReceipt(saved.intent, saved.record.id)
  vi.mocked(api.researchUploadReceipt).mockResolvedValue(received)
  vi.mocked(api.researchDocumentVersion).mockResolvedValue({ ...received.version, status: 'parsed', parse_receipt_ref: 'document://parse' })
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt'), opened = vi.fn()
  await mount({ onVersionState: opened }); await settle()
  expect(post).not.toHaveBeenCalled()
  expect(host.textContent).toContain('核对时状态：原文已解析')
  expect(host.textContent).not.toContain('核对时状态：已接收')
  expect(listPendingRequests('document-upload')).toEqual([])
  expect(opened).not.toHaveBeenCalled()
  await act(async () => button('打开已保存资料').click())
  await settle()
  expect(opened).toHaveBeenCalledWith(expect.objectContaining({ status: 'parsed', version_id: received.version.version_id }))
})

it('requires exact reselected bytes and explicitly GETs before retrying the same key and locked metadata', async () => {
  const saved = await storedUpload(), post = vi.spyOn(api, 'uploadResearchDocumentReceipt').mockImplementation(async (_file, intent, key) => uploadReceipt(intent, key))
  await mount()
  await select(new File(['PC original contenX'], 'source.pdf'))
  expect(host.textContent).toContain('所选文件与原上传不一致')
  expect(post).not.toHaveBeenCalled(); expect(button('按原内容重试上传').disabled).toBe(true)
  await select(originalFile())
  expect(post).not.toHaveBeenCalled()
  expect(button('按原内容重试上传').disabled).toBe(false)
  host.querySelector<HTMLInputElement>('[aria-label="资料标题"]')!.value = '篡改标题'
  const getCount = vi.mocked(api.researchUploadReceipt).mock.calls.length
  await act(async () => { button('按原内容重试上传').click(); button('按原内容重试上传').click() }); await settle()
  expect(api.researchUploadReceipt).toHaveBeenCalledTimes(getCount + 1)
  expect(post).toHaveBeenCalledTimes(1)
  expect(post).toHaveBeenCalledWith(expect.any(File), saved.intent, saved.record.id)
  expect(listPendingRequests('document-upload')).toEqual([])
})

it.each(['found', 'unavailable'] as const)('does not POST on explicit retry when receipt GET is %s', async outcome => {
  const saved = await storedUpload(), post = vi.spyOn(api, 'uploadResearchDocumentReceipt')
  await mount(); await select(originalFile())
  if (outcome === 'found') vi.mocked(api.researchUploadReceipt).mockResolvedValue(uploadReceipt(saved.intent, saved.record.id))
  else vi.mocked(api.researchUploadReceipt).mockRejectedValue(new Error('API 503'))
  await act(async () => button('按原内容重试上传').click()); await settle()
  expect(post).not.toHaveBeenCalled()
  if (outcome === 'found') expect(listPendingRequests('document-upload')).toEqual([])
  else { expect(listPendingRequests('document-upload')[0].id).toBe(saved.record.id); expect(host.textContent).toContain('尚未确认上传结果') }
})

it('refuses a mismatched receipt and refuses sending when browser storage cannot persist intent', async () => {
  const saved = await storedUpload()
  vi.mocked(api.researchUploadReceipt).mockResolvedValue({ ...uploadReceipt(saved.intent, saved.record.id), request_id: 'other' })
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt')
  await mount(); await settle()
  expect(host.textContent).toContain('上传回执与原请求不一致')
  expect(listPendingRequests('document-upload')[0].id).toBe(saved.record.id)
  expect(post).not.toHaveBeenCalled()
  await act(async () => root.render(<div>离开</div>)); sessionStorage.clear(); vi.mocked(api.researchUploadReceipt).mockResolvedValue(null)
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('QuotaExceededError') })
  await mount(); await select(originalFile())
  expect(host.textContent).toContain('无法保存本次提交凭据')
  expect(post).not.toHaveBeenCalled()
})

it.each(['other-version', 'other-route', 'unmounted'] as const)('does not steal navigation after a late success at %s', async change => {
  let finish!: (receipt: DocumentUploadReceipt) => void
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const opened = vi.fn()
  await mount({ routeVersionId: 'docver_original', onVersionState: opened }); await select(originalFile())
  expect(post).toHaveBeenCalledTimes(1)
  const [, intent, key] = post.mock.calls[0]
  if (change === 'other-version') await mount({ routeVersionId: 'docver_other', onVersionState: opened })
  if (change === 'other-route') window.location.hash = '#/home'
  if (change === 'unmounted') await act(async () => root.render(<div>已离开</div>))
  await act(async () => finish(uploadReceipt(intent, key))); await settle()
  expect(opened).not.toHaveBeenCalled()
  expect(listPendingRequests('document-upload')).toEqual([])
  if (change !== 'unmounted') {
    expect(host.textContent).toContain('原件已保存')
    expect(button('打开已保存资料')).toBeDefined()
  }
})

it('explains exact intent reuse without claiming parsing or AI consent has succeeded', async () => {
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt').mockImplementation(async (_file, intent, key) => uploadReceipt(intent, key, true))
  const opened = vi.fn()
  await mount({ onVersionState: opened }); await select(originalFile())
  expect(post).toHaveBeenCalledTimes(1)
  expect(host.textContent).toContain('已复用相同资料')
  expect(host.textContent).toContain('核对时状态：已接收')
  expect(host.textContent).toContain('仍需完成资料处理与审核')
  expect(host.textContent).not.toContain('用这份资料发起 BTC 研究')
  expect(opened).toHaveBeenCalledWith(expect.objectContaining({ status: 'received' }))
})

it('keeps GET-confirmed success when the original POST later fails and clears it before a new attempt', async () => {
  let fail!: (error: Error) => void
  let committed: DocumentUploadReceipt | null = null
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt').mockImplementation((_file, intent, key) => {
    committed = uploadReceipt(intent, key)
    return new Promise((_resolve, reject) => { fail = reject })
  })
  vi.mocked(api.researchUploadReceipt).mockImplementation(async () => committed)
  await mount(); await select(originalFile()); await settle()
  expect(host.textContent).toContain('原件已保存')
  expect(listPendingRequests('document-upload')).toEqual([])
  expect(button('上传中…').disabled).toBe(true)
  await act(async () => fail(new Error('pc_probe_response_withheld_after_commit'))); await settle()
  expect(host.textContent).toContain('原件已保存')
  expect(host.textContent).not.toContain('上传尚未确认')
  expect(host.textContent).not.toContain('pc_probe_response_withheld_after_commit')
  expect(button('上传资料').disabled).toBe(false)
  await select(new File([], 'empty.pdf'))
  expect(post).toHaveBeenCalledTimes(1)
  expect(host.querySelector('[aria-label="已确认上传"]')).toBeNull()
  expect(host.textContent).toContain('所选文件没有内容')
})

it('keeps a confirmed request separate from the next upload whose response is unconfirmed', async () => {
  const post = vi.spyOn(api, 'uploadResearchDocumentReceipt').mockImplementationOnce(async (_file, intent, key) => uploadReceipt(intent, key)).mockRejectedValueOnce(new Error('next_response_lost'))
  await mount(); await select(originalFile())
  expect(host.textContent).toContain('原件已保存')
  await select(new File(['another original'], 'next.pdf'))
  expect(post).toHaveBeenCalledTimes(2)
  expect(post.mock.calls[1][2]).not.toBe(post.mock.calls[0][2])
  expect(host.querySelector('[aria-label="已确认上传"]')).toBeNull()
  expect(host.textContent).toContain('尚未确认上传结果')
  expect(listPendingRequests('document-upload')).toMatchObject([{ id: post.mock.calls[1][2], payload: { file_name: 'next.pdf' } }])
})

it('describes a ready confirmation as ready and reads fresh state before explicitly opening it', async () => {
  const saved = await storedUpload(), receipt = uploadReceipt(saved.intent, saved.record.id)
  vi.mocked(api.researchUploadReceipt).mockResolvedValue(receipt)
  const ready = { ...receipt.version, status: 'ready' as const, parse_receipt_ref: 'document://parse', quality: { ...receipt.version.quality, quality_status: 'ready_for_correction' as const, issues: [] } }
  vi.mocked(api.researchDocumentVersion).mockResolvedValue(ready)
  const opened = vi.fn()
  await mount({ onVersionState: opened })
  const banner = host.querySelector('[aria-label="已确认上传"]')!
  expect(banner.textContent).toContain('核对时状态：可用于研究')
  expect(banner.textContent).toContain('资料已完成处理')
  expect(banner.textContent).not.toContain('仍需完成资料处理与审核')
  expect(opened).not.toHaveBeenCalled()
  const getCount = vi.mocked(api.researchDocumentVersion).mock.calls.length
  vi.mocked(api.researchDocumentVersion).mockResolvedValue({ ...ready, status: 'archived' })
  await act(async () => button('打开已保存资料').click()); await settle()
  expect(vi.mocked(api.researchDocumentVersion).mock.calls.length).toBeGreaterThan(getCount)
  expect(vi.mocked(api.researchDocumentVersion).mock.calls.slice(getCount).every(([id]) => id === receipt.version.version_id)).toBe(true)
  expect(opened).toHaveBeenCalledWith(expect.objectContaining({ version_id: receipt.version.version_id, status: 'archived' }))
  expect(host.querySelector('[aria-label="已确认上传"]')!.textContent).toContain('核对时状态：已归档')
})

it.each(['other-version', 'other-route', 'unmounted'] as const)('does not steal navigation when an explicit fresh-open response is late at %s', async change => {
  const saved = await storedUpload(), receipt = uploadReceipt(saved.intent, saved.record.id)
  vi.mocked(api.researchUploadReceipt).mockResolvedValue(receipt)
  vi.mocked(api.researchDocumentVersion).mockResolvedValue(receipt.version)
  const opened = vi.fn()
  await mount({ onVersionState: opened })
  let finish!: (value: typeof receipt.version) => void
  vi.mocked(api.researchDocumentVersion).mockImplementation(id => id === receipt.version.version_id ? new Promise(resolve => { finish = resolve }) : Promise.resolve({ ...uploadVersion, version_id: id }))
  await act(async () => button('打开已保存资料').click())
  await settle()
  expect(button('正在读取最新状态…').disabled).toBe(true)
  if (change === 'other-version') await mount({ routeVersionId: 'docver_other', onVersionState: opened })
  if (change === 'other-route') window.location.hash = '#/home'
  if (change === 'unmounted') await act(async () => root.render(<div>已离开</div>))
  await act(async () => finish({ ...receipt.version, status: 'parsed', parse_receipt_ref: 'document://parsed' })); await settle()
  expect(opened).not.toHaveBeenCalled()
})
