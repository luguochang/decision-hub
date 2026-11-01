// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { riskResearchStatusSchema } from '@decision-hub/contracts-ts'
import { afterEach, expect, it, vi } from 'vitest'
import { researchQualityApi, ResearchReportServingError } from '../api/researchQuality'
import * as clientApi from '../api/client'
import { ResearchQuality } from './ResearchQuality'
import { RiskResearch } from './RiskResearch'
import { failureExplanation, technicalSummaryName } from '../research/technicalPresentation'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => vi.restoreAllMocks())

it('distinguishes stopped preparation from an unknown COMMIT and prevents an unconfirmed report download', async () => {
  const read = vi.spyOn(researchQualityApi, 'read').mockRejectedValueOnce(new ResearchReportServingError({
    run_id: 'run-control', state: 'unconfirmed', state_version: 4, confirmed_at: null,
    reason_code: 'publication_bundle_preparation_failed',
  })).mockRejectedValueOnce(new ResearchReportServingError({
    run_id: 'run-control', state: 'unconfirmed', state_version: 5, confirmed_at: null,
    reason_code: 'publication_bundle_commit_unconfirmed',
  }))
  const element=document.createElement('div'); document.body.append(element); const root=createRoot(element)
  try {
    await act(async()=>root.render(<ResearchQuality runId="run-control" />))
    expect(element.textContent).toContain('报告整理失败')
    expect(element.textContent).toContain('后台整理已停止')
    expect(element.textContent).not.toContain('稍后')
    expect(element.textContent).not.toContain('publication_bundle_preparation_failed')
    expect(element.querySelector('a[download]')).toBeNull()
    await act(async()=>Array.from(element.querySelectorAll('button')).find(b=>b.textContent==='重试读取')!.click())
    expect(read).toHaveBeenCalledTimes(2)
    expect(element.textContent).toContain('报告保存回执尚未确认')
    expect(element.textContent).not.toContain('后台整理已停止')
    expect(element.querySelector('a[download]')).toBeNull()
  } finally {await act(async()=>root.unmount());element.remove()}
})

it.each(['risk_research_admission_writer_busy','spot_current_original_path_not_fresh'])('keeps %s in the original admission state without running a fourth attempt',async code=>{
  const scope='crypto:spot:okx:BTC-USDT:trade:1m'
  const client=new QueryClient({defaultOptions:{queries:{retry:false,staleTime:Infinity}}})
  const view=riskResearchStatusSchema.parse({schema_version:'risk-research-status.v1',scope_id:scope,enabled:true,
    as_of:'2026-10-10T04:00:00Z',request:{schema_version:'risk-research-request.v1',request_id:'synthetic-control',
      scope_id:scope,instrument:'BTC-USDT',status:'failed',run_id:null,run_status:null,created_at:'2026-10-10T03:30:00Z',
      available_at:'2026-10-10T04:05:00Z',attempts:3,error_code:code,trigger_refs:[]}})
  client.setQueryData(['risk-research',scope],view)
  const request=vi.spyOn(clientApi,'request').mockRejectedValue(new Error('no transport in synthetic display control'))
  const element=document.createElement('div');document.body.append(element);const root=createRoot(element)
  try {
    await act(async()=>root.render(<QueryClientProvider client={client}><RiskResearch scopeId={scope} onNavigate={vi.fn()} /></QueryClientProvider>))
    expect(element.textContent).toContain(code==='risk_research_admission_writer_busy'?'数据库写入暂时繁忙':'分钟原路径不够新')
    expect(element.textContent).toContain('本次请求已结束')
    expect(element.textContent).toContain('系统不会自动发起第四次')
    expect(element.textContent).not.toContain('研究已进入执行队列')
    expect(Array.from(element.querySelectorAll('button')).find(b=>b.textContent==='查看研究执行记录')).toBeUndefined()
    expect(element.querySelector('code')?.textContent).toBe(code)
    expect(request).not.toHaveBeenCalled()
  } finally {await act(async()=>root.unmount());client.clear();element.remove()}
})

it('explains the saved binding failure without guessing which operation held a slot or converting unknown causes',()=>{
  expect(technicalSummaryName('research_method_binding_failed')).toContain('运行身份核验未完成')
  expect(technicalSummaryName('cancellation_projection_busy')).toContain('暂不可执行')
  const known=failureExplanation('research_method_binding_failed','cancellation_projection_busy')!
  expect(known).toContain('较早的异常记录保留')
  expect(known).not.toMatch(/SDK不可用|两|超时|数据库|取消成功/)
  expect(failureExplanation('research_method_binding_failed','unknown-cause')).toBeNull()
  expect(technicalSummaryName('unknown-cause')).toBe('尚无中文说明，见原始记录')
})
