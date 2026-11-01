// @vitest-environment jsdom
// Synthetic wire controls only. V6 SQL/native commit/readback has separate tests.
import { webcrypto } from 'node:crypto'
import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { settingsApi } from './decisionSettings'
import nativeIncomplete from '../product/__fixtures__/saved-report-delivery-native-incomplete.json'
import nativeComplete from '../product/__fixtures__/saved-report-delivery-native-complete.json'
import { PerpetualSavedReportBoundary } from '../product/PerpetualSavedReport'
import { buildPerpetualReadingSnapshot, renderPerpetualReadingHtml } from '../product/perpetualReportExport'
import * as savedReportApi from './perpetualSavedReport'
import { readSavedPerpetualReport } from './perpetualSavedReport'
import { changedDelivery, deliveryWire, reportParts, reseal } from './perpetualSavedReportDelivery.test-support'
import { decisionBodyHash as hash } from './decisionOriginals'

Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())

async function read(wire: { run_id: string }) {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(wire)))
  return readSavedPerpetualReport(wire.run_id)
}

it('reads explicit V6 incomplete report through the original reader with both permissions false', async () => {
  const wire = await deliveryWire(), report = await read(wire)
  expect(report?.artifact.headline).toBe('合成四小时永续报告')
  expect(report?.financial_qualified).toBe(false)
  expect(report?.execution_authorized).toBe(false)
  expect(JSON.parse(report!.source_json).report_delivery.status).toBe('incomplete')
})

it('decodes the exact synthetic-native V6 artifact saved and read back by the original server', async () => {
  const report = await read(nativeIncomplete)
  expect(report?.source_sha256).toBe(nativeIncomplete.source_sha256)
  expect(report?.artifact_sha256).toBe(nativeIncomplete.artifact_sha256)
  expect(report?.financial_qualified).toBe(false)
  expect(report?.execution_authorized).toBe(false)
  expect(JSON.parse(report!.source_json).report_delivery.status).toBe('incomplete')
})

it('reads the exact completed synthetic-native V6 without upgrading financial or execution permission', async () => {
  const report = await read(nativeComplete)
  expect(report?.source_sha256).toBe(nativeComplete.source_sha256)
  expect(report?.financial_qualified).toBe(false)
  expect(report?.execution_authorized).toBe(false)
  expect(JSON.parse(report!.source_json).report_delivery.status).toBe('complete')
})

it('renders the completed V6 in the original component and preserves full JSON in self-contained HTML', async () => {
  const report = await read(nativeComplete)
  if (report === null) throw new Error('Expected original saved report')
  vi.spyOn(savedReportApi, 'readSavedPerpetualReport').mockResolvedValue(report)
  const host = document.createElement('div'), root = createRoot(host)
  try {
    await act(async () => root.render(createElement(PerpetualSavedReportBoundary, { runId: report.run_id, children: null })))
    expect(host.textContent).toContain(report.artifact.headline)
    expect(host.textContent).toContain('尚未取得金融资格')
    expect(host.textContent).toContain('保存阅读快照 JSON')
    expect(host.textContent).toContain('保存离线 HTML')
    const section = host.querySelector<HTMLElement>('[aria-label="已保存的永续研究报告"]')!
    const snapshot = JSON.parse(buildPerpetualReadingSnapshot(report))
    expect(snapshot.report.source_json).toBe(nativeComplete.source_json)
    expect(snapshot.report.artifact_json).toBe(nativeComplete.artifact_json)
    const offline = new DOMParser().parseFromString(renderPerpetualReadingHtml(report, section), 'text/html')
    expect(JSON.parse(offline.querySelector('[data-perpetual-reading-json]')!.textContent!)).toEqual(snapshot)
    expect(offline.querySelectorAll('script,iframe,object,embed,img,link,[src]').length).toBe(0)
  } finally { await act(async () => root.unmount()) }
})

it.each(['synthesis', 'review_request', 'authorization', 'findings', 'catalog', 'source_binding'])(
  'rejects a rehashed V6 report whose %s delivery input differs', async key => {
    const wire = await changedDelivery(value => { value.input_hashes[key] = '0'.repeat(64) })
    await expect(read(wire)).rejects.toThrow()
  },
)

it.each(['run_id', 'decision_at', 'financial_qualified', 'execution_authorized'])(
  'rejects rehashed delivery with invalid %s', async key => {
    const values = { run_id: 'foreign', decision_at: '2099-01-01T00:00:00Z',
      financial_qualified: true, execution_authorized: true }
    const wire = await changedDelivery(value => { value[key] = values[key as keyof typeof values] })
    await expect(read(wire)).rejects.toThrow()
  },
)

it('rejects complete without actual content-review findings and evidence', async () => {
  const wire = await changedDelivery(value => { value.status = 'complete'; value.reasons = [] })
  await expect(read(wire)).rejects.toThrow()
})

it('rejects source delivery that differs from the packet delivery despite new outer hashes', async () => {
  const parts = reportParts(await deliveryWire())
  parts.source.report_delivery.reasons = ['foreign_reason']
  const result = await read(await reseal(parts)).then(() => 'accepted', error => error.message)
  expect(result).toBe('perpetual_report_delivery_original_mismatch')
})

it('rejects V6 packet relabelled as a V5 source', async () => {
  const parts = reportParts(await deliveryWire())
  parts.source.schema_version = 'research-publication-source.v5'
  await expect(read(await reseal(parts))).rejects.toThrow()
})

it('rejects an authenticated incomplete report whose native wrapper hash was removed and resealed', async () => {
  const parts = reportParts(nativeIncomplete)
  parts.packet.binding.native_review_wrapper_sha256 = null
  const basis = { ...parts.packet.binding, schema_version: 'perpetual-report-binding.v1' }
  delete basis.report_delivery
  parts.packet.binding.report_delivery.input_hashes.source_binding = await hash(basis)
  parts.source.report_delivery = parts.packet.binding.report_delivery
  const result = await read(await reseal(parts)).then(() => 'accepted', error => error.message)
  expect(result).toBe('perpetual_report_delivery_original_mismatch')
})

it('rejects complete content whose three evidence references are aliases of one original', async () => {
  const parts = reportParts(nativeComplete)
  const first = parts.packet.binding.report_delivery.key_evidence_refs[0]
  parts.packet.binding.report_delivery.key_evidence_refs = [first, first, first]
  parts.source.report_delivery = parts.packet.binding.report_delivery
  const result = await read(await reseal(parts)).then(() => 'accepted', error => error.message)
  expect(result).toBe('perpetual_report_delivery_original_mismatch')
})
