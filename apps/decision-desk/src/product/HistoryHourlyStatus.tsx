import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { ApiRequestError } from '../api/client'
import { readHistorySchedule, type HistoryHourlyStatusV2 } from '../api/historySchedule'
import type { HistoryProductStatusV2 } from '../api/historyStatus'
import { historyForecastStatusV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { HistoryForecastRecord } from './HistoryForecastRecord'

type HourlyScope = HistoryHourlyStatusV2['scopes'][number]
type Forecast = HistoryProductStatusV2['forecast']
const stateNames: Record<HourlyScope['state'], string> = {
  not_configured: '尚未配置小时流程', not_due: '等待首次小时记录', reserved: '本小时已预留，结果尚待确认',
  blocked: '该小时首次执行时条件不足', committed_verified: '本小时已保存，提交时核验通过',
  committed_readback_unconfirmed: '已保存，回执尚待确认', unconfirmed: '本小时状态尚未确认',
}
const time = (value: string | null) => value ? `${new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
}).format(new Date(value))} UTC` : '尚无记录'
const days = (value: number | null) => value === null ? '未取得' : `${value} 天`

function authenticationRejected(error: unknown) {
  return error instanceof ApiRequestError && [401, 403].includes(error.status ?? 0)
}
function readFailure(error: unknown) {
  if (error instanceof ApiRequestError) {
    if (authenticationRejected(error)) return '本机验证已失效，小时记录已隐藏。请重新验证并刷新历史状态。'
    if (error.status === 503) return '小时流程服务尚未配置；当前没有已确认的自动预测运行状态。'
    if (error.status === 404) return '当前安装版本尚未提供小时流程状态接口。'
    if (error.status === 409) return '小时记录或保护回执无法核对，当前执行状态尚未确认。'
    if (error.kind === 'timeout') return '小时快状态读取超时，当前执行情况尚未确认。'
    if (error.kind === 'network') return '无法连接小时流程服务，当前执行情况尚未确认。'
  }
  return '返回的小时状态或资产身份无法核对，当前不展示预测记录。'
}

function forecastFromMetadata(row: HourlyScope): Forecast | null {
  // A reserved/blocked id can be a planned id without a committed original.
  // The exact contract has already paired BTC/ETH, scope and record id syntax.
  if (!['committed_verified', 'committed_readback_unconfirmed', 'unconfirmed'].includes(row.state)
    || !row.forecast_id || !row.cutoff_at) return null
  const mapped: Forecast = { state: 'verification_required', forecast_id: row.forecast_id, scope_id: row.scope_id,
    cutoff_at: row.cutoff_at, threshold: null, baseline_n: null, conditional_n: null,
    probabilities: null,
    blocked_reason_label: '原件尚未在本次读取核验，需主动核对已保存记录。' }
  return historyForecastStatusV2Schema.parse(mapped)
}

/** Only fast metadata polls. Original records remain explicit reads in the existing component. */
export function HistoryHourlyStatus({ authRevision, ownerStatusStale, models, existingForecast }: {
  authRevision: number; ownerStatusStale: boolean; models: HistoryProductStatusV2['models'];
  existingForecast: Forecast;
}) {
  const query = useQuery({ queryKey: ['history-hourly-status-v2', authRevision],
    queryFn: ({ signal }) => readHistorySchedule(signal), enabled: !ownerStatusStale,
    retry: false, staleTime: 60_000, refetchOnWindowFocus: false,
    refetchInterval: state => authenticationRejected(state.state.error) ? false : 60_000,
    refetchIntervalInBackground: false })
  const status = authenticationRejected(query.error) ? undefined : query.data
  const stale = ownerStatusStale || query.isError
  return <section className="product-card history-section history-hourly-status" aria-label="两资产小时预测进度">
    <div className="history-hourly-heading"><div><h2>每小时预测进度</h2>
      <p>每分钟只刷新执行记录；每小时是否产生预测，取决于真实数据、模型与原件核验。</p></div>
      <button className="button secondary" disabled={query.isFetching || ownerStatusStale} onClick={() => void query.refetch()}><RefreshCw size={15} />刷新小时进度</button></div>
    {query.isPending && <p role="status">正在读取小时快状态…</p>}
    {ownerStatusStale && <p role="status">历史身份状态刷新失败，以下记录仅为上次快照，请先刷新历史状态。</p>}
    {query.isError && <p className="product-notice danger" role="alert">{status ? '小时状态刷新失败，以下为上次快照。' : ''}{readFailure(query.error)}</p>}
    {status && <>
      <p>{status.worker_configured ? '已配置每小时流程' : '小时流程尚未配置'} · 状态读取时刻 {time(status.observed_at)}{stale ? '（上次快照）' : ''}。</p>
      <p>配置状态不代表 Worker 在线；此接口没有心跳信息。请按每个资产最后记录核对执行情况。自动生产不重试未确认的提交。</p>
      <div className="history-hourly-assets">{status.scopes.map(row => {
        const model = models.find(item => item.scope_id === row.scope_id)
        const forecast = forecastFromMetadata(row)
        const alreadyListed = Boolean(forecast && existingForecast.forecast_id === forecast.forecast_id
          && existingForecast.scope_id === forecast.scope_id && existingForecast.cutoff_at
          && Date.parse(existingForecast.cutoff_at) === Date.parse(forecast.cutoff_at!))
        return <article className="history-hourly-asset" key={row.scope_id}>
          <h3>{row.asset_label}</h3><strong>{stateNames[row.state]}</strong>
          <p>该小时首次执行时的记录：{row.reason_label}之后的授权或数据变化不会改写这条历史原因。</p>
          {row.fixture_mode && <p className="history-proof">这是测试数据记录，不能作为真实市场预测。</p>}
          {row.fence_present && <p>有提交保护记录；状态未确认时不会自动重写本小时结果。</p>}
          <dl className="history-hourly-times"><div><dt>最近小时槽</dt><dd>{time(row.hour_at)}</dd></div><div><dt>观察截点</dt><dd>{time(row.cutoff_at)}</dd></div><div><dt>记录完成</dt><dd>{time(row.completed_at)}</dd></div></dl>
          {model && <p>当前模型有效共同样本 {days(model.joint_days)}；低 / 高波动组 {days(model.low_days)} / {days(model.high_days)}。
            {!model.count_gate_met ? '数量门尚未满足：需至少60天共同样本，低、高组各30天。' : '数量门已满足，仍需模型授权与原件核验。'}
            {model.state === 'not_fitted' || model.state === 'blocked_insufficient_samples' ? '模型尚未拟合。' : '模型登记不代表预测已经交付。'}</p>}
          {row.state === 'committed_verified' && <p>这里只确认当时保存及核验通过，尚未在本次状态读取核验概率。金融有效性与历史时点可用性尚未通过。</p>}
          {forecast && (alreadyListed ? <p>这条已保存记录在下方风险记录中列出，可主动核验原件后查看。</p> : <HistoryForecastRecord
            key={`${authRevision}:${forecast.forecast_id}:${forecast.scope_id}:${forecast.cutoff_at}`}
            forecast={forecast} authRevision={authRevision} statusStale={stale} />)}
          {!forecast && row.state === 'committed_verified' && <p>这条记录缺少可核对的预测身份或截点，当前不能读取概率。</p>}
          <details className="history-detail"><summary>查看小时记录身份与原因</summary><p>小时记录：<code>{row.slot_id ?? '尚无'}</code><br />预测记录：<code>{row.forecast_id ?? '尚无'}</code><br />原因代码：<code>{row.reason_code ?? '尚无'}</code><br />执行开始：{time(row.started_at)}<br />执行期限：{time(row.deadline_at)}</p></details>
        </article>
      })}</div>
    </>}
  </section>
}
