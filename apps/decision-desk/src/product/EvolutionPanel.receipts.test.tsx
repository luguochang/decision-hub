// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EvolutionPanel } from '../App'
import { api } from '../api/client'
import type { EvolutionOverview, PromotionDecisionCommand } from '../api/types'
import { listPendingRequests } from '../research/pendingRequests'
import { confirmEvolutionReceipt, evolutionPendingDecisions, freezeEvolutionDecision, originalEvolutionCommand, rememberEvolutionRefusal, evolutionRefusal } from './evolutionDecisionReceipts'

vi.mock('../api/client', () => ({ api: { promotionDecisionReceipt: vi.fn() }, request: vi.fn() }))
;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const overview: EvolutionOverview = {
  datasets: [], experiments: [], results: [], failures: [], experiences: [], decisions: [], pointers: [],
  candidates: ['one', 'two'].map((id) => ({ candidate_id: id, execution_scope: 'directional_synthesis',
    candidate_type: 'strategy', content_hash: 'a'.repeat(64), version: `${id}.v1`, parent_version: null,
    status: 'candidate', created_at: '2026-10-01T00:00:00Z', content_ref: null, source: 'owner' })),
}
const frozen = { domain_pack_ref: 'crypto_macro.v1', candidate_version: 'one.v1', command: {
  candidate_id: 'one', owner: 'owner', decision: 'reject' as const, reason: '原拒绝理由',
  evaluation_refs: ['eval-one'], expected_generation: 0,
} }
function receipt(command: PromotionDecisionCommand, change = {}) {
  return { decision: { decision_id: 'decision-one', domain_pack_ref: 'crypto_macro.v1',
    request_id: command.request_id, candidate_id: command.candidate_id, owner: command.owner,
    decision: command.decision, reason: command.reason, evaluation_refs: command.evaluation_refs,
    previous_candidate_id: null, resulting_candidate_id: null,
    resulting_generation: command.expected_generation + (command.decision === 'reject' ? 0 : 1),
    created_at: '2026-10-01T00:00:00Z', ...change }, active_pointer: null }
}
let container: HTMLDivElement
let root: Root | null
beforeEach(() => { sessionStorage.clear(); vi.clearAllMocks(); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { if (root) await act(async () => root!.unmount()); container.remove(); sessionStorage.clear(); vi.restoreAllMocks() })
async function mount(onDecision = vi.fn(async (command: PromotionDecisionCommand) => receipt(command)), value = overview, onReceiptConfirmed = vi.fn()) {
  await act(async () => root!.render(<EvolutionPanel evolution={value} deciding={false} onReview={vi.fn()} onDecision={onDecision} onReceiptConfirmed={onReceiptConfirmed} />))
  return { onDecision, onReceiptConfirmed }
}
function button(text: string) { return [...container.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent === text)! }
async function click(text: string) { await act(async () => button(text).click()) }
async function rejectWithReason() {
  await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="拒绝 one.v1"]')!.click())
  const textarea = container.querySelector('textarea')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, '原拒绝理由')
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    container.querySelector<HTMLInputElement>('.confirm-row input')!.click()
  })
  await click('确认拒绝')
}

describe('Evolution request receipts', () => {
  it('recovers a committed lost response after leaving, with a rejected target and changed pointer, without POST', async () => {
    let committed: unknown
    const send = vi.fn(async (command: PromotionDecisionCommand) => { committed = receipt(command); throw new Error('请求超时，尚未确认结果') })
    await mount(send)
    await rejectWithReason()
    expect(send).toHaveBeenCalledOnce()
    expect(container.textContent).not.toContain('操作未执行')
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
    await act(async () => root!.unmount()); root = createRoot(container)
    const now: EvolutionOverview = { ...overview, candidates: [{ ...overview.candidates[0], status: 'rejected' }, overview.candidates[1]],
      pointers: [{ pointer_id: 'pointer', domain_pack_ref: 'crypto_macro.v1', candidate_id: 'two', generation: 9, updated_at: '2026-10-01T01:00:00Z' }] }
    const restored = await mount(send, now)
    expect(container.textContent).toContain('上次决定的回执待核对')
    expect(send).toHaveBeenCalledOnce()
    expect(api.promotionDecisionReceipt).not.toHaveBeenCalled()
    vi.mocked(api.promotionDecisionReceipt).mockResolvedValueOnce(committed as Awaited<ReturnType<typeof api.promotionDecisionReceipt>>)
    await click('核对回执')
    expect(api.promotionDecisionReceipt).toHaveBeenCalledWith('crypto_macro.v1', send.mock.calls[0][0].request_id)
    expect(send).toHaveBeenCalledOnce()
    expect(listPendingRequests('evolution-decision')).toHaveLength(0)
    expect(restored.onReceiptConfirmed).toHaveBeenCalledOnce()
    expect(restored.onReceiptConfirmed).toHaveBeenCalledWith(committed)
    expect(container.textContent).toContain('拒绝决定已确认')
  })

  it('locks the original payload and retries the same key only after a user action and an exact 404', async () => {
    const send = vi.fn<(command: PromotionDecisionCommand) => Promise<unknown>>()
      .mockRejectedValueOnce(new Error('网络连接中断')).mockImplementationOnce(async command => receipt(command))
    await mount(send); await rejectWithReason()
    const original = send.mock.calls[0][0]
    expect(container.querySelector<HTMLTextAreaElement>('textarea')!.readOnly).toBe(true)
    expect(container.querySelector<HTMLButtonElement>('[aria-label="拒绝 two.v1"]')!.disabled).toBe(true)
    expect(container.querySelector<HTMLButtonElement>('[aria-label="拒绝 one.v1"]')!.disabled).toBe(true)
    vi.mocked(api.promotionDecisionReceipt).mockRejectedValueOnce(new Error('promotion_decision_not_found'))
    await click('重试原决定')
    expect(send).toHaveBeenCalledTimes(2)
    expect(send.mock.calls[1][0]).toEqual(original)
    expect(listPendingRequests('evolution-decision')).toHaveLength(0)
    expect(container.querySelector('[aria-label="拒绝 one.v1"]')).toBeNull()
    expect(container.querySelector<HTMLButtonElement>('[aria-label="拒绝 two.v1"]')!.disabled).toBe(false)
  })

  it.each(['promotion_request_reused', 'owner_identity_mismatch', 'API 503'])('does not release or auto POST for %s and absent receipt', async error => {
    const send = vi.fn(async () => { throw new Error(error) })
    await mount(send); await rejectWithReason()
    await click('关闭并保留原决定')
    vi.mocked(api.promotionDecisionReceipt).mockRejectedValueOnce(new Error('promotion_decision_not_found'))
    await click('核对回执')
    expect(button('后台拒绝已核对，关闭原请求')).toBeUndefined()
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
    expect(send).toHaveBeenCalledOnce()
  })

  it('allows a definite application refusal to be closed only explicitly after two precise GET checks', async () => {
    const item = freezeEvolutionDecision({ ...frozen, command: { ...frozen.command, decision: 'promote' } })
    rememberEvolutionRefusal(item, new Error('promotion_gate_failed'))
    const send = vi.fn()
    await mount(send)
    expect(button('后台拒绝已核对，关闭原请求')).toBeUndefined()
    vi.mocked(api.promotionDecisionReceipt).mockRejectedValue(new Error('promotion_decision_not_found'))
    await click('核对回执')
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
    await click('后台拒绝已核对，关闭原请求')
    expect(api.promotionDecisionReceipt).toHaveBeenCalledTimes(2)
    expect(listPendingRequests('evolution-decision')).toHaveLength(0)
    expect(evolutionRefusal(item)).toBe('promotion_gate_failed')
    expect(container.querySelector<HTMLButtonElement>('[aria-label="拒绝 two.v1"]')!.disabled).toBe(false)
    expect(send).not.toHaveBeenCalled()
  })

  it('recognizes actual success even after an apparent application refusal and later status changes', async () => {
    const item = freezeEvolutionDecision(frozen)
    rememberEvolutionRefusal(item, new Error('candidate_not_rejectable'))
    await mount(vi.fn(), { ...overview, candidates: [{ ...overview.candidates[0], status: 'rejected' }] })
    vi.mocked(api.promotionDecisionReceipt).mockResolvedValueOnce(receipt(originalEvolutionCommand(item)))
    await click('核对回执')
    expect(container.textContent).toContain('拒绝决定已确认')
    expect(container.textContent).not.toContain('已关闭原请求')
    expect(listPendingRequests('evolution-decision')).toHaveLength(0)
  })

  it('does not clear a refused request when the final read fails or returns a mismatching receipt', async () => {
    const item = freezeEvolutionDecision(frozen)
    rememberEvolutionRefusal(item, new Error('candidate_not_rejectable'))
    await mount()
    vi.mocked(api.promotionDecisionReceipt).mockRejectedValueOnce(new Error('promotion_decision_not_found')).mockRejectedValueOnce(new Error('API 503'))
    await click('核对回执'); await click('后台拒绝已核对，关闭原请求')
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
    vi.mocked(api.promotionDecisionReceipt).mockResolvedValueOnce(receipt(originalEvolutionCommand(item), { candidate_id: 'two' }))
    await click('后台拒绝已核对，关闭原请求')
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
  })

  it('stops a second POST while the first is unresolved', async () => {
    let resolve!: (value: unknown) => void
    const send = vi.fn((_command: PromotionDecisionCommand) => new Promise(done => { resolve = done }))
    await mount(send); await rejectWithReason()
    expect(container.querySelector<HTMLButtonElement>('[aria-label="拒绝 two.v1"]')!.disabled).toBe(true)
    const command = send.mock.calls[0][0] as PromotionDecisionCommand
    await act(async () => resolve(receipt(command)))
    expect(send).toHaveBeenCalledOnce()
  })

  it('fails closed on damaged stored payload instead of creating a new key', async () => {
    freezeEvolutionDecision(frozen)
    const key = Object.keys(sessionStorage).find(key => key.endsWith(':evolution-decision'))!
    const rows = JSON.parse(sessionStorage.getItem(key)!)
    rows[0].payload.command.candidate_id = 'two'
    sessionStorage.setItem(key, JSON.stringify(rows))
    const { onDecision } = await mount()
    expect(container.querySelector<HTMLButtonElement>('[aria-label="拒绝 two.v1"]')!.disabled).toBe(true)
    expect(container.textContent).toContain('无法读取')
    expect(onDecision).not.toHaveBeenCalled()
  })

  it.each([false, true])('does not derive rollback eligibility from a reject receipt (pointer exists: %s)', async hasPointer => {
    const decision = receipt({ ...frozen.command, request_id: 'old-reject' }).decision
    const value: EvolutionOverview = { ...overview, candidates: [{ ...overview.candidates[0], status: 'rejected' }, overview.candidates[1]], decisions: [decision],
      pointers: hasPointer ? [{ pointer_id: 'pointer', domain_pack_ref: 'crypto_macro.v1', candidate_id: 'two', generation: 1, updated_at: '2026-10-01T01:00:00Z' }] : [] }
    const { onDecision } = await mount(undefined, value)
    expect(container.querySelector('[aria-label="回滚至 one.v1"]')).toBeNull()
    expect(onDecision).not.toHaveBeenCalled()
  })

  it('does not display rollback with no current pointer even when genuine promotion history exists', async () => {
    const decision = receipt({ ...frozen.command, decision: 'promote', request_id: 'old-promote' }).decision
    await mount(undefined, { ...overview, decisions: [decision], pointers: [] })
    expect(container.querySelector('[aria-label="回滚至 one.v1"]')).toBeNull()
  })

  it.each(['promote', 'rollback'] as const)('retains rollback for the previous candidate from verified %s history with a pointer', async action => {
    const decision = receipt({ ...frozen.command, candidate_id: 'two', decision: action, request_id: `old-${action}` }, { previous_candidate_id: 'one', resulting_candidate_id: 'two' }).decision
    const value: EvolutionOverview = { ...overview, decisions: [decision],
      pointers: [{ pointer_id: 'pointer', domain_pack_ref: 'crypto_macro.v1', candidate_id: 'two', generation: 1, updated_at: '2026-10-01T01:00:00Z' }] }
    await mount(undefined, value)
    expect(container.querySelector<HTMLButtonElement>('[aria-label="回滚至 one.v1"]')?.disabled).toBe(false)
    expect(container.querySelector('[aria-label="回滚至 two.v1"]')).toBeNull()
  })

  it('keeps successful acknowledgement truthful when annotation or journal cleanup throws', async () => {
    const item = freezeEvolutionDecision(frozen)
    await mount()
    vi.mocked(api.promotionDecisionReceipt).mockResolvedValueOnce(receipt(originalEvolutionCommand(item)))
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('storage locked') })
    await click('核对回执')
    expect(container.textContent).toContain('拒绝决定已确认')
    expect(container.textContent).toContain('浏览器凭据尚未清理')
    expect(container.textContent).not.toContain('决定结果尚未确认')
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
  })

  it('does not claim a refused request closed when local journal removal fails', async () => {
    const item = freezeEvolutionDecision(frozen)
    rememberEvolutionRefusal(item, new Error('candidate_not_rejectable'))
    await mount()
    vi.mocked(api.promotionDecisionReceipt).mockRejectedValue(new Error('promotion_decision_not_found'))
    await click('核对回执')
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('storage locked') })
    await click('后台拒绝已核对，关闭原请求')
    expect(container.textContent).toContain('原请求仍保留')
    expect(container.textContent).not.toContain('并关闭原请求')
    expect(listPendingRequests('evolution-decision')).toHaveLength(1)
    expect(container.querySelector<HTMLButtonElement>('[aria-label="拒绝 two.v1"]')!.disabled).toBe(true)
  })

  it.each(['request_id', 'domain_pack_ref', 'candidate_id', 'owner', 'decision', 'reason', 'evaluation_refs', 'resulting_generation'])('rejects a receipt with a different %s', field => {
    const item = freezeEvolutionDecision(frozen)
    const value = field === 'evaluation_refs' ? ['different'] : field === 'resulting_generation' ? 9 : field === 'decision' ? 'promote' : 'different'
    expect(() => confirmEvolutionReceipt(item, receipt(originalEvolutionCommand(item), { [field]: value }))).toThrow('不一致')
    expect(evolutionPendingDecisions('crypto_macro.v1', 'owner')).toHaveLength(1)
  })
})
