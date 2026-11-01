import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowLeft, RefreshCw } from 'lucide-react'
import { ApiRequestError } from '../api/client'
import { historyApi, HISTORY_SCOPES, type HistoryProductStatusV2 } from '../api/historyStatus'
import type { AssetNavigate } from './assetFormat'
import { HistoryForecastSelector } from './HistoryForecastSelector'
import { HistoryHourlyStatus } from './HistoryHourlyStatus'
import { HistoryOutcomeEntry } from './HistoryOutcomeEntry'
import './HistoryStatusPage.css'

const time = (value: string | null) => value ? `${new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
}).format(new Date(value))} UTC` : '未取得'
const days = (value: number | null) => value === null ? '未取得' : `${value} 天`
const scopeName = (scope: string) => HISTORY_SCOPES[scope as keyof typeof HISTORY_SCOPES] ?? '资产待核对'
const collectionNames: Record<NonNullable<HistoryProductStatusV2['collection']>['state'], string> = {
  active: '采集进行中', paused: '采集已暂停', expired: '采集期限已结束', completed: '采集作业已结束', partial: '仅完成部分采集',
}
const jobNames: Record<NonNullable<HistoryProductStatusV2['collection']>['jobs']['items'][number]['state'], string> = {
  pending: '等待采集', retry: '等待补采', running: '采集中', completed: '作业已完成', partial: '部分采集', failed: '采集失败', unknown: '状态未确认',
}
const modelNames: Record<HistoryProductStatusV2['models'][number]['state'], string> = {
  not_fitted: '尚未拟合', blocked_insufficient_samples: '有效样本不足', available_unqualified: '模型已登记，仍需验证', verification_required: '模型原件待核验',
}
const qualityNames: Record<HistoryProductStatusV2['data']['scopes'][number]['quality'], string> = {
  unavailable: '覆盖未取得', saved_metadata: '已保存的覆盖记录', last_verified_original_snapshot: '上次原件核验快照',
}

function authenticationRejected(error: unknown) {
  return error instanceof ApiRequestError && error.kind === 'http' && [401, 403].includes(error.status ?? 0)
}
function failureText(error: unknown) {
  if (error instanceof ApiRequestError) {
    if (error.status === 503) return '历史能力服务尚未配置或部署。当前没有可验收的自动历史预测流程。'
    if (authenticationRejected(error)) return '需要独立的历史能力本机验证。已有研究或方法对照的登录不能代替这次验证。'
    if (error.status === 404) return '当前安装版本没有历史能力接口，尚未交付。'
    if (error.kind === 'timeout') return '历史状态读取超时，尚未确认当前数据与预测。'
    if (error.kind === 'network') return '无法连接历史能力服务，请检查本机运行状态。'
  }
  if (error instanceof Error && error.message === 'history_owner_session_expired') return '本机验证已失效，请重新验证。'
  return '返回的历史状态无法核对，当前结果尚未确认。'
}

function PageButtons({ total, offset, count, limit, onChange, label, pending }: {
  total: number; offset: number; count: number; limit: number; onChange: (offset: number) => void; label: string; pending: boolean;
}) {
  return <nav className="history-pagination" aria-label={label}>
    <span>{count ? `本页 ${offset + 1}–${offset + count}，共 ${total} 项` : total ? `本页暂无记录，共 ${total} 项` : '尚无记录'}</span>
    <button className="button secondary" disabled={pending || offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>上一页</button>
    <button className="button secondary" disabled={pending || offset + limit >= total} onClick={() => onChange(offset + limit)}>下一页</button>
  </nav>
}

export function HistoryStatusPage({ onNavigate }: { onNavigate: AssetNavigate }) {
  const queryClient = useQueryClient()
  const entryParams = new URLSearchParams(window.location.hash.split('?')[1] ?? '')
  const entryScope = entryParams.get('scope')
  const focusedScope = entryParams.get('focus') === 'hourly' && entryScope
    && Object.prototype.hasOwnProperty.call(HISTORY_SCOPES, entryScope) ? entryScope : null
  const returnHome = entryParams.get('back') === '#/home'
  const [jobOffset, setJobOffset] = useState(0)
  const [excludedOffset, setExcludedOffset] = useState(0)
  const [authRevision, setAuthRevision] = useState(0)
  const [pairingToken, setPairingToken] = useState('')
  const [pairing, setPairing] = useState(false)
  const [pairError, setPairError] = useState<string | null>(null)
  const [pairedUntil, setPairedUntil] = useState<string | null>(null)
  const query = useQuery({ queryKey: ['history-product-status-v2', jobOffset, excludedOffset, authRevision],
    queryFn: ({ signal }) => historyApi.status({ jobOffset, excludedOffset }, signal),
    retry: false, refetchOnWindowFocus: false })
  const unavailable = query.error instanceof ApiRequestError && [404, 503].includes(query.error.status ?? 0)
  const status = authenticationRejected(query.error) ? undefined : query.data
  const stale = Boolean(status && query.isError)
  const collection = status?.collection
  const forecast = status?.forecast
  const pair = async () => {
    if (pairing || pairingToken.length < 32 || unavailable) return
    setPairing(true); setPairError(null)
    try {
      const session = await historyApi.pairOwner(pairingToken)
      await queryClient.cancelQueries({ queryKey: ['home-history-hourly-status-v2'] })
      queryClient.removeQueries({ queryKey: ['home-history-hourly-status-v2'] })
      // CSRF is not persisted or reused for another capability. No mutation is exposed here.
      setPairedUntil(session.expires_at); setPairingToken(''); setAuthRevision(value => value + 1)
    } catch (error) { setPairError(failureText(error)); setPairingToken('') }
    finally { setPairing(false) }
  }
  return <div className="content-wrap product-page history-status-page">
    <button className="product-link" onClick={() => onNavigate(returnHome ? 'home' : 'management')}><ArrowLeft size={15} />{returnHome ? '返回资产风险首页' : '返回管理与审计'}</button>
    <div className="product-heading"><div><span className="product-kicker">历史数据 · 样本 · 风险估计</span><h1>历史数据与预测能力</h1>
      <p>先核对数据和有效样本，再看模型与预测是否形成可验证的结果。</p></div>
      <button className="button secondary" disabled={query.isFetching || pairing} onClick={() => void query.refetch()}><RefreshCw size={15} />刷新状态</button></div>
    {focusedScope && <p className="product-notice">从首页进入：{scopeName(focusedScope)}的小时风险记录。请沿用下方独立本机验证；恢复后返回首页，再主动核验所选资产原件。此入口不改变原有授权或执行时点。</p>}
    <p><button className="product-link" onClick={() => onNavigate('data-jobs')}>查看原有小时行情与数据更新记录</button></p>
    {query.isPending && <p role="status">正在读取历史能力状态…</p>}
    {query.isError && <div className="product-notice danger" role="alert"><div><strong>{stale ? '刷新失败，以下为上次读取的快照' : '历史能力尚未确认'}</strong><p>{failureText(query.error)}</p></div></div>}
    {!status && !query.isPending && <section className="product-card history-section"><h2>独立本机验证</h2>
      <p>使用可信本机启动器提供的历史能力配对令牌。目前启动器尚未配置时，需要先完成服务配置，不能使用研究会话或方法对照凭据代替。</p>
      <form className="history-pair" onSubmit={event => { event.preventDefault(); void pair() }}>
        <label>历史能力配对令牌<input type="password" autoComplete="off" value={pairingToken} maxLength={2048}
          disabled={pairing || unavailable} onChange={event => setPairingToken(event.target.value)} /></label>
        <button className="button secondary" type="submit" disabled={pairing || unavailable || pairingToken.length < 32}>{pairing ? '正在验证…' : '验证本机身份'}</button>
      </form>{pairError && <p role="alert">{pairError}</p>}{pairedUntil && <p>本机验证有效至 {time(pairedUntil)}；仍须取得有效状态回执。</p>}
    </section>}
    {status && <>
      <p className="history-proof">本次仅展示已保存的状态；原件尚未在本次读取重新核验，历史资料尚未证明在对应市场时点已可获知，金融有效性尚未通过。{status.user_message}</p>
      <p>状态读取时刻：{time(status.as_of)}{stale ? '（上次快照）' : ''}</p>
      <div className="history-summary history-section">
        <section className="product-card"><h2>数据</h2><strong>{collection ? collectionNames[collection.state] : '尚无当前采集记录'}</strong><p>固定观察范围每个资产 {status.data.cutoff_count} 天；覆盖数量和原件可用性分别核对。</p></section>
        <section className="product-card"><h2>模型</h2><strong>{status.models.some(row => row.state === 'verification_required') ? '已有模型原件待核验' : status.models.some(row => row.state === 'available_unqualified') ? '已有模型登记，仍需验证' : '样本或模型尚未就绪'}</strong><p>样本数量达到要求仍需验证方法，不能直接转为可用预测。</p></section>
        <section className="product-card"><h2>预测</h2><strong>{forecast?.state === 'available_unqualified' ? '已保存未验证的风险估计' : '尚无可交付预测'}</strong><p>{forecast?.blocked_reason_label || '自动生成与完整验证流程尚未确认。'}</p></section>
      </div>
      <section className="product-card history-section"><h2>原件与首次获知覆盖</h2><p>{status.data.proof_label} · 采集计划快照时点：{time(status.data.known_asof)}。计划时计数保留采集前状态；模型有效样本来自新冻结数据集，按下方样本与模型记录核对。</p>
        <div className="table-wrap"><table className="history-table"><thead><tr><th>资产</th><th>采集计划时完整原件</th><th>新冻结可用窗口</th><th>计划时缺口</th><th>新冻结排除窗口</th><th>记录依据</th></tr></thead><tbody>
          {status.data.scopes.map(row => <tr key={row.scope_id}><th>{row.asset_label}</th><td>{days(row.raw_complete_days)}</td><td>{days(row.first_known_complete_days)}</td><td>{days(row.missing_days)}</td><td>{days(row.excluded_days)}</td><td>{qualityNames[row.quality]}</td></tr>)}
        </tbody></table></div><p>首次获知记录完整，不代表这些资料在历史预测时点已经可用。小时行情作业也不能替代这份分钟原件覆盖。</p>
      </section>
      <section className="product-card history-section"><h2>有效样本与模型</h2><div className="table-wrap"><table className="history-table"><thead><tr><th>资产</th><th>当前状态</th><th>共同有效天数</th><th>低位 / 高位样本</th><th>数量门</th><th>方法验证</th></tr></thead><tbody>
        {status.models.map(row => <tr key={row.scope_id}><th>{scopeName(row.scope_id)}</th><td>{modelNames[row.state]}{row.blocked_reason_label && <p>{row.blocked_reason_label}</p>}</td><td>{days(row.joint_days)}</td><td>{days(row.low_days)} / {days(row.high_days)}</td><td>{row.count_gate_met ? '数量要求已满足' : '尚未满足'}</td><td>{row.method_qualified ? '记录为已验证' : '尚未通过'}</td></tr>)}
      </tbody></table></div>
        <p>{status.actions.can_refresh ? '后台已授权单次生产核验；仍须通过真实模型、最新分钟原件和时点条件。' : '尚未配置可用的生产权限。'}本页只读取状态，不发起生产；小时流程的实际进度见下方。</p>
        <details className="history-detail"><summary>查看模型记录身份与记录时间</summary>{status.models.map(row => <p key={row.scope_id}>{scopeName(row.scope_id)} · 模型记录：<code>{row.model_id ?? '尚无'}</code><br />模型首次记录：{time(row.model_known_at)}<br />原件标识：<code>{row.model_hash ?? '尚无'}</code></p>)}</details>
      </section>
      <HistoryHourlyStatus authRevision={authRevision} ownerStatusStale={stale} models={status.models} existingForecast={status.forecast} />
      <HistoryForecastSelector fallbackForecast={status.forecast} authRevision={authRevision} statusStale={stale} statusAsOf={status.as_of} />
      <HistoryOutcomeEntry authRevision={authRevision} ownerStatusStale={stale} />
      {collection && <section className="product-card history-section"><h2>当前采集进度</h2>
        <p>{collectionNames[collection.state]} · 已使用 {collection.attempts} / {collection.budget} 次请求，剩余 {collection.remaining_attempts} 次。期限：{time(collection.deadline_at)}。</p>
        {collection.paused_reason_label && <p role="status">{collection.paused_reason_label}</p>}
        <div className="table-wrap"><table className="history-table"><thead><tr><th>资产</th><th>状态</th><th>已用请求</th><th>已采页数 / 上限</th></tr></thead><tbody>{collection.jobs.items.map(job => <tr key={job.job_id}><th>{scopeName(job.scope_id)}</th><td>{jobNames[job.state]}</td><td>{job.attempts}</td><td>{job.page_count} / {job.max_pages}</td></tr>)}</tbody></table></div>
        <PageButtons label="采集作业分页" total={collection.jobs.total} offset={collection.jobs.offset} count={collection.jobs.items.length}
          limit={collection.jobs.limit} pending={query.isFetching} onChange={setJobOffset} />
      </section>}
      <section className="product-card history-section"><h2>排除窗口</h2><p>这些窗口不进入有效样本。保留原因，避免缺失或事后取得的资料被当作预测依据。</p>
        <div className="table-wrap"><table className="history-table"><thead><tr><th>资产</th><th>观察截点</th><th>排除原因</th><th>缺失分钟</th></tr></thead><tbody>{status.excluded_windows.items.map((row, index) => <tr key={`${row.scope_id}:${row.cutoff_at}:${index}`}><th>{scopeName(row.scope_id)}</th><td>{time(row.cutoff_at)}</td><td>{row.reason_label}</td><td>{row.missing_minutes === null ? '未取得' : row.missing_minutes}</td></tr>)}</tbody></table></div>
        <PageButtons label="排除窗口分页" total={status.excluded_windows.total} offset={status.excluded_windows.offset} count={status.excluded_windows.items.length}
          limit={status.excluded_windows.limit} pending={query.isFetching} onChange={setExcludedOffset} />
      </section>
    </>}
  </div>
}
