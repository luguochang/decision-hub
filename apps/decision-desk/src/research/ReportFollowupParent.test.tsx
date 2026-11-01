// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReviewScheduleView } from '../api/types'
import { ReportFollowup, researchRouteHref } from './ResearchPage'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
const child = 'run_603cc378191447a88cc6094e3fa6a62d'
const parent = 'run_6428046611744160fbc2750273f2d673'
const back = researchRouteHref({ name: 'report', runId: child, back: '#/home' })
let container: HTMLDivElement, root: Root
async function render(props: Partial<Parameters<typeof ReportFollowup>[0]> = {}) {
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  await act(async () => root.render(<ReportFollowup runId={child} parentRunId={parent} admissionOrigin="scheduled_recheck" plans={[]} loading={false} failed={false} refreshing={false} back={back} onRefresh={vi.fn()} {...props} />))
}
afterEach(async () => { if (root) await act(async () => root.unmount()); container?.remove(); vi.restoreAllMocks() })

describe('empty child report followup', () => {
  it('explains the actual automatic child and links its saved parent with a return to this child', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('controlled no HTTP'))
    await render({ suggestedReviewAt: '2026-10-05T00:15:00Z' })
    expect(container.textContent).toContain('定时复查产生的子报告')
    expect(container.textContent).toContain('报告建议复查时间')
    expect(container.textContent).toContain('父报告是否已有计划需打开后确认')
    expect(container.textContent).not.toContain('父报告正在自动复查')
    expect(container.textContent).not.toContain('已计划')
    const link = container.querySelector('a')!, params = new URLSearchParams(link.getAttribute('href')!.split('?')[1])
    expect(params.get('run')).toBe(parent); expect(params.get('back')).toBe(back)
    expect(link.textContent).toContain('查看上次报告与复查记录')
    expect(fetch).not.toHaveBeenCalled()
  })
  it.each([{ failed: true }, { loading: true, plans: undefined }, { plans: [{ parent_run_id: 'other-run' } as ReviewScheduleView] }])('does not call an unconfirmed response empty or expose a parent plan claim: %j', async props => {
    await render(props)
    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).not.toContain('定时复查产生的子报告')
    expect(container.textContent).not.toContain('尚未登记自动检查计划')
  })
  it('preserves a manual child own failed plan and its original recovery destination', async () => {
    const plan: ReviewScheduleView = { schema_version: 'review-schedule-view.v1', review_plan_id: 'own-failed', parent_run_id: child, horizon: '30m', trigger_type: 'next_review', trigger_ref: 'ref', trigger_at: '2026-10-04T00:30:00Z', status: 'failed', child_run_id: 'failed-original', failure_code: 'research_observation_unavailable', updated_at: '2026-10-04T00:30:00Z' }
    const original = JSON.stringify(plan)
    await render({ admissionOrigin: 'manual', plans: [plan] })
    expect(container.textContent).not.toContain('定时复查产生的子报告')
    expect(container.textContent).not.toContain('查看上次报告与复查记录')
    const link = container.querySelector('a')!, params = new URLSearchParams(link.getAttribute('href')!.split('?')[1])
    expect(params.get('run')).toBe('failed-original'); expect(params.get('back')).toBe(back)
    expect(link.textContent).toContain('查看失败原因与恢复')
    expect(JSON.stringify(plan)).toBe(original)
  })
  it('keeps the original empty-plan behavior when no valid parent is present', async () => {
    await render({ parentRunId: child })
    expect(container.querySelector('a')).toBeNull()
    expect(container.textContent).toContain('尚未登记自动检查计划')
    expect(container.textContent).not.toContain('定时复查产生的子报告')
  })
  it('returns to the child even when its previous context was the home page', async () => {
    await render({ back: '#/home' })
    const params = new URLSearchParams(container.querySelector('a')!.getAttribute('href')!.split('?')[1])
    expect(params.get('run')).toBe(parent)
    expect(params.get('back')).toBe(researchRouteHref({ name: 'report', runId: child, back: '#/home' }))
  })
})
