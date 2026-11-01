// @vitest-environment jsdom
import { act } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import * as clientApi from '../api/client'
import { researchQualityApi, researchQualitySchema, ResearchQualityReadError, type ResearchQualityView } from '../api/researchQuality'
import { ResearchQuality } from './ResearchQuality'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })
const unavailable = (runId: string): ResearchQualityView => ({
  schema_version: 'research-quality-view.v1', run_id: runId, as_of: '2026-10-08T00:00:00Z',
  status: 'unavailable', reason_code: 'research_quality_legacy_unavailable',
  gate_status: null, published_at: null, publication_ref: null, graph_final_ref: null,
  recipe_selection_ref: null, quality_sidecar_id: null, decision: null, budget: null, unknown_receipt_codes: [],
})

it('shows missing historical qualification explicitly and never calls a research command', async () => {
  const read = vi.spyOn(researchQualityApi, 'read').mockResolvedValue(unavailable('old'))
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId="old" />))
    expect(read).toHaveBeenCalledTimes(1)
    expect(element.textContent).toContain('暂不能补认当前资格')
    expect(element.textContent).not.toContain('研究质量门已通过')
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('clears the prior run on read failure and keeps qualification unconfirmed', async () => {
  vi.spyOn(researchQualityApi, 'read').mockResolvedValueOnce(unavailable('old')).mockRejectedValue(new Error('original damaged'))
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId="old" />))
    await act(async () => root.render(<ResearchQuality runId="new" />))
    expect(element.querySelector('[role="alert"]')?.textContent).toContain('无法确认结论资格')
    expect(element.textContent).not.toContain('这份历史研究')
    expect(element.textContent).not.toContain('研究质量门已通过')
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('shows a timeout as an unconfirmed read rather than failed original verification', async () => {
  vi.spyOn(researchQualityApi, 'read').mockRejectedValue(new ResearchQualityReadError('timeout'))
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId="old" />))
    expect(element.querySelector('[role="alert"]')?.textContent).toContain('读取质量原件超时，尚未确认核验结果')
    expect(element.textContent).not.toContain('暂未通过核验')
    expect(element.textContent).not.toContain('研究质量门已通过')
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('lets the user stop waiting, ignores a late reply, and explicitly read again', async () => {
  let finishRead!: (value: ResearchQualityView) => void
  const read = vi.spyOn(researchQualityApi, 'read').mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve }))
    .mockResolvedValueOnce(unavailable('old'))
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId="old" />))
    expect(element.querySelector('[role="status"]')?.textContent).toContain('正在读取并核对质量原件')
    const signal = read.mock.calls[0][1]!
    await act(async () => (Array.from(element.querySelectorAll('button')).find(button => button.textContent === '停止等待')!).click())
    expect(signal.aborted).toBe(true)
    expect(element.textContent).toContain('已停止等待，质量核验结果尚未确认')
    await act(async () => finishRead(unavailable('old')))
    expect(element.textContent).not.toContain('这份历史研究')
    expect(element.querySelector('[role="alert"]')).toBeNull()
    await act(async () => (Array.from(element.querySelectorAll('button')).find(button => button.textContent === '重新读取')!).click())
    expect(read).toHaveBeenCalledTimes(2)
    expect(element.textContent).toContain('暂不能补认当前资格')
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('keeps actual old04 bytes readable and links the exact report to the configured HTTP attachment endpoint', async () => {
  const fixture = researchQualitySchema.parse(JSON.parse(readFileSync(resolve(
    process.cwd(), '../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json',
  ), 'utf8')))
  const raw = fixture.decision!.full_report_json
  const createUrl = vi.fn()
  const revokeUrl = vi.fn()
  const NativeURL = URL
  vi.stubGlobal('URL', class extends NativeURL {
    static createObjectURL = createUrl
    static revokeObjectURL = revokeUrl
  })
  const actualApiUrl = clientApi.apiUrl
  const downloadUrl = vi.spyOn(clientApi, 'apiUrl').mockImplementation(path => actualApiUrl(path, 'http://127.0.0.1:8047/'))
  vi.spyOn(researchQualityApi, 'read').mockResolvedValue(fixture)
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId={fixture.run_id} />))
    expect(element.textContent).toContain('对应资产的日线趋势背景')
    expect(element.textContent).toContain('对应资产的衍生品仓位与拥挤程度')
    expect(element.textContent).toContain('关键市场数据仍未取得')
    expect(element.textContent).toContain('比特币日线')
    expect(element.textContent).not.toContain('market_regime_context')
    expect(element.textContent).not.toContain('derivatives_crowding')
    expect(element.textContent).not.toContain('The DSH round')
    expect(element.textContent).not.toContain('"field_statuses"')
    expect(element.querySelectorAll('pre')).toHaveLength(0)
    const originals = Array.from(element.querySelectorAll('details')).find(item => item.firstElementChild?.textContent === '查看完整原报告 JSON')!
    await act(async () => { originals.open = true; originals.dispatchEvent(new Event('toggle')) })
    expect(originals.querySelector('pre')?.textContent).toBe(raw)
    const link = Array.from(element.querySelectorAll('a')).find(item => item.textContent === '下载完整原报告')!
    expect(link.isConnected).toBe(true)
    expect(link.href).toBe(`http://127.0.0.1:8047/v1/research/runs/${fixture.run_id}/report/download`)
    expect(downloadUrl).toHaveBeenCalledWith(`/v1/research/runs/${fixture.run_id}/report/download`)
    expect(new NativeURL(link.href).search).toBe('')
    expect(link.download).toBe(`research-${fixture.run_id}.json`)
    expect(createUrl).not.toHaveBeenCalled()
    expect(revokeUrl).not.toHaveBeenCalled()
    expect(fixture.decision!.full_report_json).toBe(raw)
    await act(async () => root.render(<ResearchQuality runId="new-run" />))
    expect(element.querySelector('a[download]')).toBeNull()
  } finally { await act(async () => root.unmount()); element.remove() }
})
