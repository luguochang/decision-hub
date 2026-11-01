import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { RunView } from '../api/types'
import { LedgerRunStatus } from './LedgerRunStatus'
import { legacyLedgerPreview } from './legacyRunPresentation'
import { AssetError, AssetPagination, assetTime, readOffset, routeChange, type AssetNavigate } from './assetFormat'

function ledgerDirectionPreview(headline: RunView['headline'], text: string) {
  // The saved revision prefix contains Chinese and enum spellings of one direction.
  // Only normalize that observed phrase; mismatched/unknown pairs and opaque text stay unchanged.
  const pair = headline?.trim().match(/^中文结论(?: revision [0-9]+[:：]|（第[0-9]+次修订）：)\s*BTC\s+七日主方向为价格规则(偏多|偏空)\/(long|short)\s+候选(?=[，,。；;]|$)/)
  if (!pair || pair[1] !== (pair[2] === 'long' ? '偏多' : '偏空')) return { text, originalDirection: null, direction: null }
  const duplicate = `价格规则${pair[1]}/${pair[1]} 候选`
  if (!text.includes(duplicate)) return { text, originalDirection: null, direction: null }
  return { text: text.replace(duplicate, `价格规则${pair[1]}候选`), originalDirection: pair[2], direction: pair[1] }
}

export function LegacyLedgerPage({ params = new URLSearchParams(), onInspect, onSubmit, onNavigate }: {
  params?: URLSearchParams
  onInspect: (run: RunView) => void
  onSubmit: () => void
  onNavigate: AssetNavigate
}) {
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary, refetchInterval: 5_000, retry: false })
  const health = useQuery({ queryKey: ['product-health'], queryFn: api.health, refetchInterval: 10_000, retry: false })
  const [initialCutoff] = useState(() => new Date().toISOString())
  const cutoff = params.get('before') || initialCutoff
  const status = params.get('status') || '', kind = params.get('kind') || '', q = params.get('q') || '', offset = readOffset(params)
  const [search, setSearch] = useState(q)
  useEffect(() => setSearch(q), [q])
  const change = (values: Record<string, string | undefined>) => onNavigate('Inbox', routeChange(params, { before: cutoff, ...values }))
  const records = useQuery({ queryKey: ['ledger-runs', offset, cutoff, status, kind, q], queryFn: () => api.ledgerRunsPage({ limit: 20, offset, created_before: cutoff, status: status || undefined, kind: kind || undefined, q: q || undefined }), retry: false, refetchOnWindowFocus: false })
  const runs = records.data?.items ?? []
  const data = summary.data
  return <div className="content-wrap product-page legacy-ledger">
    <div className="product-heading"><div><span className="product-kicker">历史记录 · 执行审计</span><h1>业务账本</h1><p>查阅研究与旧版分析记录。报告、原始证据和执行详情均保留。</p></div><div className="heading-actions">
      <button className="button secondary" onClick={() => onNavigate('management')}>返回管理与审计</button>
      <button className="button secondary" onClick={onSubmit}>提交事件文本</button>
      <button className="button primary" onClick={() => onNavigate('compose')}>发起研究</button>
    </div></div>
    {summary.isLoading && <p role="status">正在读取业务账本…</p>}
    {summary.isError && <div className="product-notice danger" role="alert"><div><strong>账本刷新失败</strong><p>{data ? '下方保留上次读取的记录，当前状态尚未确认。' : '暂时无法读取业务记录。'}</p><button className="button secondary" onClick={() => void summary.refetch()}>重试读取账本</button></div></div>}
    {data && <>
      <section className="product-grid ledger-metrics" aria-label="账本记录数量">
        <div className="product-card"><h2>等待执行</h2><strong>{data.inbox.pending_count}</strong><p>已接收、尚未开始的任务</p></div>
        <div className="product-card"><h2>正在执行</h2><strong>{data.inbox.running_count}</strong><p>每 5 秒更新一次</p></div>
        <div className="product-card"><h2>历史预测记录</h2><strong>{data.forecast_count}</strong><p>全历史，包含不交易和中性记录</p></div>
        <div className="product-card"><h2>历史结果标签</h2><strong>{data.evaluated_count}</strong><p>有标签不等于观点已验证</p></div>
      </section>
    </>}
      <section className="product-card"><div className="product-card-head"><div><h2>全部执行记录</h2><p className="product-muted">按创建时间从新到旧排列，收录截至 {assetTime(cutoff)}；状态为本次读取结果。</p></div><button className="product-link" onClick={() => change({ before: new Date().toISOString(), offset: undefined, record: undefined })}>查看最新记录</button></div>
        <form className="data-jobs-controls" onSubmit={event => { event.preventDefault(); change({ q: search.trim() || undefined, offset: undefined, record: undefined }) }}>
          <label>执行类型<select value={kind} onChange={event => change({ kind: event.target.value || undefined, offset: undefined, record: undefined })}><option value="">全部类型</option><option value="research">AI 研究</option><option value="baseline">规则分析</option><option value="other">其他版本</option></select></label>
          <label>执行状态<select value={status} onChange={event => change({ status: event.target.value || undefined, offset: undefined, record: undefined })}><option value="">全部状态</option><option value="admitted">等待执行</option><option value="running">执行中</option><option value="completed">执行完成</option><option value="degraded">降级完成</option><option value="failed">执行失败</option><option value="cancelled">已取消</option></select></label>
          <label>搜索记录<input value={search} maxLength={200} onChange={event => setSearch(event.target.value)} placeholder="摘要、任务编号或异常原因" /></label><button className="button secondary" type="submit">搜索</button>
          {(q || kind || status) && <button className="product-link" type="button" onClick={() => change({ q: undefined, kind: undefined, status: undefined, offset: undefined, record: undefined })}>清除筛选</button>}
        </form>
        {records.isPending && <p role="status">正在读取执行记录…</p>}
        {records.isError && <AssetError retry={() => void records.refetch()} />}
        {!records.isPending && !records.isError && <>

        <div className="table-wrap"><table><thead><tr><th>摘要预览</th><th>执行状态</th><th>类型</th><th>执行耗时</th><th>最后更新</th><th>操作</th></tr></thead><tbody>{runs.map(run => {
          const preview = legacyLedgerPreview(run.headline)
          const directionPreview = ledgerDirectionPreview(run.headline, preview.text)
          return <tr key={run.run_id}>
          <td><span className="ledger-headline">{directionPreview.text}</span>{preview.isExcerpt && <small className="product-muted">仅显示首句，完整判断与限制请查看详情。</small>}{directionPreview.originalDirection && <details className="legacy-direction-audit"><summary>核对原文方向写法</summary><p>原文把“{directionPreview.direction}”与方向值并列，它们表达同一方向。</p><code>{directionPreview.originalDirection}</code></details>}</td>
          <td><LedgerRunStatus status={run.gate_status} runStatus={run.status} errorCode={run.error_code} /></td>
          <td>{run.strategy_version === 'research.v1' ? 'AI 研究' : run.strategy_version === 'baseline.v1' ? '规则分析' : '其他版本'}</td>
          <td>{run.latency_ms === null ? '未记录' : `${(run.latency_ms / 1000).toFixed(1)} 秒`}</td>
          <td><time dateTime={run.updated_at}>{new Date(run.updated_at).toLocaleString('zh-CN', { hour12: false })}</time></td>
          <td><button className="product-link" aria-label={`查看记录 ${run.run_id}`} onClick={() => { onInspect(run); change({ record: run.run_id }) }}>查看详情</button></td>
        </tr> })}</tbody></table></div>
        {!runs.length && <p className="product-muted">本页没有匹配记录，可调整筛选或回到第一页。</p>}
        {records.data?.total != null ? <AssetPagination total={records.data.total} offset={offset} limit={20} onChange={value => change({ offset: value ? String(value) : undefined, record: undefined })} /> : <p role="alert">未取得匹配记录总数，请重新读取。</p>}
        </>}
      </section>
    <div className="product-grid">
      <section className="product-card"><h2>来源与服务</h2><p>{health.isError ? '来源状态读取失败，当前状态未知。' : !health.data ? '正在读取来源状态…' : `来源最近检查：${health.data.sources.filter(source => source.status === 'healthy').length} / ${health.data.sources.length} 项请求成功。连接成功仍需核对解析和覆盖。`}</p><div className="product-action-stack"><button className="product-link" onClick={() => onNavigate('sources')}>查看来源与运行状态</button><button className="product-link" onClick={() => onNavigate('Operations')}>打开运行审计</button></div></section>
      <section className="product-card"><h2>研究效果与经验</h2><p>预测总量包含不同方向和窗口，不能用“标签数 ÷ 全部预测数”作为完成率。到期资格、真实标签和样本外结果需要分别核对。</p><div className="product-action-stack"><button className="product-link" onClick={() => onNavigate('results')}>查看研究效果</button><button className="product-link" onClick={() => onNavigate('Evolution')}>查看经验候选与审核</button></div></section>
    </div>
  </div>
}
