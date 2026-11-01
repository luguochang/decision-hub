// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { delivery, productOwner } from '../api/delivery'
import { listPendingRequests, pendingRequest } from '../research/pendingRequests'
import { RevisionFeedback } from './DeliveryPage'
import { revisionFeedbackEvaluationSchema } from '@decision-hub/contracts-ts'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const stamp = '2026-10-01T01:00:00Z'
const payload = { revision: 'revision-1', verdict: 'needs_review' as const, notes: '核对原始公告', owner: productOwner }
let root: Root, container: HTMLDivElement, client: QueryClient
beforeEach(() => {
  sessionStorage.clear()
  vi.spyOn(delivery, 'latestFeedbackEvaluation').mockResolvedValue(null)
  vi.spyOn(delivery, 'feedbackEvaluation').mockRejectedValue(new Error('测试未提供当时评估'))
  vi.spyOn(delivery, 'feedbackEvaluationBasis').mockRejectedValue(new Error('测试未提供原依据'))
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); client.clear(); vi.restoreAllMocks(); sessionStorage.clear() })
async function mount(revision = 'revision-1', saved?: Parameters<typeof RevisionFeedback>[0]['saved']) {
  await act(async () => root.render(<QueryClientProvider client={client}><RevisionFeedback revision={revision} saved={saved} /></QueryClientProvider>))
  await settle()
}
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 15)) }) }
const button = (text: string) => [...container.querySelectorAll('button')].find(item => item.textContent === text)!
const received = (args: [string, string, string, string]) => ({ feedback_id: 'feedback-1', request_id: args[3], target_type: 'report_revision' as const, target_id: args[0], created_by: productOwner, verdict: args[1] as typeof payload.verdict, notes: args[2], created_at: stamp })
async function submitDraft() {
  await act(async () => button('反馈这一版').click())
  container.querySelector('textarea')!.value = payload.notes
  container.querySelector('select')!.value = payload.verdict
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  await settle()
}

it('persists before dispatch, locks a lost response and restores without automatically posting', async () => {
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(async (...args) => {
    expect(listPendingRequests('report-feedback')).toMatchObject([{ id: args[3], payload }])
    if (save.mock.calls.length === 1) throw new Error('响应丢失')
    return received(args)
  })
  await mount(); await submitDraft()
  expect(container.textContent).toContain('保存未确认')
  expect(container.querySelector('textarea')!.disabled).toBe(true)
  expect(container.querySelector('select')!.disabled).toBe(true)
  const original = save.mock.calls[0]
  await act(async () => root.render(<QueryClientProvider client={client}><div>已离页</div></QueryClientProvider>))
  await mount()
  expect(save).toHaveBeenCalledTimes(1)
  expect(container.querySelector('textarea')!.value).toBe(payload.notes)
  expect(container.querySelector('select')!.value).toBe(payload.verdict)
  expect(container.textContent).toContain('返回此页不会自动提交')
  await act(async () => button('核对原请求回执').click()); await settle()
  expect(save.mock.calls[1]).toEqual(original)
  expect(container.textContent).toContain('已反馈：需要核查')
  expect(listPendingRequests('report-feedback')).toEqual([])
})

it('ignores attempted content changes while unconfirmed and leaves unrelated receipts pending', async () => {
  const record = pendingRequest('report-feedback', payload)
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(async (...args) => ({ ...received(args), target_id: 'revision-other' }))
  await mount()
  container.querySelector('textarea')!.value = '篡改内容'
  container.querySelector('select')!.value = 'incorrect'
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); await settle()
  expect(save).toHaveBeenCalledWith(payload.revision, payload.verdict, payload.notes, record.id)
  expect(container.textContent).toContain('反馈回执与原请求不一致')
  expect(container.querySelector('[role="alert"] > p:last-child')?.textContent).toContain('反馈回执与原请求不一致')
  expect(container.textContent).not.toContain('已反馈：')
  expect(listPendingRequests('report-feedback')[0].id).toBe(record.id)
})

it('does not clear pending identity merely because the list has a saved feedback with matching content', async () => {
  const record = pendingRequest('report-feedback', payload)
  const save = vi.spyOn(delivery, 'feedback')
  await mount('revision-1', { feedback_id: 'feedback-1', verdict: payload.verdict, notes: payload.notes, created_at: stamp })
  expect(container.textContent).toContain('仍需核对原请求回执')
  expect(button('核对原请求回执')).toBeDefined()
  expect(save).not.toHaveBeenCalled()
  expect(listPendingRequests('report-feedback')[0].id).toBe(record.id)
})

it('keeps a delayed old-version receipt exact without overwriting latest cached feedback', async () => {
  let finish!: (value: Awaited<ReturnType<typeof delivery.feedback>>) => void
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  client.setQueryData(['product-reports', 0, false], { items: [{ revision_id: 'revision-1', latest_feedback: null }, { revision_id: 'revision-2', latest_feedback: null }], total: 2, limit: 12, offset: 0 })
  await mount(); await submitDraft()
  const original = save.mock.calls[0]
  await mount('revision-2')
  expect(container.querySelector('form')).toBeNull()
  await act(async () => finish(received(original))); await settle()
  expect(container.textContent).not.toContain('已反馈：')
  expect(container.textContent).not.toContain(payload.notes)
  expect(client.getQueryData(['product-reports', 0, false])).toMatchObject({ items: [{ revision_id: 'revision-1', latest_feedback: null }, { revision_id: 'revision-2', latest_feedback: null }] })
  expect(client.getQueryData(['revision-feedback-receipt', productOwner, 'revision-1', original[3]])).toMatchObject({ notes: payload.notes, request_id: original[3] })
  expect(listPendingRequests('report-feedback')).toEqual([])
})

it('blocks posting if persistence is unavailable and permits an explicit retry after storage returns', async () => {
  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('QuotaExceededError') })
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(async (...args) => received(args))
  await mount(); await submitDraft()
  expect(container.textContent).toContain('尚未发送请求')
  expect(save).not.toHaveBeenCalled()
  expect(button('保存反馈').disabled).toBe(true)
  write.mockRestore()
  await act(async () => button('重新读取反馈凭据').click())
  expect(save).not.toHaveBeenCalled()
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); await settle()
  expect(save).toHaveBeenCalledTimes(1)
  expect(container.textContent).toContain('已反馈：')
})

it.each(['bad-json', 'different-owner', 'altered-signature', 'multiple-pending'])('preserves and blocks an unverifiable journal: %s', async problem => {
  pendingRequest('report-feedback', payload)
  const key = sessionStorage.key(0)!
  if (problem === 'bad-json') sessionStorage.setItem(key, '{bad json')
  else if (problem === 'multiple-pending') pendingRequest('report-feedback', { ...payload, notes: '另一次未确认提交' })
  else {
    const rows = JSON.parse(sessionStorage.getItem(key)!)
    if (problem === 'different-owner') { rows[0].payload.owner = 'another'; rows[0].signature = JSON.stringify(rows[0].payload) }
    else rows[0].payload.notes = '不匹配的内容'
    sessionStorage.setItem(key, JSON.stringify(rows))
  }
  const journal = sessionStorage.getItem(key)
  const save = vi.spyOn(delivery, 'feedback')
  await mount()
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  await act(async () => button('反馈这一版').click())
  expect(button('保存反馈').disabled).toBe(true)
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(save).not.toHaveBeenCalled()
  expect(sessionStorage.getItem(key)).toBe(journal)
})

it('reuses a stored key after cleanup fails and does not post when the feedback form is reopened', async () => {
  pendingRequest('report-feedback', payload)
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(async (...args) => received(args))
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new DOMException('SecurityError') })
  await mount()
  await act(async () => button('核对原请求回执').click()); await settle()
  expect(container.textContent).toContain('已反馈：')
  const original = save.mock.calls[0]
  await act(async () => root.render(<QueryClientProvider client={client}><div /></QueryClientProvider>))
  await mount()
  expect(save).toHaveBeenCalledTimes(1)
  await act(async () => button('核对原请求回执').click()); await settle()
  expect(save.mock.calls[1]).toEqual(original)
})

it('does not retry automatically even when the query client enables mutation retries', async () => {
  client.setDefaultOptions({ mutations: { retry: 2, retryDelay: 0 } })
  const save = vi.spyOn(delivery, 'feedback').mockRejectedValue(new Error('响应未确认'))
  await mount(); await submitDraft(); await settle()
  expect(save).toHaveBeenCalledTimes(1)
  expect(listPendingRequests('report-feedback')).toHaveLength(1)
})

it('submits only once when the form is submitted twice before React rerenders', async () => {
  let finish!: (value: Awaited<ReturnType<typeof delivery.feedback>>) => void
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  await mount()
  await act(async () => button('反馈这一版').click())
  container.querySelector('textarea')!.value = payload.notes
  await act(async () => {
    const form = container.querySelector('form')!
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  })
  expect(save).toHaveBeenCalledTimes(1)
  expect(listPendingRequests('report-feedback')).toHaveLength(1)
  await act(async () => finish(received(save.mock.calls[0]))); await settle()
  expect(listPendingRequests('report-feedback')).toEqual([])
})

it.each(['pc_probe_response_withheld_after_commit', 'API 503\nOriginal transport response unavailable'])('keeps the actual technical error closed until the user expands it: %s', async message => {
  const save = vi.spyOn(delivery, 'feedback').mockRejectedValue(new Error(message))
  await mount(); await submitDraft()
  const alert = container.querySelector('[role="alert"]')!
  expect(alert.querySelector(':scope > p')?.textContent).toBe('保存未确认，请核对原请求回执。')
  expect(alert.querySelector(':scope > p')?.textContent).not.toContain(message)
  const original = alert.querySelector('details')!
  expect(original.open).toBe(false)
  expect(original.querySelector('pre')?.textContent).toBe(message)
  expect(container.querySelector('textarea')!.disabled).toBe(true)
  expect(listPendingRequests('report-feedback')).toHaveLength(1)
  await act(async () => original.querySelector('summary')!.click())
  expect(original.open).toBe(true)
  expect(original.querySelector('pre')?.textContent).toBe(message)
  expect(save).toHaveBeenCalledTimes(1)
})

function storedEvaluation(opinion = received(['revision-1', payload.verdict, payload.notes, 'request-1']), available = true) {
  const basisHash = 'a'.repeat(64)
  return revisionFeedbackEvaluationSchema.parse({
    schema_version: 'research-revision-feedback-evaluation.v1', owner_id: productOwner,
    revision_id: opinion.target_id, feedback: opinion, status: available ? 'available' : 'unavailable',
    reason: available ? null : 'source_invalid', confirmation_basis: 'stored_feedback_evaluation_binding',
    binding: {
      binding_id: 'binding-1', recorded_order: 1, source_run_id: 'run-1', artifact_id: 'artifact-1',
      evaluation_version: `research-value.revision-feedback.v1:${basisHash}`, basis_ref: 'basis-1',
      basis_hash: basisHash, feedback_payload_hash: 'b'.repeat(64), value_payload_hash: available ? 'd'.repeat(64) : null,
      binding_hash: 'c'.repeat(64), recorded_at: stamp, validation_issues: available ? [] : ['source_invalid'], source_witnesses: [],
    },
    value: available ? {
      schema_version: 'research-value-evaluation.v1', evaluation_id: 'value-1', run_id: 'run-1', artifact_id: 'artifact-1',
      evaluation_version: `research-value.revision-feedback.v1:${basisHash}`, mode: 'research_only',
      hard_coverage_ratio: null, soft_coverage_ratio: null, accepted_evidence_count: 1, rejected_evidence_count: 0,
      typed_fact_count: 0, baseline_status: 'not_applicable', latency_ms: null, cost_status: 'unknown', known_cost_usd: null,
      citation_traceability: 1, usefulness: 'partial', terminal_reason: 'critical_data_unavailable', outcomes: [], evaluated_at: stamp,
    } : null,
  })
}

it('labels frozen no-trade outcomes as publication records without replacing the original research directions', async () => {
  const receipt = storedEvaluation()
  receipt.value!.outcomes = (['30m', '24h', '72h'] as const).map(horizon => ({
    forecast_id: `forecast-${horizon}`, horizon, direction: 'no_trade',
    quality_status: 'unavailable', return_pct: null, net_return_pct: null, brier_score: null,
    direction_correct: null, mfe_pct: null, mae_pct: null,
    unavailable_reason: 'direction_not_applicable', observed_at: null,
  }))
  const original = JSON.stringify(receipt), save = vi.spyOn(delivery, 'feedback')
  vi.mocked(delivery.latestFeedbackEvaluation).mockResolvedValue(receipt)
  await mount()
  await act(async () => [...container.querySelectorAll('summary')].find(item => item.textContent === '查看当时评估依据')!.click())
  const evaluation = container.querySelector('[aria-label="这笔反馈的当时评估"]')!
  expect(evaluation.textContent).toContain('发布预测账本')
  expect(evaluation.querySelectorAll('strong')).toHaveLength(3)
  expect([...evaluation.querySelectorAll('strong')].every(item => item.textContent?.includes('未授权交易的发布记录'))).toBe(true)
  expect(evaluation.textContent).not.toContain('未形成方向')
  expect(evaluation.textContent).not.toContain('偏多')
  expect(evaluation.textContent).toContain('独立研究方向和到期观察请查看已保存报告与后续观察')
  const report = [...evaluation.querySelectorAll('a')].find(item => item.textContent === '查看已保存报告')!
  const observation = [...evaluation.querySelectorAll('a')].find(item => item.textContent === '查看后续观察')!
  expect(new URLSearchParams(report.getAttribute('href')!.split('?')[1]).get('run')).toBe('run-1')
  expect(new URLSearchParams(observation.getAttribute('href')!.split('?')[1]).get('run')).toBe('run-1')
  expect(JSON.stringify(receipt)).toBe(original); expect(save).not.toHaveBeenCalled()
})

it('keeps an acknowledged opinion successful when the exact evaluation read fails', async () => {
  vi.spyOn(delivery, 'feedback').mockImplementation(async (...args) => received(args))
  await mount(); await submitDraft(); await settle()
  expect(container.textContent).toContain('已反馈：需要核查')
  expect(container.textContent).toContain('反馈已保存，当时评估暂时无法读取')
  expect(container.textContent).not.toContain('保存未确认，请核对原请求回执')
  expect(listPendingRequests('report-feedback')).toEqual([])
})

it('shows old feedback as not recorded and never requests a made-up basis', async () => {
  const opinion = received(['revision-1', payload.verdict, payload.notes, 'legacy-key'])
  vi.mocked(delivery.latestFeedbackEvaluation).mockResolvedValue(revisionFeedbackEvaluationSchema.parse({
    schema_version: 'research-revision-feedback-evaluation.v1', owner_id: productOwner, revision_id: 'revision-1',
    feedback: opinion, status: 'unavailable', reason: 'legacy_feedback_not_bound',
    confirmation_basis: 'legacy_feedback_receipt_only', binding: null, value: null,
  }))
  const save = vi.spyOn(delivery, 'feedback')
  await mount()
  expect(container.textContent).toContain('已反馈：需要核查')
  expect(container.textContent).toContain('未记录当时评估')
  expect(container.querySelector('pre')).toBeNull()
  expect(delivery.feedbackEvaluationBasis).not.toHaveBeenCalled()
  expect(save).not.toHaveBeenCalled()
})

it('shows a newer unavailable binding rather than an older available score', async () => {
  const old = storedEvaluation()
  client.setQueryData(['revision-feedback-evaluation', productOwner, 'revision-1', old.feedback.request_id, undefined], old)
  const latest = storedEvaluation(received(['revision-1', 'incorrect', '后来发现原文错误', 'new-key']), false)
  vi.mocked(delivery.latestFeedbackEvaluation).mockResolvedValue(latest)
  await mount()
  expect(container.textContent).toContain('已反馈：发现错误')
  expect(container.textContent).toContain('当时评估暂不可用：原评估材料无法核验')
  expect(container.textContent).not.toContain('当时评估：部分有帮助')
})

it('does not overwrite later same-version feedback when an older ACK returns', async () => {
  let finish!: (value: Awaited<ReturnType<typeof delivery.feedback>>) => void
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  const later = storedEvaluation(received(['revision-1', 'incorrect', '后来保存的意见', 'later-key']), false)
  const list = { items: [{ revision_id: 'revision-1', latest_feedback: { feedback_id: later.feedback.feedback_id, notes: later.feedback.notes } }] }
  client.setQueryData(['product-reports', productOwner, 0, false], list)
  await mount(); await submitDraft()
  vi.mocked(delivery.latestFeedbackEvaluation).mockResolvedValue(later)
  vi.mocked(delivery.feedbackEvaluation).mockImplementation(async value => storedEvaluation(received([value.revision, value.verdict, value.notes, value.id])))
  await act(async () => finish(received(save.mock.calls[0]))); await settle()
  expect(client.getQueryData(['product-reports', productOwner, 0, false])).toEqual(list)
  expect(container.textContent).toContain('当时评估：部分有帮助')
  expect(container.textContent).toContain('查看这一版的最新反馈')
  expect(container.textContent).toContain('后来保存的意见')
  expect(save).toHaveBeenCalledTimes(1)
})

it('fetches complete original basis only after explicit expansion and keeps raw text closed', async () => {
  const receipt = storedEvaluation()
  vi.mocked(delivery.latestFeedbackEvaluation).mockResolvedValue(receipt)
  vi.mocked(delivery.feedbackEvaluationBasis).mockResolvedValue({
    schema_version: 'research-revision-feedback-evaluation-basis.v1', owner_id: productOwner,
    revision_id: 'revision-1', request_id: receipt.feedback.request_id, binding_id: 'binding-1',
    basis_ref: 'basis-1', basis_hash: 'a'.repeat(64), basis_json: '{"original":"原依据"}',
  })
  await mount()
  expect(delivery.feedbackEvaluationBasis).not.toHaveBeenCalled()
  expect(container.textContent).not.toContain('basis_hash')
  await act(async () => [...container.querySelectorAll('summary')].find(item => item.textContent === '查看当时评估依据')!.click())
  await vi.waitFor(async () => {
    await settle()
    expect([...container.querySelectorAll('details')].find(item => item.querySelector(':scope > summary')?.textContent === '核对完整原依据')).toBeDefined()
  }, { timeout: 1000, interval: 20 })
  expect(delivery.feedbackEvaluationBasis).toHaveBeenCalledTimes(1)
  const original = [...container.querySelectorAll('details')].find(item => item.querySelector(':scope > summary')?.textContent === '核对完整原依据')!
  expect(original.open).toBe(false)
  expect(original.querySelector('pre')?.textContent).toBe('{"original":"原依据"}')
  expect(container.textContent).toContain('缺标签不会算作未命中')
})

it.each(['pending', 'journal-error'])('opens recovery after a confirmed feedback encounters an external %s', async problem => {
  const save = vi.spyOn(delivery, 'feedback').mockImplementation(async (...args) => received(args))
  await mount(); await submitDraft(); await settle()
  const original = save.mock.calls[0]
  expect(container.textContent).toContain('已反馈：需要核查')
  const next = pendingRequest('report-feedback', { ...payload, notes: '新的原意图' })
  if (problem === 'journal-error') sessionStorage.setItem(sessionStorage.key(0)!, '{broken journal')
  const stored = sessionStorage.getItem(sessionStorage.key(0)!)
  await act(async () => button('补充反馈').click()); await settle()
  expect(container.textContent).toContain('查看已确认的原反馈')
  expect(container.textContent).toContain(payload.notes)
  expect(save).toHaveBeenCalledTimes(1)
  expect(sessionStorage.getItem(sessionStorage.key(0)!)).toBe(stored)
  expect(client.getQueryData(['revision-feedback-receipt', productOwner, 'revision-1', original[3]])).toMatchObject({ notes: payload.notes })
  if (problem === 'pending') {
    expect(container.querySelector('textarea')!.value).toBe('新的原意图')
    expect(button('核对原请求回执')).toBeDefined()
    expect(listPendingRequests('report-feedback')[0].id).toBe(next.id)
    await act(async () => button('核对原请求回执').click()); await settle()
    expect(save.mock.calls[1]).toEqual(['revision-1', payload.verdict, '新的原意图', next.id])
  } else {
    expect(button('重新读取反馈凭据')).toBeDefined()
    expect(button('保存反馈').disabled).toBe(true)
  }
})

it('labels a failed refresh while retaining the original evaluated opinion without posting', async () => {
  const receipt = storedEvaluation()
  vi.mocked(delivery.latestFeedbackEvaluation).mockResolvedValueOnce(receipt).mockRejectedValue(new Error('lookup failed'))
  const save = vi.spyOn(delivery, 'feedback')
  await mount()
  await act(async () => { await client.invalidateQueries({ queryKey: ['revision-feedback-latest', productOwner, 'revision-1'] }) }); await settle()
  expect(container.textContent).toContain('上次核对的当时评估仍保留，本次读取尚未确认')
  expect(container.textContent).toContain('当时评估：部分有帮助')
  expect(save).not.toHaveBeenCalled()
})
