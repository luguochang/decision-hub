import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { z } from 'zod'
import { request } from '../api/client'
import { requirementNames } from '../research/technicalPresentation'
import { researchDisplayText } from './researchLanguage'

const item = z.object({ case_id: z.string().min(1), run_id: z.string().min(1), revision_id: z.string().min(1), instrument: z.string().min(1), horizon: z.string().min(1), classification: z.enum(['verified_market_case', 'product_feedback', 'operational_failure', 'model_error', 'research_diagnostic']), available_at: z.string().datetime({ offset: true }), original_cutoff_at: z.string().datetime({ offset: true }).nullable(), label_known_at: z.string().datetime({ offset: true }).nullable(), mechanism: z.string().nullable(), counter_evidence: z.array(z.string()), unresolved_questions: z.array(z.string()), exclusion_reasons: z.array(z.string()), stored_eligible: z.boolean(), current_qualification: z.literal('not_reassessed') }).strict()
const viewSchema = z.object({ schema_version: z.literal('market-cases-view.v1'), scope_id: z.string(), as_of: z.string().datetime({ offset: true }), integrity: z.literal('stored_payload_hash'), items: z.array(item), has_more: z.boolean() }).strict()
const classes = { verified_market_case: '历史市场案例', product_feedback: '使用反馈', operational_failure: '运行失败记录', model_error: '模型错误记录', research_diagnostic: '研究诊断记录' }
const time = (value: string) => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
type Navigate = (scene: string, params?: Record<string, string | undefined>) => void
const questionText = (text: string) => Object.hasOwn(requirementNames, text)
  ? `仍需核验：${requirementNames[text]}`
  : /^[a-z][a-z0-9]*[_:.][a-z0-9_.:-]+$/.test(text)
    ? '另有一项未登记的核验内容，原始标识见下方原件。'
    : researchDisplayText(text)

export function MarketMemory({ scopeId, onNavigate }: { scopeId: string; onNavigate: Navigate }) {
  const [offset, setOffset] = useState(0)
  const query = useQuery({ queryKey: ['market-memory', scopeId, offset], enabled: Boolean(scopeId), retry: false, queryFn: async ({ signal }) => {
    const view = viewSchema.parse(await request(`/v1/risk/scopes/${encodeURIComponent(scopeId)}/cases?limit=10&offset=${offset}`, { signal }))
    if (view.scope_id !== scopeId) throw new Error('案例返回的资产范围无法核对。')
    return view
  } })
  return <details className="product-card" aria-label="研究记忆与复盘"><summary><strong>研究记忆与复盘</strong> · {query.data?.items.length ?? 0} 条当前记录</summary>
    <p>保存每次报告的原结论、最强反方和未解决问题。没有成熟标签或有效版本的记录保留为诊断，不能当作预测成功经验。</p>
    {query.isError && <p role="alert">案例读取失败；已保存的原件尚未核对。</p>}
    {!query.data?.items.length && <p>{query.isLoading ? '正在读取已保存的案例…' : '尚无这个资产的案例。研究报告生成后会自动归档；后续标签成熟时产生新的复盘版本。'}</p>}
    {query.data?.items.map(caseItem => <article key={caseItem.case_id}><h3>{classes[caseItem.classification]} · {researchDisplayText(caseItem.horizon)}</h3><p>{caseItem.mechanism ? researchDisplayText(caseItem.mechanism) : '本次没有保存完整机制结论。'}</p><p className="product-muted">归档：{time(caseItem.available_at)} · {caseItem.label_known_at ? `标签已记录：${time(caseItem.label_known_at)}` : '尚无可核验的成熟标签'}；列表未重新认证当前资格。</p>
      <details><summary>反证与未解决问题</summary>{caseItem.counter_evidence.length ? <ul>{caseItem.counter_evidence.map((text, index) => <li key={index}>{researchDisplayText(text)}</li>)}</ul> : <p>原报告没有保存反方链，不能当作已排除反证。</p>}{caseItem.unresolved_questions.length > 0 && <ul>{caseItem.unresolved_questions.map((text, index) => <li key={index}>{questionText(text)}</li>)}</ul>}</details>
      <button type="button" className="button secondary" onClick={() => onNavigate('report', { run: caseItem.run_id, back: '#/home' })}>阅读原始报告</button>
      <details className="risk-audit"><summary>原件与排除原因</summary><p>{caseItem.case_id} · {caseItem.revision_id}</p><p>{caseItem.exclusion_reasons.join('、') || '原归档没有排除原因；仍需当前原件认证。'}</p>{caseItem.unresolved_questions.length > 0 && <p>原始未解问题：{caseItem.unresolved_questions.join('、')}</p>}</details>
    </article>)}
    <div className="risk-actions"><button className="button secondary" type="button" disabled={query.isFetching} onClick={() => { void query.refetch() }}>刷新案例记录</button>{offset > 0 && <button className="button secondary" type="button" onClick={() => setOffset(value => Math.max(0, value - 10))}>上一页案例</button>}{query.data?.has_more && <button className="button secondary" type="button" onClick={() => setOffset(value => value + 10)}>下一页案例</button>}</div>
    <p className="product-muted">历史经验检索需匹配资产、期限和有效分析版本；当前尚未登记稳定方法时不会把旧案例自动塞入新研究。黄金的数据、报价口径、日历与标签须独立准入后开放。</p>
  </details>
}
