// @vitest-environment jsdom

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import type { ResearchRunView } from '../api/types'
import { ResearchPage } from './ResearchPage'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const now = '2026-10-01T00:00:00Z'
const anotherRun = {
  schema_version: 'research-run-view.v2', run_id: 'another-readable-run',
  event_id: 'another-event', event_title: '另一份已保存研究',
  admission_origin: 'manual', priority: 'high', status: 'degraded', stage: 'done',
  runtime_id: 'dsh', profile_ref: 'decision-research.web.v1', current_round: 1,
  budget: null, coverage: null, current_action: '已保存', stop_reason: null,
  failure: null, latest_sequence_no: 1, artifact_id: 'another-artifact',
  available_at: null, parent_run_id: null, created_at: now, updated_at: now,
} as ResearchRunView

let root: Root | null = null
let container: HTMLDivElement | null = null
let queryClient: QueryClient | null = null

afterEach(async () => {
  if (root) await act(async () => root?.unmount())
  queryClient?.clear()
  container?.remove()
  root = null
  queryClient = null
  container = null
  sessionStorage.clear()
  vi.restoreAllMocks()
  window.history.replaceState(null, '', '/')
})

async function flushQueries(): Promise<void> {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) })
}

async function renderError(scene: string, error: string) {
  const runId = 'linked-run-outside-first-page'
  const back = '#/runs?q=BTC&status=failed&offset=50&back=%23%2Fresearch'
  window.history.replaceState(null, '', `/#/${scene}?${new URLSearchParams({ run: runId, back })}`)
  vi.spyOn(api, 'researchRuns').mockResolvedValue([anotherRun])
  vi.spyOn(api, 'researchInbox').mockResolvedValue({ schema_version: 'research-inbox-view.v1', items: [], generated_at: now })
  vi.spyOn(api, 'macroEvents').mockResolvedValue([])
  vi.spyOn(api, 'strategyCalibrations').mockResolvedValue([])
  vi.spyOn(api, 'researchRunScope').mockResolvedValue(null)
  vi.spyOn(api, 'researchReviewPlans').mockResolvedValue([])
  vi.spyOn(api, 'researchLinkedCommandReceipt').mockResolvedValue(null)
  for (const method of ['researchRunComparison', 'researchObservability', 'researchValueEvaluation', 'researchForecastAudit'] as const) {
    vi.spyOn(api, method).mockRejectedValue(new Error('Optional diagnostic unavailable'))
  }
  const readRun = vi.spyOn(api, 'researchRun').mockRejectedValue(new Error(error))
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  await act(async () => root!.render(<QueryClientProvider client={queryClient!}><ResearchPage operationsLoading={false} operationsFailed={false} /></QueryClientProvider>))
  await flushQueries()
  await flushQueries()
  return { readRun, runId, back }
}

describe('research detail route identity on read failure', () => {
  for (const scene of ['report', 'report-incomplete', 'technical']) {
    it.each([
      ['research_run_not_found', '未找到链接中的研究任务'],
      ['请求超时，尚未确认结果。', '这次研究暂时读取失败'],
    ])(`keeps the linked Run on ${scene} when the detail read fails with %s`, async (error, heading) => {
      const { readRun, runId, back } = await renderError(scene, error)
      expect(readRun.mock.calls.map(([id]) => id)).toEqual([runId])
      expect(new URLSearchParams(window.location.hash.split('?')[1]).get('run')).toBe(runId)
      expect(new URLSearchParams(window.location.hash.split('?')[1]).get('back')).toBe(back)
      expect(new URLSearchParams(window.location.search).get('run_id')).toBe(runId)
      expect(container!.querySelector('[role="alert"]')?.textContent).toContain(heading)
      expect(container!.querySelector('.research-detail-panel h2')).toBeNull()
      expect(container!.textContent).not.toContain('已自动打开最新可读报告')

      const retry = Array.from(container!.querySelectorAll<HTMLButtonElement>('button')).find(button => button.textContent === '重新读取这次研究')!
      expect(retry.disabled).toBe(false)
      await act(async () => retry.click())
      await flushQueries()
      expect(readRun.mock.calls.map(([id]) => id)).toEqual([runId, runId])
      expect(new URLSearchParams(window.location.hash.split('?')[1]).get('run')).toBe(runId)
      expect(container!.querySelector('[role="alert"]')?.textContent).toContain(heading)
    })
  }
})
