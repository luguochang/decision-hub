// @vitest-environment jsdom
// Existing synthetic presentation wire; no SQL, Host or real Provider claim.
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import fixture from './__fixtures__/saved-probability-definitions-available.json'
import { settingsApi } from '../api/decisionSettings'
import { PerpetualSavedReportBoundary } from './PerpetualSavedReport'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
const originalCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
const originalRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')
afterEach(() => {
  if (vi.isFakeTimers()) { vi.runOnlyPendingTimers(); vi.useRealTimers() }
  vi.restoreAllMocks()
  if (originalCreate) Object.defineProperty(URL, 'createObjectURL', originalCreate)
  else Reflect.deleteProperty(URL, 'createObjectURL')
  if (originalRevoke) Object.defineProperty(URL, 'revokeObjectURL', originalRevoke)
  else Reflect.deleteProperty(URL, 'revokeObjectURL')
})

function downloads() {
  const blobs: Blob[] = []
  Object.defineProperty(URL, 'createObjectURL', { configurable: true,
    value: vi.fn((blob: Blob) => { blobs.push(blob); return `blob:reading-${blobs.length}` }) })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  return blobs
}
function blobText(blob: Blob) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('Not text'))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}
async function page() {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(fixture)))
  const host = document.createElement('div'), root = createRoot(host)
  await act(async () => root.render(<PerpetualSavedReportBoundary runId={fixture.run_id}><p>旧分支</p></PerpetualSavedReportBoundary>))
  for (let attempt = 0; attempt < 50 && !host.querySelector('[aria-label="已核对报告审核状态"]'); attempt += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  }
  expect(host.querySelector('[aria-label="已核对报告审核状态"]')).not.toBeNull()
  return { host, root, close: async () => { await act(async () => root.unmount()) } }
}
function button(host: HTMLElement, label: string) {
  const result = Array.from(host.querySelectorAll('button')).find(item => item.textContent === label)
  if (!result) throw new Error(`Missing reading export action: ${label}`)
  return result
}

it('downloads the full authenticated reading copy and keeps the original source link and PDF limit', async () => {
  const blobs = downloads(), view = await page()
  try {
    expect(view.host.textContent).toContain('PDF 导出尚未接通')
    expect(view.host.querySelector('a[download]')?.getAttribute('href')).toContain(`/research/runs/${fixture.run_id}/report/source`)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await act(async () => button(view.host, '保存阅读快照 JSON').click())
    expect(blobs).toHaveLength(1)
    const saved = JSON.parse(await blobText(blobs[0]))
    expect(saved.report.source_json).toBe(fixture.source_json)
    expect(saved.report.artifact_json).toBe(fixture.artifact_json)
    expect(saved.report.probability_definitions).toEqual(fixture.probability_definitions)
    expect(saved.report.financial_qualified).toBe(false)
    expect(saved.report.execution_authorized).toBe(false)
    expect(saved.report.bundle_export_status).toBe('unsupported_perpetual_version')
  } finally { await view.close() }
})

it('downloads existing Chinese report sections as standalone inert HTML without inventing unknown comparisons', async () => {
  const blobs = downloads(), view = await page()
  try {
    const displayed = view.host.querySelector('[aria-label="已保存的永续研究报告"]')!
    const headline = displayed.querySelector('h3')!.textContent
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await act(async () => button(view.host, '保存离线 HTML').click())
    const html = new DOMParser().parseFromString(await blobText(blobs[0]), 'text/html')
    expect(html.body.textContent).toContain(headline)
    expect(html.body.textContent).toContain('条件盈利概率、单位预期净收益与首选方案保持未知')
    expect(html.body.textContent).toContain('概率定义原件与任务绑定')
    expect(html.querySelector('script,nav,button,a[href],link,iframe,img')).toBeNull()
    const saved = JSON.parse(html.querySelector('[data-perpetual-reading-json]')!.textContent!)
    expect(saved.report.run_id).toBe(fixture.run_id)
    expect(saved.report.probability_definitions).toEqual(fixture.probability_definitions)
  } finally { await view.close() }
})

it('stops offering the previous Run while a new Run is still loading', async () => {
  const view = await page()
  try {
    expect(button(view.host, '保存阅读快照 JSON')).toBeDefined()
    vi.mocked(fetch).mockImplementation(() => new Promise<Response>(() => undefined))
    await act(async () => view.root.render(<PerpetualSavedReportBoundary runId="another-run"><p>旧分支</p></PerpetualSavedReportBoundary>))
    expect(view.host.textContent).toContain('正在核对已保存报告')
    expect(view.host.textContent).not.toContain('保存阅读快照 JSON')
    expect(view.host.querySelector('[aria-label="已保存的永续研究报告"]')).toBeNull()
  } finally { await view.close() }
})

it('clears the previous owner value on a session change before rereading completes', async () => {
  const view = await page()
  try {
    expect(button(view.host, '保存离线 HTML')).toBeDefined()
    vi.mocked(settingsApi.session).mockReturnValue({ owner_id: 'another-owner', csrf_token: 'synthetic-csrf', expires_at: '2099-01-01T00:00:00Z' })
    vi.mocked(fetch).mockImplementation(() => new Promise<Response>(() => undefined))
    await act(async () => window.dispatchEvent(new Event('decision-hub-settings-session-changed')))
    expect(view.host.textContent).toContain('正在核对已保存报告')
    expect(view.host.textContent).not.toContain('保存离线 HTML')
    expect(view.host.querySelector('[aria-label="已保存的永续研究报告"]')).toBeNull()
  } finally { await view.close() }
})

it('removes reading exports when the report is hidden', async () => {
  const view = await page()
  try {
    expect(button(view.host, '保存离线 HTML')).toBeDefined()
    await act(async () => view.root.render(<PerpetualSavedReportBoundary runId={fixture.run_id} visible={false}><p>旧分支</p></PerpetualSavedReportBoundary>))
    expect(view.host.textContent).not.toContain('保存离线 HTML')
    expect(view.host.querySelector('[aria-label="已保存的永续研究报告"]')).toBeNull()
  } finally { await view.close() }
})

it('invalidates a captured export action synchronously when the session-change event arrives', async () => {
  const blobs = downloads(), view = await page()
  try {
    const previous = button(view.host, '保存阅读快照 JSON')
    vi.mocked(fetch).mockImplementation(() => new Promise<Response>(() => undefined))
    await act(async () => {
      window.dispatchEvent(new Event('decision-hub-settings-session-changed'))
      previous.click()
    })
    expect(blobs).toHaveLength(0)
    expect(view.host.textContent).not.toContain('保存阅读快照 JSON')
  } finally { await view.close() }
})

it('refuses a changed owner even before the page receives a session event', async () => {
  const blobs = downloads(), view = await page()
  try {
    vi.mocked(settingsApi.session).mockReturnValue({ owner_id: 'another-owner', csrf_token: 'PRIVATE_CREDENTIAL', expires_at: '2099-01-01T00:00:00Z' })
    await act(async () => button(view.host, '保存阅读快照 JSON').click())
    expect(blobs).toHaveLength(0)
    expect(view.host.querySelector('[role="alert"]')?.textContent).toContain('尚未保存成功')
    expect(view.host.textContent).not.toContain('PRIVATE_CREDENTIAL')
  } finally { await view.close() }
})

it('reports download failures safely without changing the saved report or qualification', async () => {
  downloads()
  const view = await page()
  try {
    vi.mocked(URL.createObjectURL).mockImplementation(() => { throw new Error('PRIVATE_ERROR_DETAIL') })
    await act(async () => button(view.host, '保存离线 HTML').click())
    expect(view.host.querySelector('[role="alert"]')?.textContent).toContain('尚未保存成功')
    expect(view.host.textContent).not.toContain('PRIVATE_ERROR_DETAIL')
    expect(view.host.querySelector('[aria-label="已保存的永续研究报告"]')).not.toBeNull()
    expect(view.host.textContent).toContain('尚未取得金融资格')
  } finally { await view.close() }
})
