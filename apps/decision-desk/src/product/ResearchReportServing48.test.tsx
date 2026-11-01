// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi, researchQualitySchema, ResearchReportServingError, type ResearchReportServingStatus } from '../api/researchQuality'
import { ResearchQuality } from './ResearchQuality'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())
const pending = (state: ResearchReportServingStatus['state']): ResearchReportServingStatus => ({
  run_id: 'private-run', state, state_version: 2, confirmed_at: state === 'confirmed' ? '2026-10-10T00:00:00Z' : null, reason_code: null,
})

it.each([
  ['queued', '报告正在排队整理'], ['prepared', '报告已整理，正在确认'], ['unconfirmed', '报告整理尚未完成'],
  ['cancelled', '本次报告整理已取消'], ['revoked', '这份报告当前不可读取'], ['confirmed', '报告已确认'],
] as const)('shows %s in Chinese without a false integrity failure or download', async (state, title) => {
  const read = vi.spyOn(researchQualityApi, 'read').mockRejectedValue(new ResearchReportServingError(pending(state)))
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId="private-run" />))
    expect(element.textContent).toContain(title)
    expect(element.textContent).not.toContain('暂未通过核验')
    expect(element.textContent).not.toContain(state)
    expect(element.querySelector('[role="alert"]')).toBeNull()
    expect(element.querySelector('a[download]')).toBeNull()
    const retry = Array.from(element.querySelectorAll('button')).find(button => button.textContent === '重试读取')!
    await act(async () => retry.click())
    expect(read).toHaveBeenCalledTimes(2)
    await act(async () => root.render(<ResearchQuality runId="private-run" visible={false} />))
    expect(element.textContent).toBe('')
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('clears the prior state on run changes and hides internal reason values', async () => {
  const read = vi.spyOn(researchQualityApi, 'read').mockRejectedValueOnce(new ResearchReportServingError({
    ...pending('unconfirmed'), reason_code: 'publication_bundle_commit_unconfirmed',
  })).mockImplementationOnce(() => new Promise(() => {}))
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId="private-run" />))
    expect(element.textContent).toContain('报告保存回执尚未确认')
    expect(element.textContent).not.toContain('publication_bundle_commit_unconfirmed')
    await act(async () => root.render(<ResearchQuality runId="other-run" />))
    expect(element.textContent).not.toContain('报告保存回执尚未确认')
    expect(element.querySelector('a[download]')).toBeNull()
    expect(read).toHaveBeenCalledTimes(2)
  } finally { await act(async () => root.unmount()); element.remove() }
})

it('makes the exact original download available only after an explicit successful reread', async () => {
  const original = researchQualitySchema.parse(JSON.parse(readFileSync(resolve(process.cwd(),
    '../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json'), 'utf8')))
  const raw = original.decision!.full_report_json
  const read = vi.spyOn(researchQualityApi, 'read').mockRejectedValueOnce(new ResearchReportServingError({
    ...pending('prepared'), run_id: original.run_id,
  })).mockResolvedValueOnce(original)
  const element = document.createElement('div'); document.body.append(element)
  const root = createRoot(element)
  try {
    await act(async () => root.render(<ResearchQuality runId={original.run_id} />))
    expect(element.querySelector('a[download]')).toBeNull()
    await act(async () => Array.from(element.querySelectorAll('button')).find(button => button.textContent === '重试读取')!.click())
    expect(element.textContent).not.toContain('报告已整理，正在确认')
    const download = element.querySelector<HTMLAnchorElement>('a[download]')!
    expect(download.href.endsWith(`/v1/research/runs/${original.run_id}/report/download`)).toBe(true)
    expect(download.download).toBe(`research-${original.run_id}.json`)
    expect(read).toHaveBeenCalledTimes(2)
    expect(original.decision!.full_report_json).toBe(raw)
  } finally { await act(async () => root.unmount()); element.remove() }
})
