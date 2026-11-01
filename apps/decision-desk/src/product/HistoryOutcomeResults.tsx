import { useEffect, useRef, useState } from 'react'
import { ApiRequestError } from '../api/client'
import { readHistoryOutcomeStatus, readHistoryOutcomeScore, readHistoryOutcomeObservation,
  type HistoryOutcomeStatusV2, type HistoryOutcomeScoreReplyV2,
  type HistoryOutcomeObservationReplyV2 } from '../api/historyOutcome'
import type { OutcomeOriginalRefV2 } from '../../../../packages/contracts_ts/src/generated/history_outcome_v2'
import './HistoryStatusPage.css'

const publications = { pending: '等待发布', on_time: '按时发布', late: '迟到发布', off_schedule: '非计划发布',
  blocked: '受阻', failed: '失败', abstained: '主动不发布', missing: '缺失', unconfirmed: '尚未确认' }
const labels = { pending_maturity: '尚未到期', verified: '完整核验', partial: '部分缺失', invalid: '无效',
  missing_at_deadline: '截止仍缺失', late_diagnostic: '迟到诊断' }
const exclusions: Record<string, string> = { publication_late: '发布迟到', publication_missing: '发布缺失',
  publication_blocked: '发布受阻', label_partial: '路径缺失', label_invalid: '路径无效',
  label_missing_at_deadline: '到截止时间仍缺失', label_late_diagnostic: '仅供迟到诊断', fixture: '模拟资料' }
const clock = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC',
}).format(new Date(value)) + ' UTC'
const number = (value: string | null) => value === null ? '尚无可评分配对' : value
const failure = (error: unknown) => error instanceof ApiRequestError && error.status === 403
  ? '当前身份未获此评估的读取权限，请先完成身份验证。'
  : error instanceof ApiRequestError && error.status === 503
    ? '评估服务尚未配置，当前没有可验收结果。'
    : '读取未完成，尚未确认原件。请刷新状态后再明确读取。'

/** Read-only results; the trusted configuration supplies the protocol identity. */
export function HistoryOutcomeResults({ protocolId, authRevision = 0 }: { protocolId: string | null; authRevision?: number }) {
  const [offset, setOffset] = useState(0)
  const [revision, setRevision] = useState(0)
  const [status, setStatus] = useState<HistoryOutcomeStatusV2 | null>(null)
  const [statusError, setStatusError] = useState('')
  const [statusLoading, setStatusLoading] = useState(false)
  const [score, setScore] = useState<HistoryOutcomeScoreReplyV2 | null>(null)
  const [observation, setObservation] = useState<HistoryOutcomeObservationReplyV2 | null>(null)
  const [readError, setReadError] = useState('')
  const [reading, setReading] = useState(false)
  const generation = useRef(0)
  const fullController = useRef<AbortController | null>(null)
  const cancel = () => {
    generation.current++
    fullController.current?.abort()
    fullController.current = null
    setReading(false)
  }
  useEffect(() => { setOffset(0) }, [protocolId, authRevision])
  useEffect(() => {
    generation.current++
    const current = generation.current
    const controller = new AbortController()
    fullController.current?.abort(); fullController.current = null
    setReading(false); setScore(null); setObservation(null); setReadError('')
    setStatus(null); setStatusError(''); setStatusLoading(Boolean(protocolId))
    if (protocolId) {
      void readHistoryOutcomeStatus(protocolId, offset, controller.signal).then(value => {
        if (!controller.signal.aborted && generation.current === current) setStatus(value)
      }).catch(error => {
        if (!controller.signal.aborted && generation.current === current) setStatusError(failure(error))
      }).finally(() => {
        if (!controller.signal.aborted && generation.current === current) setStatusLoading(false)
      })
    }
    return () => { controller.abort(); generation.current++; fullController.current?.abort() }
  }, [protocolId, authRevision, offset, revision])

  const readOriginal = async (kind: 'score' | 'observation', original: OutcomeOriginalRefV2) => {
    if (!protocolId || !status || reading) return
    const controller = new AbortController()
    fullController.current?.abort(); fullController.current = controller
    const current = ++generation.current
    setReading(true); setReadError(''); setObservation(null)
    if (kind === 'score') setScore(null)
    try {
      if (kind === 'score') {
        const value = await readHistoryOutcomeScore(protocolId, original, controller.signal)
        if (!controller.signal.aborted && current === generation.current) setScore(value)
      } else {
        const value = await readHistoryOutcomeObservation(protocolId, original, controller.signal)
        if (!controller.signal.aborted && current === generation.current) setObservation(value)
      }
    } catch (error) {
      if (!controller.signal.aborted && current === generation.current) {
        setReadError(failure(error)); setScore(null); setObservation(null)
        if (error instanceof ApiRequestError && error.status === 403) setStatus(null)
      }
    } finally {
      if (current === generation.current) { setReading(false); fullController.current = null }
    }
  }

  return <section className="history-status-page history-section" aria-label="预测结果验收">
    <div className="history-hourly-heading"><div><h2>预测是否产生了价值</h2>
      <p>固定观察比特币、以太坊各 24 个小时机会。受阻、失败、缺失和迟到都保留在分母中。</p></div>
      <button disabled={!protocolId || statusLoading} onClick={() => { cancel(); setRevision(value => value + 1) }}>刷新评估状态</button></div>
    {!protocolId && <p role="status">尚未配置可读取的评估。评估范围由服务端登记。</p>}
    {statusLoading && <p role="status">正在读取评估状态…</p>}
    {statusError && <p role="alert">{statusError}</p>}
    {readError && <p role="alert">{readError}</p>}
    {status && <>
      <p>状态截至 {clock(status.actual_as_of)}。此摘要尚未重新核验完整原件。</p>
      <dl className="history-risk-values">
        <div><dt>全部计划机会</dt><dd>{status.expected_total}</dd></div>
        <div><dt>按时发布 / 全部机会</dt><dd>{status.publication_counts.on_time} / 48</dd></div>
        <div><dt>完整核验路径 / 已到期</dt><dd>{status.label_counts.verified} / {status.label_due_count}</dd></div>
        <div><dt>同一配对集合 / 已到期</dt><dd>{status.paired_count} / {status.label_due_count}</dd></div>
      </dl>
      <div className="history-summary"><div><h3>发布情况</h3><ul>{Object.entries(publications).map(([key, label]) =>
        <li key={key}>{label}：{status.publication_counts[key as keyof typeof publications]}</li>)}</ul></div>
        <div><h3>后续 24 小时路径</h3><ul>{Object.entries(labels).map(([key, label]) =>
          <li key={key}>{label}：{status.label_counts[key as keyof typeof labels]}</li>)}</ul></div>
        <div><h3>原件核验</h3><p>评分只比较同一批可评分配对。样本为零时保留空值。</p>
          <button disabled={!status.latest_score || reading} onClick={() => { if (status.latest_score) void readOriginal('score', status.latest_score) }}>读取评分原件</button></div></div>
      <div className="history-section"><h3>每个机会都可追溯</h3>
        <table className="history-table"><thead><tr><th>计划时间</th><th>资产</th><th>发布</th><th>路径</th><th>已尝试</th></tr></thead>
          <tbody>{status.opportunities.map(item => <tr key={item.opportunity_id}><td>{clock(item.hour_at)}</td><td>{item.asset_label}</td>
            <td>{publications[item.publication_state]}</td><td>{labels[item.label_state]}</td><td>{item.attempts} / 4</td></tr>)}</tbody></table>
        <div className="history-pagination"><button disabled={offset === 0 || statusLoading || reading} onClick={() => setOffset(0)}>上一页机会</button>
          <span>第 {offset + 1}—{offset + status.opportunities.length} 条 / 48</span>
          <button disabled={offset >= 30 || statusLoading || reading} onClick={() => setOffset(30)}>下一页机会</button></div></div>
    </>}
    {reading && <p role="status">正在核验完整原件，最长等待 120 秒。<button onClick={cancel}>取消原件读取</button></p>}
    {score && status && <div className="history-section" aria-label="已核验评分">
      <h3>本次读取已核验评分原件</h3><p>评分截至 {clock(score.score.actual_as_of)}，配对 {score.score.paired_count} / 48。
        Brier 越小越好；配对改善为基线分数减条件分数，正值表示改善。</p>
      <table className="history-table"><thead><tr><th>风险目标</th><th>同一配对数</th><th>基线 Brier</th><th>条件 Brier</th><th>配对改善</th></tr></thead>
        <tbody>{score.score.targets.map(target => <tr key={`${target.direction}:${target.threshold}`}>
          <td>{target.direction === 'long' ? '多头下行风险' : '空头上行风险'} {Number(target.threshold) * 100}%</td>
          <td>{target.n_pair}</td><td>{number(target.baseline_brier)}</td><td>{number(target.conditional_brier)}</td><td>{number(target.paired_gain)}</td></tr>)}</tbody></table>
      <details className="history-detail"><summary>查看配对与排除原因</summary>
        <ul>{score.score.cases.map(item => <li key={item.opportunity_id}>
          {clock(item.hour_at)} · {item.scope_id.includes('BTC-USDT') ? '比特币' : '以太坊'} · {item.eligible_pair ? '已纳入配对' : '未纳入配对'}
          {item.exclusions.length > 0 && `：${item.exclusions.map(code => exclusions[code] || '未通过评分准入，详见完整原件').join('、')}`}
          {item.observation && <button disabled={reading} onClick={() => { if (item.observation) void readOriginal('observation', item.observation) }}>读取该路径原件</button>}
        </li>)}</ul></details>
    </div>}
    {observation && status && <div className="history-section"><h3>本次读取已核验路径原件</h3>
      <p>{labels[observation.observation.classification]}：{observation.observation.observed_count} / 1440 分钟。
        观察窗 {clock(observation.observation.window_start_at)} 至 {clock(observation.observation.window_end_at)}。</p>
      <p>路径原件主体不声明接纳时间；评分资格还需独立接纳回执。</p></div>}
    <p className="history-proof">当前未授予金融决策、历史时点有效性或自动晋级资格。小时观察窗相互重叠，尚无已登记的置信区间。</p>
  </section>
}
