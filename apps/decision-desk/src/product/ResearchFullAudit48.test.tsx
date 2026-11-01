// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi, researchQualitySchema, ResearchReportServingError } from '../api/researchQuality'
import { ResearchQuality } from './ResearchQuality'
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())
const original = () => researchQualitySchema.parse(JSON.parse(readFileSync(resolve(process.cwd(),
  '../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json'), 'utf8')))
it('runs no full audit by default, starts only on the explicit button and shows the exact scope', async () => {
  const body = original(); vi.spyOn(researchQualityApi, 'read').mockResolvedValue(body)
  const audit = vi.spyOn(researchQualityApi, 'audit').mockResolvedValue({ view: body, comparison: 'saved_bundle_full_quality', state_version: 7, checked_at: body.as_of })
  const element = document.createElement('div'); document.body.append(element); const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId={body.run_id} />)); expect(audit).not.toHaveBeenCalled()
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '完整原库核对')!.click())
    expect(audit).toHaveBeenCalledOnce(); expect(element.textContent).toContain('全部字段与已确认保存材料一致')
    expect(element.textContent).not.toContain('saved_bundle_full_quality')
  } finally { await act(async () => root.unmount()); element.remove() }
})
it('drops an old pending audit on run changes and ignores its later success', async () => {
  const body = original(); let resolveAudit: (value: { view: typeof body; comparison: 'saved_bundle_full_quality'; state_version: number; checked_at: string }) => void = () => {}
  vi.spyOn(researchQualityApi, 'read').mockResolvedValueOnce(body).mockRejectedValueOnce(new ResearchReportServingError({ run_id: 'other', state: 'revoked', state_version: 8, confirmed_at: null, reason_code: null }))
  vi.spyOn(researchQualityApi, 'audit').mockImplementationOnce(() => new Promise(resolve => { resolveAudit = resolve }))
  const element = document.createElement('div'); document.body.append(element); const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId={body.run_id} />))
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '完整原库核对')!.click())
    expect(element.textContent).toContain('正在核对完整原库材料')
    await act(async () => root.render(<ResearchQuality runId="other" />))
    await act(async () => resolveAudit({ view: body, comparison: 'saved_bundle_full_quality', state_version: 7, checked_at: body.as_of }))
    expect(element.textContent).toContain('这份报告当前不可读取'); expect(element.textContent).not.toContain('本次完整原库核对结果')
    expect(element.querySelector('a[download]')).toBeNull()
  } finally { await act(async () => root.unmount()); element.remove() }
})
