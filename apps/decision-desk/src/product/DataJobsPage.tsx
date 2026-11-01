import { DataCoveragePanel } from './DataCoveragePanel'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Database, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api/client'
import type { DataJobView } from '../api/dataJobs'
import { AssetError, AssetPagination, readOffset, routeChange, type AssetNavigate } from './assetFormat'

const statusNames: Record<DataJobView['status'], string> = { queued: '等待更新', running: '更新中', partial: '部分完成', failed: '更新失败', completed: '已完成' }
const datasetNames: Record<string, string> = {
  'crypto.btc_usdt.ohlcv_1h': 'BTC 每小时行情', 'crypto.btc_usdt.ohlcv_1d': 'BTC 每日行情', 'crypto.eth_usdt.ohlcv_1d': 'ETH 每日行情', 'crypto.btc_usdt_swap.funding_rate': 'BTC 永续资金费率', 'crypto.btc_usdt_swap.open_interest': 'BTC 永续持仓量', 'macro.fred.dgs2': '美国两年期国债收益率', 'macro.fred.dgs10': '美国十年期国债收益率', 'macro.fred.dtwexbgs': '美元指数',
}
const failureName = (error: string) => {
  const code = error.toLowerCase()
  if (code.includes('connecterror')) return '无法连接数据来源'
  if (code.includes('timeout')) return '等待数据来源响应超时'
  if (code.includes('403') || code.includes('access_denied')) return '数据来源拒绝访问'
  if (code.includes('rate_limit') || code.includes('429')) return '数据来源限制了请求频率'
  return '本次采集未完成，原始原因见技术记录'
}
const at = (value: string | null) => value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : '暂无记录'

export function DataJobsPage({ params = new URLSearchParams(), onNavigate }: { params?: URLSearchParams; onNavigate: AssetNavigate }) {
  const [initialCutoff] = useState(() => new Date().toISOString())
  const cutoff = params.get('before') || initialCutoff
  const status = params.get('status') || '', dataset = params.get('dataset') || '', offset = readOffset(params), selectedJob = params.get('job')
  const change = (values: Record<string, string | undefined>) => onNavigate('data-jobs', routeChange(params, { before: cutoff, ...values }))
  const query = useQuery({ queryKey: ['data-jobs-page', status, dataset, offset, cutoff], queryFn: () => api.dataJobsPage({ limit: 20, offset, status: status || undefined, dataset: dataset || undefined, created_before: cutoff }), retry: false, refetchInterval: 30_000 })
  const jobs = query.data?.items ?? []
  const total = query.data?.total
  return <div className="content-wrap product-page data-jobs-page">
    <button className="product-link" onClick={() => onNavigate('management')}><ArrowLeft size={15} />返回管理与审计</button>
    <div className="product-heading"><div><span className="product-kicker">历史数据</span><h1>数据更新记录</h1><p>每次补采独立留痕。失败记录仍显示，不能把作业启动当作数据覆盖已完成。</p></div><button className="button secondary" onClick={() => change({ before: new Date().toISOString(), offset: undefined, job: undefined })}><RefreshCw size={15} />查看最新记录</button></div>
    <div className="product-card data-jobs-summary"><Database size={19} /><div><strong>{query.isPending ? '读取中' : query.isError ? '记录暂不可读' : total == null ? '总数未取得' : `${total.toLocaleString()} 次匹配记录`}</strong><small>收录截至 {at(cutoff)}；作业状态和覆盖核验结果为当前状态。</small></div></div>
    <section className="product-card"><div className="data-jobs-controls"><label>状态<select value={status} onChange={(event) => change({ status: event.target.value || undefined, offset: undefined, job: undefined })}><option value="">全部状态</option>{Object.entries(statusNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label>数据集<select value={dataset} onChange={(event) => change({ dataset: event.target.value || undefined, offset: undefined, job: undefined })}><option value="">全部数据集</option>{Object.entries(datasetNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>{query.isError && <AssetError retry={() => void query.refetch()} />}{query.isPending && <p>读取更新记录…</p>}{!query.isPending && !query.isError && jobs.length === 0 && <p className="product-muted">当前页没有匹配记录。可调整筛选或回到第一页。</p>}{jobs.map((job) => <details className="data-job" key={job.job_id} open={selectedJob === job.job_id}><summary onClick={event => { event.preventDefault(); change({ job: selectedJob === job.job_id ? undefined : job.job_id }) }}><span><strong>{job.dataset_ids.map((id) => datasetNames[id] ?? '其他数据集').join('、')}</strong><small>{at(job.created_at)} · 更新区间 {at(job.window_start_at)} 至 {at(job.window_end_at)}</small></span><b className={job.status === 'failed' ? 'danger' : job.status === 'completed' ? 'success' : ''}>{statusNames[job.status]}</b></summary><div className="data-job-detail"><p>尝试次数：{job.attempt} · 数据分区：{job.partition_refs.length} 项 · 完成时间：{at(job.finished_at)}</p>{job.errors.length > 0 && <p>失败原因：{[...new Set(job.errors.map(failureName))].join('；')}</p>}{job.coverage_manifest_ref ? <DataCoveragePanel jobId={job.job_id} names={datasetNames} /> : <p>尚未登记覆盖清单，作业状态不能替代实际数据覆盖。</p>}<details><summary>技术记录</summary><code>{job.job_id}</code>{job.errors.length > 0 && <pre className="asset-json">{job.errors.join('\n')}</pre>}</details></div></details>)}{!query.isPending && !query.isError && (total != null ? <AssetPagination total={total} offset={offset} limit={20} onChange={value => change({ offset: String(value), job: undefined })} /> : <p role="alert">接口未返回记录总数，暂时无法确认完整分页。请刷新或检查服务版本。</p>)}</section>
  </div>
}
