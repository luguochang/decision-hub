import { useQuery } from '@tanstack/react-query'
import { riskPathErrorMessage, riskPathsApi, type PathTrainingReadiness, type PathTrialSummary } from '../api/riskPaths'
import './risk.css'

const states: Record<PathTrialSummary['state'], string> = {
  registered: '等待首个预测时点', running: '正在收集事前预测', paused: '试验已暂停',
  draining: '等待最后标签补齐', completed: '已按固定期限结项', failed: '试验执行失败',
}
function time(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
}
function number(value: string | null) {
  return value === null ? '暂无可用成绩' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(Number(value))
}
function percentage(value: string | null) {
  return value === null ? '资料不足' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1, style: 'percent' }).format(Number(value))
}
function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return <div><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

const trainingReasons: Record<string, string> = {
  baseline_training_windows_insufficient: '基线有效历史窗口未达到 60 个',
  candidate_training_windows_insufficient: '候选可用训练窗口未达到 60 个',
  low_volatility_group_insufficient: '低波动分组未达到 30 个窗口',
  high_volatility_group_insufficient: '高波动分组未达到 30 个窗口',
  frozen_fit_receipt_unavailable: '冻结拟合原件尚不可用，训练样本数量未确认',
  complete_24h_label_unavailable: '历史 24 小时路径标签不完整',
  outside_registered_history_window: '窗口在登记的训练历史范围之外',
  training_feature_snapshot_unavailable: '历史时点的训练特征不可用',
}
function TrainingStatus({ value }: { value: PathTrainingReadiness | undefined }) {
  if (!value) return <p className="risk-warning">训练就绪状态尚未核验。试验已运行或拟合任务已完成，都不代表模型已有可用训练样本。</p>
  const available = value.status === 'training_sufficient'
  const title = value.status === 'training_pending' ? '冻结训练尚未完成'
    : value.status === 'training_partial' ? '基线样本条件达到，候选分组仍不足'
      : available ? '冻结训练样本条件达到，效果仍待验证' : '训练历史不足，当前没有可用前瞻概率'
  const count = (item: number | null) => item === null ? '未核验' : String(item)
  return <section aria-label="冻结训练就绪状态">
    <h3>{title}</h3>
    <p className={available ? 'product-muted' : 'risk-warning'}>{value.frozen_fit ? '冻结拟合已保存。' : '尚无可核验的冻结模型。'}样本条件与当前特征、概率校准和预测效果分别核验，均不表示可用于自动交易。</p>
    <div className="risk-metrics">
      <Metric label="基线有效训练窗口" value={`${count(value.baseline_n)} / 60`} note={`已接受 ${count(value.accepted_windows)} 个；已排除 ${count(value.excluded_windows)} 个`} />
      <Metric label="候选有效训练窗口" value={`${count(value.candidate_training_n)} / 60`} note="历史特征合格后才进入条件分组" />
      <Metric label="低波动分组" value={`${count(value.low_group_n)} / 30`} note="每个分组独立核对最低样本数" />
      <Metric label="高波动分组" value={`${count(value.high_group_n)} / 30`} note="分组样本不足不能用零概率代替" />
    </div>
    {value.read_as_of && <p className="product-muted">冻结拟合的信息截止：{time(value.read_as_of)}；本次状态核对：{time(value.as_of)}（北京时间）。</p>}
    {value.available_history_start_at && value.available_history_end_at
      ? <p className="product-muted">拟合当时已落库的历史行情：{time(value.available_history_start_at)} 至 {time(value.available_history_end_at)}，共 {value.available_candle_minutes} 个分钟时点。这只是已知数据的覆盖范围，不表示连续、完整或可作为历史回测。</p>
      : <p className="product-muted">{value.frozen_fit ? '拟合当时没有已落库的可用历史行情范围。' : '拟合时的历史可用范围尚未核验。'}后采集的历史数据不会倒填为当时已知。</p>}
    {value.accepted_history_start_at && value.accepted_history_end_at
      ? <p className="product-muted">实际接受的训练路径范围：{time(value.accepted_history_start_at)} 至 {time(value.accepted_history_end_at)}。</p>
      : value.frozen_fit && <p className="product-muted">尚无实际接受的完整训练路径。</p>}
    {value.reasons.length > 0 && <p className="risk-warning">{value.reasons.map(reason => trainingReasons[reason] ?? '有训练资料条件尚未满足，具体原因见审计原件').join('；')}。</p>}
    {Object.entries(value.exclusion_counts).filter(([reason]) => trainingReasons[reason]).map(([reason, amount]) => <p className="product-muted" key={reason}>{trainingReasons[reason]}：{amount} 个。</p>)}
    {value.frozen_fit && !available && <p className="risk-warning">本轮模型已固定，不会随之后采集的数据自动重训；只等待 28 日结束不能补齐这次训练缺口。试验仍按原期限保留全部缺失并结项。</p>}
    <details className="risk-audit"><summary>训练原件与核验范围</summary><p>状态：{value.status}；拟合任务：{value.fit_job_state}。</p><p>模型哈希：{value.model_hash ?? '尚无原件'}；拟合原件哈希：{value.fit_result_hash ?? '尚无原件'}。</p><p>读取核验已存拟合内容哈希及资产、方法与时间绑定；行情范围来自同一拟合信息截止的库内元数据，本次未重新下载或重验全部行情原件。</p><p>原因：{value.reasons.join('、') || '样本数量门达到，效果仍未验证'}。</p></details>
  </section>
}

export function RiskPathScore({ scopeId }: { scopeId: string }) {
  const current = useQuery({ queryKey: ['risk-path-current', scopeId], queryFn: ({ signal }) => riskPathsApi.current(scopeId, signal), enabled: Boolean(scopeId), retry: false })
  const trial = current.data?.trial
  const trialId = trial?.trial_id ?? ''
  const training = trial?.training_readiness?.find(item => item.scope_id === scopeId)
  const scoreQuery = useQuery({ queryKey: ['risk-path-score', scopeId, trialId], queryFn: ({ signal }) => riskPathsApi.score(trialId, signal), enabled: Boolean(trialId), retry: false })
  const predictions = useQuery({ queryKey: ['risk-path-predictions', scopeId, trialId], queryFn: ({ signal }) => riskPathsApi.predictions(scopeId, trialId, signal), enabled: Boolean(trialId), retry: false })
  const refreshing = current.isFetching || scoreQuery.isFetching || predictions.isFetching
  const error = current.error ?? scoreQuery.error ?? predictions.error
  const score = scoreQuery.data?.report?.score
  const alarms = score?.alarm_metrics
  const ci = score?.ci
  const inference = score?.schema_version === 'path-shadow-score.v2' ? score.inference : null
  const legacy = score?.schema_version === 'path-shadow-score.v1'
  const targets = score?.targets.filter(item => item.scope_id === scopeId) ?? []
  const latest = predictions.data?.predictions[0]?.forecast
  const latestAt = latest?.cutoff_at
  const latestPair = predictions.data?.predictions.filter(item => item.forecast.cutoff_at === latestAt) ?? []
  const insufficient = latestPair.some(item => item.forecast.calibration_status === 'insufficient')
  const refresh = async () => {
    const updated = await current.refetch()
    if (updated.data?.trial?.trial_id === trialId && trialId) await Promise.all([scoreQuery.refetch(), predictions.refetch()])
  }
  return <section className="product-card" aria-label="事前预测验证">
    <div className="product-card-head"><div><h2>预测有没有实际帮助</h2><p className="product-muted">先保存事前预测，24 小时后核对真实路径。评价期间不用于自动交易。</p></div>
      <button className="button secondary" disabled={refreshing} onClick={() => void refresh()}>{refreshing ? '正在刷新预测成绩' : '刷新预测成绩'}</button></div>
    {error && <p className="product-notice danger" role="alert">刷新失败，当前预测成绩未确认。{riskPathErrorMessage(error)}已有记录仅供参照。</p>}
    {current.isPending && <p className="product-muted">正在核对当前资产的试验登记…</p>}
    {!current.isPending && !current.error && !trial && <><h3>尚未登记 28 日影子评价</h3><p className="product-muted">目前没有可核验的事前预测成绩。完成数据与方法准入后，试验从未来时点开始记录，不能用回填预测补成绩。</p></>}
    {trial && <>
      <p><span className="risk-badge risk-unknown">{training?.status === 'training_unavailable' && trial.state === 'running' ? '按固定期限记录试验，训练不可用' : states[trial.state]}</span> <span className="risk-warning">当前不具备晋级资格</span></p>
      <p className="product-muted">预测期：{time(trial.first_cutoff_at)} 至 {time(trial.last_cutoff_at)}（北京时间）；最后路径截止 {time(trial.last_window_end_at)}，标签补齐期限 {time(trial.label_deadline_at)}。</p>
      {trial.deadline_elapsed && !trial.report_available && <p className="product-notice danger">固定结项期限已到，结项回执尚未生成。不能将此状态视为已完成验证。</p>}
      {trial.state === 'paused' && <p className="risk-warning">暂停期间错过的事前时点会保留在全量分母中；固定结项期限不会延长。</p>}
      <TrainingStatus value={training} />
      <div className="risk-metrics">
        <Metric label="已保存试验预测记录" value={`${trial.published_prediction_count} / ${trial.expected_prediction_count}`} note={`尚待未来时点 ${trial.future_prediction_count} 条；已错过 ${trial.missed_prediction_count} 条；已保存不等于已有可用概率`} />
        <Metric label="完整真实路径标签" value={`${trial.complete_label_count} / ${trial.expected_slot_count}`} note={`尚未成熟 ${trial.pending_label_count} 个；成熟但未补齐 ${trial.unavailable_mature_label_count} 个`} />
        <Metric label="独立日区块" value={score ? `${score.independent_paired_day_blocks} / 100` : '结项后核对'} note="两资产同一世界标准日只计一个区块；28 天不足以证明预测优势" />
        <Metric label="系统预警命中占比" value={alarms ? percentage(alarms.precision) : '结项后核对'} note="按成熟事件匹配；系统命中不代表用户已看到提醒" />
      </div>
      <p className="product-muted">上述分母覆盖本试验全部 {trial.scope_ids.length} 个资产，每个时点同时保存基线与候选。下方只展示当前选择资产。</p>
      {trial.job_error_count > 0 && <p className="risk-warning">有 {trial.job_error_count} 个任务记录了未完成或异常原因，相关缺失会保留在评价中。</p>}
      {!scoreQuery.isPending && !score && <p className="product-muted">尚无正式结项成绩。固定期限后即使样本不足也会结项，并记录全部缺失。</p>}
      {insufficient && <p className="risk-warning">最新预测的训练历史不足，尚未给出可用事件概率。基线至少需要 60 个有效历史窗口，候选还需要所在分组至少 30 个窗口。</p>}
      {latest && !insufficient && <p className="product-muted">最新事前记录：{time(latest.cutoff_at)}。事件频率仍为未校准试验结果，不能作为可靠交易胜率。</p>}
      {score && <>
        <h3>本轮结项：证据不足，不能晋级</h3>
        <p className="product-muted">完整标签 {score.complete_label_count}，部分标签 {score.partial_label_count}，没有标签 {score.unavailable_label_count}；未发布预测 {score.unpublished_prediction_count}，迟到预测 {score.late_prediction_count}。缺失与无效数据不会从总样本中删去。</p>
        <p className="product-muted">概率误差分数越低越好。基线与候选采用同一对照样本；误差减少为正仅表示本轮样本更好，不代表交易胜率或晋级。</p>
        {legacy && <p className="product-muted">此历史方法的置信区间尚未登记，按原回执保留，不补造新方法成绩。</p>}
        {inference && !ci && <p className="risk-warning">固定 95% 区间方法已登记，但本轮配对样本、独立日或事件匹配资料不足，尚未生成区间。完整配对 {inference.complete_pairs} / {inference.expected_pairs}，独立日 {inference.independent_day_blocks} / 100，独立事件区块 {inference.event_blocks ?? '尚未核验'} / 30。</p>}
        {ci && <div className="risk-monitor-row"><div><h3>主要目标：候选相对基线的误差减少</h3><p>固定 95% 日区块区间：{number(ci.lower)} 至 {number(ci.upper)}；日均误差减少 {number(ci.point_estimate)}。</p><p className="product-muted">同一天的两资产一起重采样，每个独立日权重相同。{Number(ci.lower) <= 0 && Number(ci.upper) >= 0 ? '区间包含零，本轮不能确认候选优于基线。' : Number(ci.upper) < 0 ? '本轮区间显示候选误差较大。' : '本轮对照显示误差减少，仍需独立验证。'}不表示事件概率已校准，也不授予晋级资格。</p></div></div>}
        {alarms && <section aria-label="系统预测预警成绩"><h3>系统预测预警的实际匹配</h3>
          <p className="product-muted">仅评价候选方法的多头 2% 不利路径主目标。显示与用户曝光单独核验，下面不是用户实际获益或避险成功率。</p>
          <p>系统命中占比：{percentage(alarms.precision)}；事件覆盖占比：{percentage(alarms.recall)}；每资产日误报次数：{number(alarms.false_episodes_per_asset_day)}。</p>
          {alarms.lead_time ? <p>匹配 {alarms.lead_time.matched_count} 个事件，保守最短提前 {number(alarms.lead_time.minimum_seconds)} 秒，中位提前 {number(alarms.lead_time.median_seconds)} 秒；行情时间分辨率为 60 秒。</p> : <p className="product-muted">尚无可核验的保守提前量。</p>}
          {alarms.status !== 'evaluated' && <p className="risk-warning">成熟预警、完整标签或匹配原件仍不足，缺失资料不能按零误报或全部命中处理。</p>}
        </section>}
        <div className="risk-research">{targets.map(item => <div className="risk-monitor-row" key={`${item.direction}:${item.threshold}`}>
          <div><strong>{item.direction === 'long' ? '持有多头的不利路径' : '持有空头的不利路径'}达到 {Number(item.threshold) * 100}%</strong><p className="product-muted">配对有效样本 {item.paired_scored} / {item.expected_slots}；独立日 {item.independent_day_blocks}</p></div>
          <div><p>基线误差：{number(item.paired_baseline_brier)}</p><p>候选误差：{number(item.paired_candidate_brier)}</p><small>相对基线误差减少：{number(item.paired_delta)}</small></div>
        </div>)}</div>
      </>}
      <details className="risk-audit"><summary>试验方法与审计</summary><p>试验标识：{trial.trial_id}</p><p>方法版本：{trial.protocol_version}；结项成绩回执仅在读取时核验已存内容哈希，不表示本次重新下载或复核所有行情原件。</p><p>晋级要求包括至少 100 个独立日区块和 30 个可匹配事件区块；试验期限 28 日保持固定，单轮不足以证明优势。</p>{inference && <p>区间方法：{inference.protocol}；种子 {inference.seed}，重采样 {inference.replicates} 次，方法哈希 {inference.protocol_hash}。</p>}</details>
    </>}
  </section>
}
