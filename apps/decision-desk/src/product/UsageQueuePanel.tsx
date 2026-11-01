import { useQuery } from '@tanstack/react-query'
import { assets } from '../api/assets'

const labels: Record<string, string> = {
  pending: '首次待检查', captured: '已保存会话核验', partial: '记录不完整',
  waiting_archive: '等待日志归档', retry_wait: '读取失败待重试',
}
const filters = { all: '全部会话版本', ...labels, overdue: '检查延后超过 10 分钟', checking: '正在核对' }
const time = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚无记录'

export function UsageQueuePanel({ params = new URLSearchParams(), onNavigate }: { params?: URLSearchParams; onNavigate?: (scene: string, params?: Record<string, string | undefined>) => void }) {
  const query = useQuery({ queryKey: ['usage-queue-summary'], queryFn: assets.usageQueueSummary, retry: false, refetchInterval: 30_000 })
  const requested = params.get('usage_status')
  const selected = requested && Object.hasOwn(filters, requested) ? requested : 'all'
  const rawOffset = Number(params.get('usage_offset') ?? 0)
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? rawOffset : 0
  const jobs = useQuery({ queryKey: ['usage-queue-jobs', selected, offset], queryFn: () => assets.usageQueueJobs(selected, offset), enabled: requested !== null, retry: false })
  const change = (filter: string, page = 0) => onNavigate?.('management', { usage_status: filter, usage_offset: String(page) })
  const back = `#/management?${new URLSearchParams({ usage_status: selected, usage_offset: String(jobs.data?.offset ?? offset) })}`
  const view = query.data
  return <section className="product-card" aria-label="用量归集进度">
    <div className="product-card-head"><h2>用量归集进度</h2><button className="button secondary" disabled={query.isFetching || jobs.isFetching} onClick={() => { void query.refetch(); if (requested !== null) void jobs.refetch() }}>刷新归集进度</button></div>
    <p>后台核对研究日志并保存用量凭据。这里反映归集进度，不能据此认定费用已全部对账。</p>
    {query.isLoading && <p role="status">正在读取归集队列…</p>}
    {query.isError && <p role="alert">归集进度暂不可用。{view ? '下方保留上次读取的记录，不代表当前状态。' : '请重试，当前状态未知。'}</p>}
    {view && <>
      <p>{view.unique_runs.toLocaleString()} 次研究 · {view.session_versions.toLocaleString()} 个会话版本 · 正在核对 {view.checking} 项</p>
      <div className="product-monitor">
        {Object.entries(view.statuses).map(([state, value]) => <div key={state}><span>{labels[state] ?? '其他状态'}</span><strong>{value.toLocaleString()}</strong></div>)}
      </div>
      {view.session_versions === 0 ? <p>尚未登记用量归集任务；不代表研究费用为零。</p> : <p role="status">已到计划检查时间 {view.due} 项，其中延后超过 10 分钟 {view.overdue_10m} 项。{view.overdue_10m > 0 ? '队列仍有积压，请结合最近检查时间确认是否在推进。' : '目前没有超过 10 分钟的检查积压。'}</p>}
      <p>最近实际检查：{time(view.latest_check_at)}{view.oldest_due_at && <> · 最早到期计划：{time(view.oldest_due_at)}</>}</p>
      <p className="product-muted">新研究优先，历史任务保留检查名额。归档未变化时逐步降低频率；部分记录和缺失归档保持可见。单次用量及历史凭据可在报告的技术详情查看。</p>
      <small>本页读取于 {time(view.observed_at)}，每 30 秒刷新。</small>
    </>}
    {requested === null ? <p><button className="button secondary" onClick={() => change('all')}>查看归集任务</button></p> : <section aria-label="归集任务明细">
      <h3>定位需要核对的研究</h3>
      <label className="usage-queue-filter">归集状态 <select aria-label="筛选归集状态" value={selected} onChange={event => change(event.target.value)}>{Object.entries(filters).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <p>每行对应一次研究的一个会话版本；不同版本可能属于同一次研究。筛选和页码保存在当前地址，返回时可继续查看。</p>
      {jobs.isLoading && <p role="status">正在读取归集任务…</p>}
      {jobs.isError && <p role="alert">任务明细读取失败。{jobs.data ? '下方保留上次读取结果，当前状态尚未确认。' : '当前筛选结果未知。'}<button className="product-link" onClick={() => void jobs.refetch()}>重试读取任务</button></p>}
      {jobs.data && <>
        <p>共 {jobs.data.total} 个会话版本{jobs.data.total > 0 && <> · 当前 {jobs.data.offset + 1}–{jobs.data.offset + jobs.data.items.length}</>} · 明细读取于 {time(jobs.data.observed_at)}</p>
        {jobs.data.items.length === 0 ? <p>当前筛选下没有归集任务，不代表费用为零。</p> : <div className="usage-call-scroll"><table className="asset-table"><thead><tr><th>研究与会话版本</th><th>归集情况</th><th>最近检查 / 下次计划</th><th>查看记录</th></tr></thead><tbody>{jobs.data.items.map(item => <tr key={item.job_id}>
          <td>研究创建：{time(item.run_created_at)}<br />会话版本 {item.generation}<details><summary>核对研究编号</summary><code>{item.run_id}</code></details></td>
          <td>{labels[item.status] ?? '状态未识别'}{item.checking && <p>正在核对</p>}<p>{item.status === 'partial' ? '已保存部分用量，完整性仍待核对。' : item.status === 'waiting_archive' ? '尚未读到对应日志，后台会继续检查。' : item.status === 'retry_wait' ? '最近读取或保存失败，后台会自动重试。' : item.status === 'captured' ? '会话核验已保存，完整费用仍待对账。' : '等待后台检查日志。'}</p>{item.error_code && <details><summary>查看技术原因</summary><code>{item.error_code}</code></details>}</td>
          <td>{time(item.checked_at)}<br />计划：{time(item.next_check_at)}<p>已检查 {item.attempts} 次；计划时间可能因排队延后。</p></td>
          <td>{item.run_available ? <a className="product-link" href={`#/technical?run=${encodeURIComponent(item.run_id)}&back=${encodeURIComponent(back)}`}>查看研究与用量记录</a> : <span>关联研究不存在，保留归集记录供核查。</span>}</td>
        </tr>)}</tbody></table></div>}
        <div className="design-filter-row"><button className="button secondary" disabled={jobs.isFetching || jobs.data.offset === 0} onClick={() => change(selected, Math.max(0, jobs.data!.offset - jobs.data!.limit))}>上一页归集任务</button><button className="button secondary" disabled={jobs.isFetching || !jobs.data.has_more} onClick={() => change(selected, jobs.data!.offset + jobs.data!.limit)}>下一页归集任务</button></div>
      </>}
    </section>}
  </section>
}
