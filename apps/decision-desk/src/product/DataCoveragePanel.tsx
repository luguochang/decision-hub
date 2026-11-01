import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { assets } from '../api/assets'
import { AssetError, assetTime } from './assetFormat'
export function coverageGapText(gap: string): string {
  const code = gap.toLowerCase()
  if (code.includes('connecterror')) return '无法连接数据来源'
  if (code.includes('timeout')) return '等待数据来源响应超时'
  if (code.includes('403') || code.includes('access_denied')) return '数据来源拒绝访问'
  if (code.includes('429') || code.includes('rate_limit')) return '数据来源限制了请求频率'
  if (code === 'provider_empty') return '来源未返回数据'
  if (code === 'okx_public_open_interest_has_no_historical_endpoint') return '该来源不提供历史持仓量接口'
  if (code === 'actual_coverage_does_not_span_requested_window') return '实际覆盖未达到请求区间'
  return '存在未分类缺口，请展开原始覆盖清单核对'
}

export function DataCoveragePanel({ jobId, names }: { jobId: string; names: Record<string, string> }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['job-coverage', jobId], queryFn: () => assets.jobCoverage(jobId), enabled: open, retry: false })
  return <div><button className="product-link" onClick={() => setOpen(!open)}>{open ? '收起数据覆盖' : '核验已保存的数据覆盖'}</button>{open && (query.isPending ? <p>核验覆盖清单及各分区哈希…</p> : query.isError ? <AssetError retry={() => void query.refetch()} /> : <><p>{query.data.datasets.some(dataset => dataset.partition_refs.length > 0) ? '以下为这次作业当时冻结的覆盖清单，已核验清单中列出的归档分区；起止时间不代表区间内没有缺失。' : '这次作业未保存任何数据分区，无法证明数据覆盖。以下保留当时的缺失记录。'}</p><div className="table-wrap"><table className="asset-table"><thead><tr><th>数据</th><th>状态 / 行数</th><th>实际起止</th><th>已知缺口</th></tr></thead><tbody>{query.data.datasets.map(dataset => <tr key={dataset.dataset_id}><td>{names[dataset.dataset_id] ?? '其他数据集'}<small>{dataset.instrument}</small></td><td>{dataset.status === 'complete' ? '覆盖完整' : dataset.status === 'partial' ? '部分覆盖' : '缺失'}<small>{dataset.row_count.toLocaleString()} 行 · {dataset.partition_refs.length} 个分区</small></td><td>{assetTime(dataset.actual_start_at)}<br />{assetTime(dataset.actual_end_at)}</td><td>{dataset.gaps.length ? dataset.gaps.map(coverageGapText).join('；') : '清单未报告缺口'}</td></tr>)}</tbody></table></div><details><summary>原始覆盖清单</summary><pre className="asset-json">{JSON.stringify(query.data, null, 2)}</pre></details></>)}</div>
}
