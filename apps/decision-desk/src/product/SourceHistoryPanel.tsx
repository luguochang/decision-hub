import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { assets } from '../api/assets'
import { apiUrl } from '../api/client'
import { AssetError, assetTime, type AssetNavigate } from './assetFormat'

export function SourceHistoryPanel({ names, onNavigate }: { names: Record<string, string>; onNavigate: AssetNavigate }) {
  const query = useQuery({ queryKey: ['source-history'], queryFn: assets.sourceHistory, retry: false, refetchInterval: 60_000 })
  const days = query.data ? Array.from({ length: 14 }, (_, i) => new Date(Date.parse(query.data!.start_at) + i * 86400000).toISOString().slice(0, 10)) : []
  const sources = [...new Set([...Object.keys(names), ...(query.data?.items.map(item => item.source_id) ?? [])])]
  return <section className="product-card source-history"><div className="product-card-head"><h2>近 14 天来源采集</h2><button className="button secondary" onClick={() => void query.refetch()}>刷新覆盖</button></div><p>按 UTC 自然日统计所有轮询记录。绿色表示当日有成功请求，黄色表示出现失败，灰色表示没有请求记录；请求成功不等于日历或研究材料完整。</p>
    {query.isPending ? <p>读取来源历史…</p> : query.isError ? <AssetError retry={() => void query.refetch()} /> : <div className="table-wrap"><table className="source-heatmap"><thead><tr><th>来源</th>{days.map(day => <th key={day}>{day.slice(5)}</th>)}</tr></thead><tbody>{sources.map(source => <tr key={source}><th><button className="product-link" onClick={() => onNavigate('sources', { source })}>{names[source] ?? '其他登记来源'}</button></th>{days.map(day => {
      const rows = query.data.items.filter(row => row.source_id === source && row.day === day)
      const count = (statuses: string[]) => rows.filter(row => statuses.includes(row.status)).reduce((sum, row) => sum + row.attempts, 0)
      const success = count(['succeeded', 'empty']), failed = count(['failed']), other = rows.reduce((sum, row) => sum + row.attempts, 0) - success - failed
      const detail = `${day} UTC：成功或无新增 ${success} 次，失败 ${failed} 次，进行中或跳过 ${other} 次；采纳 ${rows.reduce((sum, r) => sum + r.accepted, 0)} 项`
      return <td key={day}><span role="img" className={`source-heat-cell ${failed ? 'warning' : success ? 'success' : 'empty'}`} aria-label={`${names[source] ?? source} ${detail}`} title={detail}>{failed ? '!' : success ? '✓' : other ? '·' : '—'}</span></td>
    })}</tr>)}</tbody></table></div>}
  </section>
}

export function PollCaptureLinks({ attemptId }: { attemptId: string }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['poll-captures', attemptId], queryFn: () => assets.pollCaptures(attemptId), enabled: open, retry: false })
  return <div><button className="product-link" onClick={() => setOpen(!open)}>{open ? '收起采集文件' : '查看采集文件'}</button>{open && (query.isPending ? <p>读取原始采集…</p> : query.isError ? <AssetError retry={() => void query.refetch()} /> : query.data.length ? <ul>{query.data.map((capture, index) => <li key={capture.capture_id}>{capture.body_ref ? <CaptureReadback captureId={capture.capture_id} index={index + 1} /> : <span>未取得响应正文</span>}<small>{assetTime(capture.completed_at)} · {capture.status_code === null ? '无 HTTP 回执' : `HTTP ${capture.status_code}`}</small></li>)}</ul> : <p>这次历史轮询未保存原始 HTTP 文件，不补造成功回执。</p>)}</div>
}

export function CaptureReadback({ captureId, index }: { captureId: string; index: number }) {
  const [open, setOpen] = useState(false)
  const query = useQuery({ queryKey: ['capture-text', captureId], queryFn: () => assets.captureText(captureId), enabled: open, retry: false })
  return <div><button className="product-link" onClick={() => setOpen(!open)}>{open ? '收起原文' : `查看原始响应 ${index}`}</button>{open && <div className="capture-readback"><p>按纯文本查看保存的响应，不执行网页脚本。预览最多显示前 12,000 字。</p>{query.isPending ? <p>读取原文…</p> : query.isError ? <AssetError retry={() => void query.refetch()} /> : <><pre tabIndex={0} aria-label="原始响应预览">{query.data.slice(0, 12000)}</pre>{query.data.length > 12000 && <p>原文共 {query.data.length.toLocaleString()} 字，当前预览已截断。</p>}</>}<a className="product-link" href={apiUrl(`/v1/raw-captures/${encodeURIComponent(captureId)}/body`)} target="_blank" rel="noreferrer">在浏览器打开完整原文</a></div>}</div>
}
