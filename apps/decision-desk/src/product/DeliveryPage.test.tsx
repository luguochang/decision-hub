// @vitest-environment jsdom
import { createHash, webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { delivery, productOwner, type RevisionReadRequest } from '../api/delivery'
import { assets } from '../api/assets'
import { readCommandState } from '../research/readCommandRequests'
import { DeliveryPage, RevisionFeedback } from './DeliveryPage'
import { StudyTracking } from './StudyTracking'
import { revisionFeedbackEvaluationSchema, type StudyView } from '@decision-hub/contracts-ts'

const stamp = '2026-09-29T12:00:00Z'
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const item = { revision_id: 'revision-1', study_id: 'study-1', source_run_id: 'run-1', title: '美联储声明', instrument: 'BTC-USDT-SWAP', strategy_version: 'research.v1', publication_status: 'research_only', created_at: stamp, local_written_at: stamp, external_dispatch_at: null, read_at: null, latest_feedback: null }
let root: Root, container: HTMLDivElement
beforeEach(() => { sessionStorage.clear(); vi.stubGlobal('crypto', webcrypto); vi.spyOn(delivery, 'latestFeedbackEvaluation').mockResolvedValue(null); vi.spyOn(delivery, 'feedbackEvaluation').mockRejectedValue(new Error('controlled unavailable')); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })
async function mount(element: React.ReactNode, queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })) { await act(async () => root.render(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>)); await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) }) }
function button(text: string) { return [...container.querySelectorAll('button')].find(item => item.textContent === text)! }

function strongReadReceipt(value: RevisionReadRequest) {
  return {
    schema_version: 'read-command-receipt.v1' as const, request_id: value.id, revision_id: value.revision,
    owner_id: value.owner, command: { request_id: value.id, read: value.read },
    result: { revision_id: value.revision, owner_id: value.owner, read_at: value.read ? stamp : null, updated_at: stamp },
    payload_hash: createHash('sha256').update(JSON.stringify([value.owner, value.revision, 'read_receipt', { read: value.read, request_id: value.id }])).digest('hex'),
    confirmation_basis: 'stored_command_hash' as const,
  }
}

describe('version delivery controls', () => {
  it('distinguishes scope lookup failure from an unapproved market and offers a retry', async () => {
    const lookup = vi.spyOn(assets, 'researchMarkets').mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ registry_version: 'test', items: [] })
    await mount(<StudyTracking study={{ study_id: 'btc', status: 'paused', identity_basis: 'occurrence', market: 'crypto', instrument: 'BTC-USDT-SWAP' } as StudyView} />)
    expect(container.textContent).toContain('研究范围读取失败')
    expect(container.textContent).not.toContain('市场未开通')
    expect(button('开启后续自动复查').disabled).toBe(true)
    await act(async () => button('重新核对范围').click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    expect(lookup).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain('市场未开通')
    expect(container.textContent).not.toContain('研究范围读取失败')
  })

  it.each([{ study_id: 'other', status: 'paused' }, { study_id: 'btc', status: 'unknown' }])('refuses an unrelated or invalid tracking receipt %s', async result => {
    vi.spyOn(assets, 'researchMarkets').mockResolvedValue({ registry_version: 'test', items: [] })
    vi.spyOn(delivery, 'tracking').mockResolvedValue({ request_id: 'receipt', previous_status: 'active', cancelled_runs: 0, retired_plans: 0, recorded_at: stamp, ...result })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const study = { study_id: 'btc', status: 'active', identity_basis: 'occurrence', market: 'crypto', instrument: 'BTC-USDT-SWAP' } as StudyView
    client.setQueryData(['asset-study', 'btc', 0], { study })
    await mount(<StudyTracking study={study} />, client)
    await act(async () => button('暂停自动复查').click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    expect(container.textContent).toContain('操作未确认')
    expect(client.getQueryData(['asset-study', 'btc', 0])).toMatchObject({ study: { status: 'active' } })
    client.clear()
  })

  it('applies the saved receipt to all cached topic views and waits for refresh before another action', async () => {
    vi.spyOn(assets, 'researchMarkets').mockResolvedValue({ registry_version: 'test', items: [] })
    const study = { study_id: 'btc', status: 'active', identity_basis: 'occurrence', market: 'crypto', instrument: 'BTC-USDT-SWAP' } as StudyView
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['asset-study', 'btc', 0], { study, total: 0, revisions: [] })
    client.setQueryData(['asset-study', 'other', 0], { study: { ...study, study_id: 'other' } })
    client.setQueryData(['asset-studies', '', 0], { items: [study, { ...study, study_id: 'other' }] })
    let finishRefresh!: () => void
    const refresh = new Promise<void>(resolve => { finishRefresh = resolve })
    vi.spyOn(client, 'invalidateQueries').mockReturnValue(refresh)
    const save = vi.spyOn(delivery, 'tracking').mockResolvedValue({ request_id: 'saved', study_id: 'btc', previous_status: 'active', status: 'paused', cancelled_runs: 0, retired_plans: 0, recorded_at: stamp })
    await mount(<StudyTracking study={study} />, client)
    await act(async () => button('暂停自动复查').click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    expect(button('正在保存并核对…').disabled).toBe(true)
    await act(async () => button('正在保存并核对…').click())
    expect(save).toHaveBeenCalledTimes(1)
    expect(client.getQueryData(['asset-study', 'btc', 0])).toMatchObject({ study: { status: 'paused', updated_at: stamp } })
    expect(client.getQueryData(['asset-study', 'other', 0])).toMatchObject({ study: { status: 'active' } })
    expect(client.getQueryData(['asset-studies', '', 0])).toMatchObject({ items: [{ status: 'paused' }, { status: 'active' }] })
    await act(async () => finishRefresh())
    await mount(<StudyTracking study={{ ...study, status: 'paused' }} />, client)
    expect(container.textContent).toContain('已暂停，停止 0 个自动排队任务')
    expect(container.textContent).toContain('手动任务及正在执行的任务会保留')
    expect(container.textContent).toContain('也不会立即发起新研究')
    client.clear()
  })

  it('does not enable a paused BTC study when backend market admission is disabled', async () => {
    vi.spyOn(assets, 'researchMarkets').mockResolvedValue({ registry_version: 'test', items: [] })
    const tracking = vi.spyOn(delivery, 'tracking')
    await mount(<StudyTracking study={{ study_id: 'btc', market: 'crypto', instrument: 'BTC-USDT-SWAP', identity_basis: 'occurrence', status: 'paused' } as StudyView} />)
    expect(button('开启后续自动复查').disabled).toBe(true)
    expect(tracking).not.toHaveBeenCalled()
  })
  it('loads an exact Run deep link and can clear that filter', async () => {
    const list = vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [item], total: 1, limit: 12, offset: 0 })
    const navigate = vi.fn()
    await mount(<DeliveryPage params={new URLSearchParams({ run: 'run-1' })} onNavigate={navigate} />)
    expect(list).toHaveBeenCalledWith({ offset: 0, unread: false, run: 'run-1' })
    await act(async () => button('阅读这一版报告').click())
    expect(navigate).toHaveBeenCalledWith('report', { run: 'run-1', back: '#/delivery?run=run-1' })
    await act(async () => button('查看主题变化').click())
    expect(navigate).toHaveBeenCalledWith('studies', { study: 'study-1', back: '#/delivery?run=run-1' })
    await act(async () => button('显示全部报告').click())
    expect(navigate).toHaveBeenCalledWith('delivery', {})
  })
  it('shows the saved version feedback after reloading without a new submission form', async () => {
    const save = vi.spyOn(delivery, 'feedback')
    await mount(<RevisionFeedback revision="revision-1" saved={{ feedback_id: 'f1', verdict: 'needs_review', notes: '核对原文', created_at: stamp }} />)
    expect(container.textContent).toContain('已反馈：需要核查')
    expect(container.textContent).toContain('核对原文')
    expect(container.querySelector('form')).toBeNull()
    expect(save).not.toHaveBeenCalled()
  })
  it('reads this owner revision evaluation separately from the report list summary without changing read actions', async () => {
    vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [{ ...item, latest_feedback: { feedback_id: 'old', verdict: 'useful', notes: '较旧列表意见', created_at: stamp } }], total: 1, limit: 12, offset: 0 })
    vi.mocked(delivery.latestFeedbackEvaluation).mockResolvedValue(revisionFeedbackEvaluationSchema.parse({
      schema_version: 'research-revision-feedback-evaluation.v1', owner_id: productOwner, revision_id: item.revision_id,
      feedback: { feedback_id: 'actual', request_id: 'actual-original-key', target_type: 'report_revision', target_id: item.revision_id,
        created_by: productOwner, verdict: 'needs_review', notes: '真实原意见', created_at: stamp },
      status: 'unavailable', reason: 'legacy_feedback_not_bound', confirmation_basis: 'legacy_feedback_receipt_only', binding: null, value: null,
    }))
    const save = vi.spyOn(delivery, 'feedback'), mark = vi.spyOn(delivery, 'read')
    await mount(<DeliveryPage params={new URLSearchParams()} onNavigate={vi.fn()} />)
    expect(delivery.latestFeedbackEvaluation).toHaveBeenCalledWith(item.revision_id, item.source_run_id)
    expect(container.textContent).toContain('已反馈：需要核查')
    expect(container.textContent).toContain('未记录当时评估')
    expect(container.textContent).not.toContain('较旧列表意见')
    expect(button('阅读这一版报告')).toBeDefined()
    expect(button('标记已读')).toBeDefined()
    expect(mark).not.toHaveBeenCalled()
    expect(save).not.toHaveBeenCalled()
  })
  it('opening a report does not mark read; explicit mark preserves the immutable version', async () => {
    const list = vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [item], total: 1, limit: 12, offset: 0 })
    const mark = vi.spyOn(delivery, 'read').mockResolvedValue({ revision_id: item.revision_id, owner_id: productOwner, read_at: stamp, updated_at: stamp })
    vi.spyOn(delivery, 'readCommandReceipt').mockImplementation(async value => strongReadReceipt(value))
    const navigate = vi.fn()
    await mount(<DeliveryPage params={new URLSearchParams()} onNavigate={navigate} />)
    expect(container.textContent).toContain('未标记已读')
    expect(container.textContent).toContain('本地写出')
    expect(mark).not.toHaveBeenCalled()
    await act(async () => button('阅读这一版报告').click())
    expect(navigate).toHaveBeenCalledWith('report', { run: 'run-1', back: '#/delivery' })
    expect(mark).not.toHaveBeenCalled()
    list.mockResolvedValue({ items: [{ ...item, read_at: stamp }], total: 1, limit: 12, offset: 0 })
    await act(async () => button('标记已读').click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    await vi.waitFor(() => { expect(readCommandState().busy).toBe(false); expect(container.textContent).toContain('恢复未读') })
    expect(mark).toHaveBeenCalledWith('revision-1', true, expect.any(String))
    expect(container.textContent).toContain('恢复未读')
  })

  it('returns to the valid unread page after the last item is marked without dropping filters', async () => {
    const list = vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [item], total: 13, limit: 12, offset: 12 })
    vi.spyOn(delivery, 'read').mockResolvedValue({ revision_id: item.revision_id, owner_id: productOwner, read_at: stamp, updated_at: stamp })
    vi.spyOn(delivery, 'readCommandReceipt').mockImplementation(async value => strongReadReceipt(value))
    const navigate = vi.fn()
    await mount(<DeliveryPage params={new URLSearchParams({ unread: '1', run: 'run-1', offset: '12' })} onNavigate={navigate} />)
    expect(navigate).not.toHaveBeenCalled()
    list.mockResolvedValue({ items: [], total: 12, limit: 12, offset: 12 })
    await act(async () => button('标记已读').click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    await vi.waitFor(() => { expect(readCommandState().busy).toBe(false); expect(navigate).toHaveBeenCalledWith('delivery', { unread: '1', run: 'run-1' }) })
    expect(navigate).toHaveBeenCalledWith('delivery', { unread: '1', run: 'run-1' })
    expect(container.textContent).toContain('正在回到有效页')
    expect(container.textContent).not.toContain('当前没有符合筛选的报告')
  })

  it('repairs an obsolete deep page to the last available page', async () => {
    vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [], total: 25, limit: 12, offset: 999 })
    const navigate = vi.fn()
    await mount(<DeliveryPage params={new URLSearchParams({ unread: '1', offset: '999' })} onNavigate={navigate} />)
    expect(navigate).toHaveBeenCalledWith('delivery', { unread: '1', offset: '24' })
  })

  it('keeps the requested page on a read failure and retries the same mark request', async () => {
    vi.spyOn(delivery, 'reports').mockResolvedValue({ items: [item], total: 13, limit: 12, offset: 12 })
    const mark = vi.spyOn(delivery, 'read').mockRejectedValueOnce(new Error('network')).mockResolvedValue({ revision_id: item.revision_id, owner_id: productOwner, read_at: stamp, updated_at: stamp })
    vi.spyOn(delivery, 'readCommandReceipt').mockResolvedValueOnce(null).mockImplementation(async value => strongReadReceipt(value))
    const navigate = vi.fn()
    await mount(<DeliveryPage params={new URLSearchParams({ unread: '1', offset: '12' })} onNavigate={navigate} />)
    await act(async () => button('标记已读').click())
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    expect(container.textContent).toContain('标记未确认')
    expect(navigate).not.toHaveBeenCalled()
    await act(async () => button('核对原标记回执').click())
    await vi.waitFor(() => { expect(mark).toHaveBeenCalledTimes(2); expect(readCommandState().busy).toBe(false); expect(container.textContent).toContain('原阅读请求已确认') })
    expect(mark.mock.calls[0]).toEqual(mark.mock.calls[1])
  })

  it('feedback retry reuses the same request and records only the selected revision', async () => {
    const save = vi.spyOn(delivery, 'feedback').mockRejectedValueOnce(new Error('network')).mockResolvedValue({ feedback_id: 'feedback-1' } as Awaited<ReturnType<typeof delivery.feedback>>)
    await mount(<RevisionFeedback revision="revision-2" />)
    await act(async () => button('反馈这一版').click())
    container.querySelector('textarea')!.value = '缺少政策原文，请复核'
    container.querySelector('select')!.value = 'needs_review'
    await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    expect(container.textContent).toContain('保存未确认')
    await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(save.mock.calls[0]).toEqual(save.mock.calls[1])
    expect(save).toHaveBeenCalledWith('revision-2', 'needs_review', '缺少政策原文，请复核', expect.any(String))
  })

  it('tracking requires a supported scope and sends the expected current status', async () => {
    const tracking = vi.spyOn(delivery, 'tracking').mockRejectedValue(new Error('study_status_changed'))
    await mount(<StudyTracking study={{ study_id: 'study', market: 'crypto', instrument: 'BTC-USDT-SWAP', identity_basis: 'occurrence', status: 'active' } as StudyView} />)
    await act(async () => button('暂停自动复查').click())
    expect(tracking).toHaveBeenCalledWith('study', 'paused', 'active', expect.any(String))
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) })
    expect(container.textContent).toContain('操作未确认')
    expect(container.textContent).not.toContain('已暂停，')
    await act(async () => button('暂停自动复查').click())
    expect(tracking.mock.calls[0]).toEqual(tracking.mock.calls[1])
    await mount(<StudyTracking study={{ study_id: 'legacy', market: 'unknown', instrument: 'unknown', identity_basis: 'legacy_unknown', status: 'paused' } as StudyView} />)
    expect(button('开启后续自动复查').disabled).toBe(true)
  })
})
