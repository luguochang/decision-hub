// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import App from './App'
import { api } from './api/client'
import type { RunInspector, RunView } from './api/types'

vi.mock('./api/client', () => {
  const pending = () => new Promise(() => undefined)
  return { api: { summary: pending, ledgerRunsPage: pending, runInspector: pending, health: pending, researchMemos: pending, evolutionOverview: pending, evolutionJobs: pending, operations: pending } }
})

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | undefined, container: HTMLDivElement | undefined, client: QueryClient | undefined
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  client?.clear(); container?.remove(); vi.restoreAllMocks()
  window.history.replaceState(null, '', '#/home')
})

function record(status: RunView['status']): RunView {
  return { run_id: 'run-refresh', event_id: 'evt-refresh', status, strategy_version: 'baseline.v1', runtime_version: 'rules', snapshot_id: null, artifact_id: null, created_at: '2026-09-30T01:00:00Z', updated_at: '2026-09-30T01:00:00Z', finished_at: null, latency_ms: null, cost_usd: null, error_code: status === 'failed' ? 'execution_failure' : null, headline: '正在验收的历史记录', gate_status: null }
}
function inspector(run: RunView): RunInspector {
  return { run, artifact: null, timeline: [], calls: [], steps: [], evaluations: [], evaluation_count: 0, snapshot_cutoff_at: null, snapshot_hash: null, evidence_lineage: [], versions: { strategy_version: run.strategy_version, runtime_version: run.runtime_version, pack_version: null, provider_ids: [], models: [], schema_versions: [], pricing_versions: [] }, orchestration: { mode: 'fixed_graph', supervisor_role: null, planned_capabilities: [], required_capabilities: [], specialist_coverage: [], missing_capabilities: [], replan_count: 0, experiment_refs: [] } }
}
async function settle(predicate: () => boolean) {
  for (let attempt = 0; attempt < 50 && !predicate(); attempt++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)) })
  expect(predicate()).toBe(true)
}

it.each([
  ['admitted', '等待执行', 'failed', '执行失败'],
  ['running', '执行中', 'completed', '执行完成'],
] as const)('updates the selected %s ledger record after an explicit detail refresh', async (initialStatus, initialLabel, finalStatus, finalLabel) => {
  const initial = record(initialStatus), terminal = record(finalStatus)
  vi.spyOn(api, 'ledgerRunsPage').mockResolvedValue({ items: [initial], total: 1 })
  const read = vi.spyOn(api, 'runInspector').mockResolvedValueOnce(inspector(initial)).mockResolvedValue(inspector(terminal))
  window.history.replaceState(null, '', '#/Inbox?kind=baseline&before=2026-10-01T00%3A00%3A00Z')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => root!.render(<QueryClientProvider client={client!}><App /></QueryClientProvider>))
  await settle(() => Boolean(container!.querySelector('[aria-label="查看记录 run-refresh"]')))
  await act(async () => container!.querySelector<HTMLButtonElement>('[aria-label="查看记录 run-refresh"]')!.click())
  await settle(() => Boolean(container!.querySelector('[role="dialog"] .detail-grid')))
  const drawer = () => container!.querySelector('[role="dialog"]')!
  expect(drawer().querySelector('.drawer-meta .status-pill')!.textContent).toBe(initialLabel)
  const refresh = [...drawer().querySelectorAll('button')].find(button => button.textContent === '刷新执行详情')!
  await act(async () => refresh.click())
  await settle(() => drawer().querySelector('.drawer-meta .status-pill')?.textContent === finalLabel)
  const stateCell = [...drawer().querySelectorAll('.detail-cell')].find(cell => cell.querySelector('span')?.textContent === '执行状态')!
  expect(stateCell.querySelector('strong')!.textContent).toBe(finalLabel)
  expect(new URLSearchParams(window.location.hash.split('?')[1]).get('record')).toBe(initial.run_id)
  expect(read.mock.calls).toEqual([[initial.run_id], [initial.run_id]])
  if (finalStatus === 'failed') expect(drawer().textContent).toContain('execution_failure')
})
