// @vitest-environment jsdom

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { EvolutionJob, OperationsOverview } from './api/types'
import { researchServiceHealth } from './product/operationLabels'
import { EvolutionExecutionStatus, EvolutionJobQueue, OperationsPage } from './OperationsPage'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const operations: OperationsOverview = {
  schema_version: 'operations-overview.v1',
  checked_at: '2026-08-29T01:00:00Z',
  services: [
    { service_id: 'hub-api', role: 'api', instance_id: 'local:1:api', version: '0.1.0', mode: 'fake', status: 'online', interval_seconds: 10, started_at: '2026-08-29T00:59:00Z', heartbeat_at: '2026-08-29T01:00:00Z', last_error_code: null },
    { service_id: 'hub-research-worker', role: 'research_worker', instance_id: null, version: null, mode: null, status: 'offline', interval_seconds: null, started_at: null, heartbeat_at: null, last_error_code: null },
    { service_id: 'hub-realtime-worker', role: 'realtime_worker', instance_id: null, version: null, mode: null, status: 'offline', interval_seconds: null, started_at: null, heartbeat_at: null, last_error_code: null },
    { service_id: 'hub-evolution-worker', role: 'evolution_worker', instance_id: null, version: null, mode: null, status: 'offline', interval_seconds: null, started_at: null, heartbeat_at: null, last_error_code: null },
  ],
  runtime: { runtime_id: 'fake', runtime_version: 'fake.v1', mode: 'fake', provider_configured: false, live_canary_status: 'not_run', model: null, api_mode: null },
  sources: [], capabilities: [],
  jobs: { queued: 0, running: 0, retry_wait: 0, waiting_inputs: 0, pending_owner_review: 1, completed: 0, failed: 0, cancelled: 0 },
  recent_jobs: [],
}

const job: EvolutionJob = {
  job_id: 'job-1', schema_version: 'evolution-job.v1', trigger_key: 'feedback:1', trigger_type: 'feedback', domain_pack_ref: 'crypto_macro.v1', status: 'pending_owner_review', stage: 'review', input_refs: ['feedback:1'], candidate_id: 'candidate-1', experiment_refs: ['replay-1', 'holdout-1', 'shadow-1'], result_refs: ['result-1'], attempt: 1, max_attempts: 3, lease_owner: null, lease_expires_at: null, next_attempt_at: null, last_error_code: null, created_at: '2026-08-29T00:00:00Z', updated_at: '2026-08-29T01:00:00Z', finished_at: null,
}

let container: HTMLDivElement | null = null

afterEach(() => {
  container?.remove()
  container = null
})

describe('Operations and Evolution observability', () => {
  it('shows a saved candidate waiting for inputs without claiming evaluation success', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<EvolutionJobQueue jobs={[{ ...job, status: 'waiting_inputs', stage: 'candidate', result_refs: [], experiment_refs: [], last_error_code: 'evaluation_cross_split_overlap' }]} loading={false} failed={false} />))
    expect(container.textContent).toContain('等待评测材料')
    expect(container.textContent).toContain('候选已保存')
    expect(container.textContent).toContain('当前没有评测成绩')
    expect(container.querySelector('details')?.open).toBe(false)
    expect(container.querySelector('details')?.textContent).toContain('evaluation_cross_split_overlap')
    expect(container.querySelector('.job-heading')?.textContent).not.toContain('job-1')
    await act(async () => root.unmount())
  })

  it('keeps active tasks visible without burying candidates under the whole queue', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    const jobs: EvolutionJob[] = Array.from({ length: 8 }, (_, i) => ({ ...job, job_id: `job-${i}`, status: i === 7 ? 'running' : 'queued' }))
    await act(async () => root.render(<EvolutionJobQueue jobs={jobs} loading={false} failed={false} />))
    expect(container.querySelectorAll('article')).toHaveLength(5)
    expect(container.querySelector('article')?.textContent).toContain('执行中')
    const button = container.querySelector<HTMLButtonElement>('.job-list-toggle')!
    await act(async () => button.click())
    expect(container.querySelectorAll('article')).toHaveLength(8)
    await act(async () => button.click())
    expect(container.querySelectorAll('article')).toHaveLength(5)
    await act(async () => root.unmount())
  })

  it('distinguishes saved historical scores from waiting for prospective observations', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<EvolutionJobQueue jobs={[{ ...job, status: 'waiting_inputs', stage: 'shadow', experiment_refs: ['replay', 'holdout'], result_refs: ['r1', 'r2'], last_error_code: 'prospective_evaluation_required' }]} loading={false} failed={false} />))
    expect(container.textContent).toContain('已保存 2 个历史评测阶段')
    expect(container.textContent).toContain('正在等待真实前瞻观察')
    expect(container.textContent).not.toContain('当前没有评测成绩')
    await act(async () => root.unmount())
  })

  it('separates fake runtime, provider configuration and absent workers', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<OperationsPage data={operations} loading={false} failed={false} />) })

    expect(container.textContent).toContain('fake')
    expect(container.textContent).toContain('供应商配置')
    expect(container.textContent).toContain('在线探测尚未验证')
    expect(container.textContent).toContain('API 与研究通道1/3')
    expect(container.textContent).toContain('尚无心跳记录')
    expect(container.textContent).not.toContain('Realtime worker')
    expect(container.textContent).not.toContain('Evolution worker')
    expect(container.textContent).not.toContain('Provider Ready')
    expect(container.textContent).not.toContain('{"')
    await act(async () => { root.unmount() })
  })

  it('includes auxiliary services, distinguishes configured sources from success, and routes to exact source history', async () => {
    const source = { source_id: 'bls-calendar', status: 'degraded', enabled: true, cursor: null, last_success_at: null, next_poll_at: null, consecutive_failures: 5, error_code: 'source_access_denied' }
    const data: OperationsOverview = { ...operations, services: [...operations.services, { ...operations.services[0], service_id: 'hub-research-priority-worker', role: 'research_worker' }, { ...operations.services[0], service_id: 'hub-documents', role: 'documents_worker' }], sources: [source, { ...source, source_id: 'fed-calendar', status: 'healthy', consecutive_failures: 0, error_code: null }] }
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container), navigate = vi.fn(), refresh = vi.fn()
    await act(async () => root.render(<OperationsPage data={data} loading={false} failed={false} onNavigate={navigate} onRefresh={refresh} back="#/Inbox?kind=baseline&offset=20" />))
    expect(container.textContent).toContain('用户研究通道')
    expect(container.textContent).toContain('资料处理')
    expect(container.textContent).toContain('来源拒绝访问 · 下次计划 暂无记录')
    expect(container.textContent).toContain('来源与事件调度')
    expect(container.textContent).toContain('来源最近请求成功1/2')
    expect(container.textContent).toContain('已启用 2 个来源')
    expect(container.textContent).toContain('工作台能力目录暂无登记，不能据此判断研究工具不可用')
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#/Inbox?kind=baseline&offset=20')
    await act(async () => [...container!.querySelectorAll('button')].find(button => button.textContent === '核对采集记录')!.click())
    const auditBack = `#/Operations?${new URLSearchParams({ back: '#/Inbox?kind=baseline&offset=20' })}`
    expect(navigate).toHaveBeenCalledWith('sources', { source: 'bls-calendar', back: auditBack })
    await act(async () => [...container!.querySelectorAll('button')].find(button => button.textContent === '查看研究执行记录')!.click())
    expect(navigate).toHaveBeenCalledWith('runs', { back: auditBack })
    await act(async () => [...container!.querySelectorAll('button')].find(button => button.textContent === '刷新运行状态')!.click())
    expect(refresh).toHaveBeenCalledOnce()
    await act(async () => root.render(<OperationsPage data={data} loading={false} failed={true} />))
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('不能确认当前运行状态')
    expect(container.querySelector('.ops-metrics')?.textContent).toContain('API 与研究通道未确认')
    expect(container.querySelector('.status-mark.success')).toBeNull()
    await act(async () => root.unmount())
  })

  it('does not report a complete research path when a required channel is missing or stale', () => {
    const api = operations.services[0]
    const primary = { ...api, service_id: 'hub-research-worker' }
    const priority = { ...api, service_id: 'hub-research-priority-worker' }
    expect(researchServiceHealth([api])).toEqual({ online: 1, expected: 3, complete: false })
    expect(researchServiceHealth([api, primary])).toEqual({ online: 2, expected: 3, complete: false })
    expect(researchServiceHealth([api, primary, { ...priority, status: 'stale' }]).complete).toBe(false)
    expect(researchServiceHealth([api, primary, priority]).complete).toBe(true)
  })

  it('shows an offline evolution process without claiming that there is no feedback', async () => {
    container = document.createElement('div'); document.body.append(container)
    const root = createRoot(container)
    await act(async () => root.render(<><EvolutionExecutionStatus data={operations} loading={false} failed={false} /><EvolutionJobQueue jobs={[]} loading={false} failed={false} /></>))
    expect(container.textContent).toContain('演进进程当前离线')
    expect(container.textContent).toContain('没有任务不代表没有反馈')
    expect(container.textContent).not.toContain('尚无可执行演进输入')
    await act(async () => root.render(<EvolutionExecutionStatus data={operations} loading={false} failed={true} />))
    expect(container.textContent).toContain('状态未确认')
    expect(container.textContent).not.toContain('当前离线')
    await act(async () => root.unmount())
  })

  it('shows durable job trigger, current stage and owner review state', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    await act(async () => { root.render(<EvolutionJobQueue jobs={[job]} loading={false} failed={false} />) })

    expect(container.textContent).toContain('用户反馈')
    expect(container.textContent).toContain('待负责人审核')
    expect(container.textContent).toContain('负责人审核')
    expect(container.textContent).toContain('candidate-1')
    await act(async () => { root.unmount() })
  })
})
