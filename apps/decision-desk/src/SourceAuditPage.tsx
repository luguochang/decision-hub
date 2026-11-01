import { sourceFailure } from './product/sourceFailure'
import { AssetBackLink, assetBackLabel } from './product/assetFormat'
import { useDetailFocus } from './product/useDetailFocus'
import { useQuery } from '@tanstack/react-query'
import { Activity, AlertTriangle, CheckCircle2, Clock3, Database, Network } from 'lucide-react'
import { api } from './api/client'
import type { OperationsOverview, ProductHealth } from './api/types'
import type { SourcePollAttempt } from './api/sourceAudit'
import { SourceHistoryPanel, PollCaptureLinks } from './product/SourceHistoryPanel'

const PAGE_SIZE = 20
const SOURCE_NAMES: Record<string, string> = {
  'bea-news': '美国经济分析局公告',
  'bls-calendar': '美国劳工统计局日历',
  'bls-releases': '美国劳工统计局数据发布',
  'boj-calendar': '日本央行日历',
  'ecb-calendar': '欧洲央行日历',
  'fed-calendar': '美联储日历',
  'fed-press': '美联储新闻',
  'fed-speeches': '美联储官员讲话',
}
const sourceName = (sourceId: string) => SOURCE_NAMES[sourceId] ?? '已登记来源'

type SourceState = {
  sourceId: string
  operation?: OperationsOverview['sources'][number]
  health?: ProductHealth['sources'][number]
}

function timeLabel(value: string | null | undefined): string {
  if (!value) return '暂无记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间不可用' : date.toLocaleString('zh-CN', { hour12: false })
}

function sourceStatus(source: SourceState): string {
  if (source.operation?.enabled === false) return '未启用'
  const status = source.health?.status ?? source.operation?.status
  if (status === 'healthy' || status === 'online' || status === 'ok') return '最近采集可用'
  if (status === 'degraded' || status === 'failed') return '异常'
  if (status === 'offline') return '离线'
  return '未确认'
}

function attemptStatus(attempt: SourcePollAttempt): string {
  if (attempt.status === 'succeeded') return '采集成功'
  if (attempt.status === 'empty') return '本次无新内容'
  if (attempt.status === 'failed') return '采集失败'
  if (attempt.status === 'skipped') return '已跳过'
  if (attempt.status === 'running') return '进行中'
  return '状态未确认'
}

function ContentSummary({ attempt }: { attempt: SourcePollAttempt }) {
  const snapshot = attempt.content_snapshot
  if (!snapshot) return <span>未记录内容覆盖</span>
  if (snapshot.http_status && snapshot.http_status >= 400) return <span>{sourceFailure(attempt.error_code, snapshot.http_status).reason}（HTTP {snapshot.http_status}）</span>
  if (snapshot.parse_status === 'invalid') return <span>页面已收到，内容结构无法识别</span>
  if (snapshot.parse_status !== 'valid') return <span>尚未完成内容解析</span>
  return <div><span>已解析 {snapshot.parsed_count ?? '未知'} 项</span>{snapshot.upcoming_count !== null && <><br /><span>{snapshot.upcoming_count > 0 ? `未来 ${snapshot.upcoming_count} 项：${snapshot.first_upcoming_date} 至 ${snapshot.last_upcoming_date}` : '覆盖异常：未发现未来事件'}</span></>}</div>
}

function serviceCard(label: string, service: OperationsOverview['services'][number] | undefined, missing: string) {
  const online = service?.status === 'online'
  return <div className="design-source-row"><div><strong>{label}</strong><span>{service ? `最近心跳：${timeLabel(service.heartbeat_at)}` : missing}</span>{service?.last_error_code && <details><summary>技术详情</summary><code>{service.last_error_code}</code></details>}</div><span className={`status-pill ${online ? 'success' : service ? 'warning' : 'neutral'}`}>{service ? online ? '在线' : '未在线' : '未上报'}</span></div>
}

export function SourceAuditPage({ params, onNavigate }: { params: URLSearchParams; onNavigate: (scene: string, params?: Record<string, string | undefined>) => void }) {
  const requestedBack = params.get('back')
  const back = assetBackLabel(requestedBack) ? requestedBack! : '#/management'
  const sourceNavigate = (scene: string, next?: Record<string, string | undefined>) => onNavigate(scene, { ...next, back })
  const operationsQuery = useQuery({ queryKey: ['operations'], queryFn: api.operations, refetchInterval: 10_000, retry: false })
  const healthQuery = useQuery({ queryKey: ['product-health'], queryFn: api.health, refetchInterval: 10_000, retry: false })
  const operations = operationsQuery.isError ? undefined : operationsQuery.data
  const health = healthQuery.isError ? undefined : healthQuery.data
  const statusUnavailable = operationsQuery.isError || healthQuery.isError
  const sourceIds = [...new Set([...(operations?.sources ?? []).map((source) => source.source_id), ...(health?.sources ?? []).map((source) => source.source_id)])]
  const sources: SourceState[] = sourceIds.map((sourceId) => ({ sourceId, operation: operations?.sources.find((source) => source.source_id === sourceId), health: health?.sources.find((source) => source.source_id === sourceId) }))
  const selectedId = params.get('source') || sources[0]?.sourceId
  const rawOffset = Number(params.get('offset') ?? '0')
  const offset = Number.isSafeInteger(rawOffset) && rawOffset >= 0 ? Math.floor(rawOffset / PAGE_SIZE) * PAGE_SIZE : 0
  const selected = sources.find((source) => source.sourceId === selectedId)
  const attemptsQuery = useQuery({ queryKey: ['source-poll-attempts', selectedId, offset], queryFn: () => api.sourcePollAttempts(selectedId!, PAGE_SIZE + 1, offset), enabled: Boolean(selectedId), retry: false })
  const detailRef = useDetailFocus(params.get('source') ? `${params.get('source')}:${offset}` : null, !attemptsQuery.isLoading && !operationsQuery.isLoading && !healthQuery.isLoading)
  const attempts = attemptsQuery.data?.slice(0, PAGE_SIZE) ?? []
  const discoveryWorker = operations?.services.find((service) => service.service_id === 'hub-realtime-worker' || service.role === 'realtime_worker' || service.role.includes('source') || service.service_id.includes('source') || service.service_id.includes('acquisition'))
  const researchWorker = operations?.services.find((service) => service.service_id === 'hub-research-worker')
  const priorityWorker = operations?.services.find((service) => service.service_id === 'hub-research-priority-worker')
  const apiService = operations?.services.find((service) => service.service_id === 'hub-api')
  const navigatePage = (nextOffset: number) => sourceNavigate('sources', { source: selectedId, offset: nextOffset ? String(nextOffset) : undefined })
  const selectedError = selected?.health ? selected.health.error_code : selected?.operation?.error_code
  const refreshing = attemptsQuery.isFetching || operationsQuery.isFetching || healthQuery.isFetching
  const refreshRecords = () => Promise.all([attemptsQuery.refetch(), operationsQuery.refetch(), healthQuery.refetch()])

  return <div className="content-wrap design-state-page source-audit-page">
    <AssetBackLink back={back} />
    <section className="page-heading design-state-heading"><div><div className="eyebrow"><span className="live-dot" />真实来源记录</div><h1>来源与运行状态</h1><p>查看发现、研究进程和各来源的真实轮询记录。</p></div><span className={`status-pill ${health?.status === 'ok' ? 'success' : healthQuery.isError || health?.status === 'degraded' ? 'warning' : 'neutral'}`}>{healthQuery.isLoading ? '读取中' : healthQuery.isError ? '健康状态不可用' : health?.status === 'ok' ? '采集连接可用' : health?.status === 'degraded' ? '存在降级' : '状态未确认'}</span></section>
    <SourceHistoryPanel names={SOURCE_NAMES} onNavigate={sourceNavigate} />
    <div className="design-source-grid">
      <section className="panel"><header className="panel-header"><div><h2>运行进程</h2><p>来源发现与研究执行分别核对，不把旧观察运行时当成研究进程。</p></div></header><div className="design-source-list">
        {operationsQuery.isError && <div className="workspace-state danger"><AlertTriangle size={17} />运行进程状态不可用。</div>}
        {operationsQuery.isLoading && <div className="workspace-state"><Activity size={17} />正在读取进程心跳…</div>}
        {operations && <>{serviceCard('业务接口', apiService, '接口进程未单独上报')}{serviceCard('来源发现进程', discoveryWorker, '未单独上报发现进程；以下来源记录仍可审计')}{serviceCard('自动研究通道', researchWorker, '自动研究进程未上报')}{serviceCard('用户研究通道', priorityWorker, '用户研究进程未上报')}</>}
      </div></section>
      <section className="panel"><header className="panel-header"><div><h2>来源概况</h2><p>最近采集可用不代表事件完整；在下方核对每次解析与未来覆盖。</p></div></header><div className="design-source-list">
        {healthQuery.isError && <div className="workspace-state danger"><AlertTriangle size={17} />来源健康状态不可用。</div>}
        {healthQuery.isLoading && <div className="workspace-state"><Activity size={17} />正在读取来源健康…</div>}
        {!healthQuery.isLoading && !operationsQuery.isLoading && !statusUnavailable && sources.length === 0 && <div className="workspace-state"><Network size={17} />当前没有已登记的来源。</div>}
        {sources.map((source) => <button key={source.sourceId} type="button" className="design-source-row" aria-pressed={selectedId === source.sourceId} onClick={() => sourceNavigate('sources', { source: source.sourceId })} style={{ width: '100%', textAlign: 'left', background: selectedId === source.sourceId ? '#f3f8ff' : 'transparent', border: 0, cursor: 'pointer' }}><div><strong>{sourceName(source.sourceId)}</strong><span>最近成功：{timeLabel(source.health?.last_success_at ?? source.operation?.last_success_at)}</span><span>最近错误：{timeLabel(source.health?.last_error_at)}</span><span>下次计划：{timeLabel(source.health?.next_poll_at ?? source.operation?.next_poll_at)}</span></div><span className={`status-pill ${sourceStatus(source) === '最近采集可用' ? 'success' : sourceStatus(source) === '异常' || sourceStatus(source) === '离线' ? 'warning' : 'neutral'}`}>{sourceStatus(source)}</span></button>)}
      </div></section>
    </div>
    {selectedId && <section ref={detailRef} tabIndex={-1} className="panel design-table-panel"><header className="panel-header"><div><h2>{sourceName(selectedId)} · 轮询记录</h2><p>每次请求均显示结果与时间；无新内容也保留记录。{attemptsQuery.dataUpdatedAt > 0 && <> 读取于 {timeLabel(new Date(attemptsQuery.dataUpdatedAt).toISOString())}。</>}</p><button className="button secondary" type="button" disabled={refreshing} onClick={() => void refreshRecords()}>{refreshing ? '正在刷新记录…' : '刷新轮询记录'}</button><details><summary>来源技术标识</summary><code>{selectedId}</code></details></div><span className="status-pill neutral">{selected ? sourceStatus(selected) : statusUnavailable ? '来源状态未确认' : '来源未登记'}</span></header>
      {selectedError && <div className="product-notice warning" role="status"><div><strong>{sourceFailure(selectedError).reason}</strong><p>{sourceFailure(selectedError).recovery}</p>{['source_access_denied', 'source_authentication_required'].includes(selectedError) && <p>自动采集已延后，访问受限期间至少间隔 24 小时再检查。下次计划：{timeLabel(selected?.health?.next_poll_at ?? selected?.operation?.next_poll_at)}。</p>}<p>刷新仅重新读取已保存的状态和记录。已保存的研究报告仍可查看。</p><details><summary>技术详情</summary><code>{selectedError}</code></details></div></div>}
      {attemptsQuery.isLoading && <div className="workspace-state"><Activity size={17} />正在读取轮询记录…</div>}
      {attemptsQuery.isError && <div className="workspace-state danger"><AlertTriangle size={17} />轮询记录暂时无法更新。{attemptsQuery.data ? '以下保留上次读取的记录，请刷新后核对最新状态。' : '请点击刷新轮询记录重试。'}<details><summary>技术详情</summary><code>{attemptsQuery.error.message}</code></details></div>}
      {!attemptsQuery.isLoading && !attemptsQuery.isError && attempts.length === 0 && <div className="workspace-state"><Database size={17} />该来源尚无轮询记录。</div>}
      {attempts.length > 0 && <div className="table-wrap"><table className="design-data-table"><thead><tr><th>开始时间</th><th>完成时间</th><th>结果</th><th>接收 / 采纳</th><th>重复 / 修订</th><th>内容与覆盖</th><th>详情</th></tr></thead><tbody>{attempts.map((attempt) => <tr key={attempt.attempt_id}><td>{timeLabel(attempt.started_at)}</td><td>{timeLabel(attempt.completed_at)}</td><td><span className={`status-pill ${attempt.status === 'succeeded' ? 'success' : attempt.status === 'failed' ? 'warning' : 'neutral'}`}>{attempt.status === 'succeeded' ? <CheckCircle2 size={13} /> : attempt.status === 'running' ? <Clock3 size={13} /> : null}{attemptStatus(attempt)}</span></td><td>{attempt.received_count} / {attempt.accepted_count}</td><td>{attempt.duplicate_count} / {attempt.revision_count}</td><td><ContentSummary attempt={attempt} /><PollCaptureLinks attemptId={attempt.attempt_id} /></td><td><details><summary>技术详情</summary><div>触发：{attempt.trigger_type}</div><div>耗时：{attempt.latency_ms === null ? '未知' : `${attempt.latency_ms} 毫秒`}</div>{attempt.error_code && <code>{attempt.error_code}</code>}</details></td></tr>)}</tbody></table></div>}
      {!attemptsQuery.isLoading && !attemptsQuery.isError && <div className="design-filter-row" aria-label="轮询记录分页"><button className="button secondary" type="button" disabled={offset === 0} onClick={() => navigatePage(Math.max(0, offset - PAGE_SIZE))}>上一页</button><span className="small">第 {Math.floor(offset / PAGE_SIZE) + 1} 页</span><button className="button secondary" type="button" disabled={(attemptsQuery.data?.length ?? 0) <= PAGE_SIZE} onClick={() => navigatePage(offset + PAGE_SIZE)}>下一页</button></div>}
    </section>}
  </div>
}
