import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { delivery, productOwner } from '../api/delivery'
import { RevisionFeedback } from './RevisionFeedback'
export { RevisionFeedback } from './RevisionFeedback'
import { AssetError, AssetPagination, assetTime, instrumentName, readOffset, routeChange, type AssetNavigate } from './assetFormat'
import { studyTitle } from './StudiesPage'
import { useReadCommand } from './useReadCommand'

function ReadCommandNotice({ command }: { command: ReturnType<typeof useReadCommand> }) {
  if (!command.pending && !command.error && !command.confirmed) return null
  return <div className="product-notice" role={command.error ? 'alert' : 'status'}>
    {command.pending && <>
      <p>阅读标记未确认。原操作：{command.pending.read ? '标记已读' : '恢复未读'}。重新进入此页不会自动提交。</p>
      <p>即使报告已离开当前筛选列表，仍需核对这一笔原请求。</p>
      <details><summary>原阅读凭据</summary><pre className="asset-json">{JSON.stringify(command.pending, null, 2)}</pre></details>
      <button className="button secondary" disabled={command.busy} onClick={command.retry}>{command.busy ? '正在核对原请求…' : '核对原标记回执'}</button>
    </>}
    {command.error && <>
      <p>阅读标记尚未确认，请保留原凭据并核对。</p>
      <details><summary>查看原始错误</summary><pre className="asset-json">{command.error}</pre></details>
    </>}
    {command.storageBlocked && <button className="button secondary" disabled={command.busy} onClick={command.restore}>重新读取阅读凭据</button>}
    {command.confirmed && !command.pending && <p>原阅读请求已确认；当前阅读状态以收件箱重新读取的记录为准。</p>}
  </div>
}

export function DeliveryPage({ params, onNavigate }: { params: URLSearchParams; onNavigate: AssetNavigate }) {
  const offset = readOffset(params), unread = params.get('unread') === '1'
  const run = params.get('run') || undefined
  const listBack = `#/delivery${params.size ? `?${params}` : ''}`
  const query = useQuery({ queryKey: ['product-reports', productOwner, offset, unread, run], queryFn: () => delivery.reports({ offset, unread, run }), retry: false })
  const routeParams = params.toString()
  const lastOffset = query.data ? Math.max(0, Math.floor((query.data.total - 1) / 12) * 12) : offset
  const needsPageCorrection = query.isSuccess && !query.isFetching && offset > lastOffset
  useEffect(() => {
    if (needsPageCorrection) {
      onNavigate('delivery', routeChange(new URLSearchParams(routeParams), { offset: lastOffset ? String(lastOffset) : undefined }))
    }
  }, [needsPageCorrection, lastOffset, routeParams, onNavigate])
  const read = useReadCommand()
  const change = (values: Record<string, string | undefined>) => onNavigate('delivery', routeChange(params, values))
  return <div className="content-wrap product-page"><div className="product-heading"><div><span className="product-kicker">每一版报告都有回执</span><h1>报告收件箱</h1><p>查看 AI 研究报告并明确标记已读。本地写出和渠道发送均不代表用户已读。</p><button className="product-link" onClick={() => onNavigate('Inbox')}>查看旧兼容分析账本</button></div><button className="button secondary" onClick={() => void query.refetch()}>刷新收件箱</button></div><div className="asset-filters"><label className="receipt-unread-filter"><input type="checkbox" checked={unread} onChange={e => change({ unread: e.target.checked ? '1' : undefined, offset: undefined })} />只看未标记已读</label>{run && <button className="product-link" onClick={() => change({ run: undefined, offset: undefined })}>显示全部报告</button>}</div><ReadCommandNotice command={read} />{needsPageCorrection ? <p role="status">正在回到有效页…</p> : query.isPending ? <p>读取报告…</p> : query.isError ? <AssetError retry={() => void query.refetch()} /> : <><section className="delivery-list">{query.data.items.map(item => <article className="product-card" key={item.revision_id}><div className="product-card-head"><span>{assetTime(item.created_at)} · {instrumentName(item.instrument)} · {item.strategy_version === 'research.v1' ? 'AI 研究' : '旧兼容分析'}</span><strong>{item.read_at ? '已标记阅读' : '未标记已读'}</strong></div><h2>{studyTitle(item.title)}</h2>{item.title.length > 90 && <details><summary>查看完整研究问题</summary><p>{item.title}</p></details>}<p className="product-muted">{item.local_written_at ? `本地写出：${assetTime(item.local_written_at)}` : '尚无本地写出回执'} · {item.external_dispatch_at ? `外部渠道发送：${assetTime(item.external_dispatch_at)}（未确认接收者阅读）` : '无外部渠道发送回执'}</p>{item.read_at && <p>阅读标记：{assetTime(item.read_at)}</p>}<div className="delivery-actions"><button className="button primary" onClick={() => onNavigate('report', { run: item.source_run_id, back: listBack })}>阅读这一版报告</button><button className="button secondary" disabled={read.blocked} onClick={() => read.mark(item.revision_id, !item.read_at)}>{item.read_at ? '恢复未读' : '标记已读'}</button><button className="product-link" onClick={() => onNavigate('studies', { study: item.study_id, back: listBack })}>查看主题变化</button></div><RevisionFeedback revision={item.revision_id} sourceRunId={item.source_run_id} saved={item.latest_feedback} /></article>)}</section>{query.data.total === 0 && <section className="product-card"><h2>当前没有符合筛选的报告</h2><p>新报告完成后会出现在这里，系统不会用打开页面的动作代替你的阅读标记。</p></section>}<AssetPagination limit={12} total={query.data.total} offset={offset} onChange={value => change({ offset: String(value) })} /></>}</div>
}
