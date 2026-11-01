// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi, researchQualitySchema, ResearchReportServingError, type ResearchQualityServingRead } from '../api/researchQuality'
import { ResearchQuality } from './ResearchQuality'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())
const original = () => researchQualitySchema.parse(JSON.parse(readFileSync(resolve(process.cwd(),
  '../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json'), 'utf8')))

it.each([['confirmed_bundle', '读取已确认保存材料', 7], ['legacy_full_original', '完整历史原件核对', 0], ['confirmed_report_snapshot', '已读取确认保存的报告', 7]] as const)('shows successful %s in Chinese with optional details only', async (mode, title, version) => {
  const body = original()
  vi.spyOn(researchQualityApi, 'read').mockImplementation(async (_run, _signal, options) => {
    options?.onServingRead?.({ run_id: body.run_id, read_mode: mode, state_version: version, checked_at: body.as_of })
    return body
  })
  const element = document.createElement('div'); document.body.append(element); const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId={body.run_id} />))
    expect(element.textContent).toContain(title)
    expect(element.textContent).not.toContain(mode)
    const detail = Array.from(element.querySelectorAll('details')).find(value => value.querySelector('summary')?.textContent === '本次读取核验')!
    expect(detail.open).toBe(false)
    expect(detail.textContent).toContain(`状态版本：${version}`)
    expect(detail.textContent).toContain('核验时间：')
    expect(element.querySelector('a[download]')).not.toBeNull()
    if (mode === 'confirmed_report_snapshot') {
      expect(new URL(element.querySelector<HTMLAnchorElement>('a[download]')!.href).pathname).toBe(`/v1/research/runs/${body.run_id}/report/snapshot/download`)
      expect(detail.textContent).toContain('未重新完整审计材料')
    }
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('clears successful metadata on another run revocation and ignores the stale callback', async () => {
  const body = original(); let oldCallback: ((read: ResearchQualityServingRead) => void) | undefined
  vi.spyOn(researchQualityApi, 'read').mockImplementationOnce(async (_run, _signal, options) => {
    oldCallback = options?.onServingRead
    oldCallback?.({ run_id: body.run_id, read_mode: 'confirmed_bundle', state_version: 7, checked_at: body.as_of })
    return body
  }).mockRejectedValueOnce(new ResearchReportServingError({ run_id: 'another-run', state: 'revoked', state_version: 8, confirmed_at: null, reason_code: null }))
  const element = document.createElement('div'); document.body.append(element); const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId={body.run_id} />))
    expect(element.textContent).toContain('读取已确认保存材料')
    await act(async () => root.render(<ResearchQuality runId="another-run" />))
    await act(async () => oldCallback?.({ run_id: body.run_id, read_mode: 'confirmed_bundle', state_version: 7, checked_at: body.as_of }))
    expect(element.textContent).toContain('这份报告当前不可读取')
    expect(element.textContent).not.toContain('读取已确认保存材料')
    expect(element.textContent).not.toContain('本次读取核验')
    expect(element.querySelector('a[download]')).toBeNull()
  } finally { await act(async () => root.unmount()); element.remove() }
})
