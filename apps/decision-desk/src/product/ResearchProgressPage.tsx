import { projectResearchProgress } from './researchProgressProjection'
import { AssetBackLink, assetBackLabel, assetTime } from './assetFormat'
import { useQuery } from '@tanstack/react-query'
import { ResearchFocus, researchTitle } from './ResearchContext'
export { researchTitle } from './ResearchContext'
import { api } from '../api/client'
import { currentPreparationWaiting, preparationWaitingText, requirementNames, technicalSummaryName } from '../research/technicalPresentation'
import type { ResearchRunView } from '../api/types'
import { useRunCommand } from '../research/useRunCommand'
import { RunCommandRecovery } from '../research/RunCommandRecovery'

const OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'

const nativeCauseLabel = (code: string) => code === 'skill_callback_transport_timeout' ? '技能回调传输等待超时' : '原生研究执行未完成'

export const isResearchActive = (status: string) => ['admitted', 'running', 'queued', 'researching', 'retry_wait'].includes(status)
export function researchTaskScene(status: string, reportAvailable: boolean) {
  if (['failed', 'cancelled'].includes(status)) return 'failed'
  if (['admitted', 'queued', 'running', 'researching', 'retry_wait'].includes(status)) return 'queued'
  return reportAvailable ? 'report' : 'queued'
}
const stages: Record<ResearchRunView['stage'], string> = {
  admission: '正在登记研究', planning: '正在制定研究计划', acquiring_evidence: '正在收集并核对证据', assessing_sufficiency: '正在检查证据缺口', synthesis: '正在整理研究结论', gate: '正在检查报告质量', monitoring: '正在安排后续复核', done: '正在核验并保存研究结果',
}
export function ResearchProgressPage({ runId, back, onNavigate }: { runId?: string; back?: string | null; onNavigate: (scene: string, params?: Record<string, string | undefined>) => void }) {
  const query = useQuery({
    queryKey: ['research-progress-basic', runId], enabled: Boolean(runId), retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
    queryFn: async () => {
      const value = await api.run(runId!)
      if (value.run_id !== runId || value.strategy_version !== 'research.v1') throw new Error('任务进度返回的研究身份或研究类型无法核对。')
      return value
    },
    refetchInterval: state => state.state.status === 'error' || (state.state.data && !isResearchActive(state.state.data.status)) ? false : 3_000,
  })
  const details = useQuery({
    queryKey: ['research-progress-details', runId], enabled: false, retry: false,
    refetchInterval: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
    queryFn: async () => {
      const value = await api.researchRun(runId!)
      if (value.run.run_id !== runId) throw new Error('后台材料返回的研究身份无法核对。')
      return value
    },
  })
  const validBack = assetBackLabel(back ?? null) ? back : null
  const command = useRunCommand(runId, OWNER_ID, receipt => {
    void query.refetch()
    if (receipt.result.target_run_id) onNavigate('queued', { run: receipt.result.target_run_id, ...(validBack ? { back: validBack } : {}) })
  })
  const run = query.data && query.data.run_id === runId && query.data.strategy_version === 'research.v1' ? query.data : undefined
  const detail = details.data?.run.run_id === runId ? details.data : undefined
  const detailMatchesBasic = Boolean(detail && run && !details.isError && !query.isError && Date.parse(detail.run.updated_at) === Date.parse(run.updated_at))
  const progress = detail ? projectResearchProgress(detail, detailMatchesBasic) : null
  const active = run && isResearchActive(run.status)
  const errorStatusKnown = Boolean(run && query.isSuccess && !query.isError && !details.isError && detail && Date.parse(run.updated_at) >= Date.parse(detail.run.updated_at))
  const canStop = Boolean(active && !query.isError && run.run_id === runId && !command.busy && !command.locked)
  const failed = run && ['failed', 'cancelled'].includes(run.status)
  const showFailureCoverage = !active || query.isError || Boolean(progress?.firstNativeFailure || progress?.currentNativeFailure || progress?.nativeStop)
  const title = !runId ? '链接缺少研究任务' : query.isError ? '最新进度暂时无法确认' : !run ? '正在读取研究进度'
    : run.status === 'cancelled' ? '本次研究已停止' : failed ? '本次研究未完成'
    : active ? run.status === 'admitted' ? '研究正在排队' : '研究正在执行'
    : run.artifact_id ? '研究已结束，有报告记录待核对' : '研究已结束，未生成报告'
  return <div className="content-wrap product-page research-task-page">
    <div className="product-heading"><div><span className="product-kicker">市场与宏观研究</span><h1>{title}</h1><p>{query.isError ? '连接恢复后才能确认当前状态，请勿重复提交研究。'
      : active ? '基础任务状态每 3 秒更新。可以离开此页，稍后从“研究”继续查看同一任务。' : '研究状态与报告以实际保存记录为准。'}</p></div><>{validBack ? <AssetBackLink back={validBack} /> : <button className="button secondary" onClick={() => onNavigate('research')}>返回研究列表</button>}<button className="button secondary" disabled={!runId || query.isFetching} onClick={() => void query.refetch()}>刷新进度</button></></div>
    {query.isError && <div className="product-notice danger" role="alert"><div><strong>暂时无法获取最新进度</strong><p>以下保留内容不代表任务仍在运行。自动读取已暂停，请重新读取或查看系统状态。</p><button className="button secondary" onClick={() => void query.refetch()}>重新连接</button></div></div>}
    {run && <section className="product-hero" aria-live="polite">
      <h2>{researchTitle({ event_title: run.headline || '本次研究任务' })}</h2>
      <p>{query.isError ? '下方保留最近一次读取的任务记录，不代表当前执行状态。' : active ? '任务记录显示尚在执行；当前步骤与材料可按需读取。等待期间无需重复提交。' : failed ? '任务记录已保留，可查看原因后决定是否重试。' : !run.artifact_id ? '本次任务没有已保存报告，可查看任务记录与已取得的材料。' : '已保存报告引用；完整内容、身份与适用限制请进入报告页核对。'}</p>
      <div className="product-monitor"><div><span>基础任务状态</span><strong>{run.status === 'admitted' ? '已准入' : run.status === 'running' ? '进行中' : run.status === 'failed' ? '失败' : run.status === 'cancelled' ? '已停止' : run.status === 'degraded' ? '有限结果' : '已结束'}</strong></div><div><span>报告记录</span><strong>{run.artifact_id ? '已保存引用，待核对' : '未保存'}</strong></div><div><span>任务最近更新</span><strong>{assetTime(run.updated_at)}</strong><small>任务更新时间不代表新增证据或执行进程存活</small></div></div>
      {run.error_code && <p role={failed ? 'alert' : 'status'}>任务有已保存错误记录，请打开原任务核对原因与恢复条件。</p>}
      <div className="product-hero-foot"><button className="button primary" onClick={() => onNavigate(failed ? 'failed' : 'report', { run: run.run_id, ...(!failed && active ? { focus: 'sources' } : {}), ...(validBack ? { back: validBack } : {}) })}>{failed ? '查看原因与恢复' : active ? '查看已收集的证据' : run.artifact_id ? '阅读研究报告' : '查看任务记录'}</button>{active && <button className="button secondary" disabled={!canStop} onClick={() => { if (canStop) command.execute('cancel', '用户在研究进度页停止本次任务。') }}>{command.busy ? '正在核对操作…' : command.locked ? '请先核对原操作' : '停止本次研究'}</button>}<button className="button secondary" onClick={() => onNavigate('home')}>返回今日研究</button></div>
      <details className="risk-audit"><summary>任务身份与保存记录</summary><p>任务：{run.run_id}</p><p>建立于 {assetTime(run.created_at)}；{run.finished_at ? `结束记录：${assetTime(run.finished_at)}` : '尚无结束时间记录'}</p>{run.error_code && <p>保存的错误：<code>{run.error_code}</code></p>}</details>
    </section>}
    {runId && <section className="product-card" aria-label="读取后台工作与材料记录">
      <h3>按需核对后台记录</h3><p>完整材料与工具记录需要单独核验；展开后读取一次，不随上方基础进度自动刷新。</p>
      {!detail && <p>材料条目：未读取；工具调用：未读取。未读取不表示零条材料或后台空转。</p>}
      <button className="button secondary" type="button" disabled={details.isFetching} aria-expanded={Boolean(detail || details.isFetching || details.isError)} onClick={() => void details.refetch()}>{details.isFetching ? '正在读取后台工作与材料记录…' : detail || details.isError ? '重新读取后台工作与材料记录' : '展开后台工作与材料记录'}</button>
      {details.isFetching && <p role="status">正在核验完整后台记录，可能需要较长时间；上方基础状态独立更新，可以离开后返回。</p>}
      {details.isError && <p role="alert">后台工作与材料记录读取失败。请稍后显式重新读取；上方基础任务状态仍独立显示。</p>}
      {detail && <><h4>{researchTitle(detail.run, detail.research_focus)}</h4><ResearchFocus run={detail.run} focus={detail.research_focus} /><p>按需读取时的阶段：{stages[detail.run.stage]}。这是保存记录快照，不作为当前执行进程存活证明。</p>{currentPreparationWaiting(detail) !== null && <p role="status">按需读取时记录：{preparationWaitingText}</p>}<p>详情材料条目 {detail.evidence.length} 条；工具调用 {detail.total_tool_calls} 次。后续变化请显式重新读取。</p></>}
    </section>}
    {progress && <section className="product-card" aria-label="后台工作与材料记录">
      <h3>后台工作与材料记录</h3>
      <p>任务最近保存于 {assetTime(progress.taskRecordedAt)}；这是任务记录时间，不能单独确认执行进程存活或新增研究结论。</p>
      {!progress.currentConfirmed && <p role="status">下方是按需读取的记录，尚未确认与最新基础状态一致；完整材料不会自动轮询。</p>}
      <p>已接纳且带内容身份的材料 {progress.materials.length} 条；未解材料要求 {progress.gaps.length} 项。必需材料齐全不代表主因果链、反向机制或独立复核已完成。</p>
      {progress.inFlight.length > 0 ? <><h4>在途工作记录</h4><ul>{progress.inFlight.map(item => <li key={`${item.kind}:${item.session}:${item.reference}`}>{item.kind === 'model' ? '模型调用' : '取证调用'}从 {assetTime(item.startedAt)} 开始，尚未保存匹配的结束记录。</li>)}</ul><p>持续时间超过一分钟不会在此被判为失败；可以离开并返回查看。缺少结束记录也不能证明进程仍在工作。</p></> : <p>当前视图没有可确认的在途调用；这不等于后台已空转或没有执行。</p>}
      {progress.firstNativeFailure && <><h4>最早已认证的原生阻断</h4><p>{nativeCauseLabel(progress.firstNativeFailure.error_code)}。发生于 {assetTime(progress.firstNativeFailure.occurred_at)}，原回执入库于 {assetTime(progress.firstNativeFailure.recorded_at)}。</p></>}
      {showFailureCoverage && progress.failureCoverage?.status !== 'complete' && <p role="status">最早原因尚未查全；{progress.failureCoverage?.uncovered_generations.length ? `第 ${progress.failureCoverage.uncovered_generations.join('、')} 轮研究的原生终态绑定尚未认证。` : '当前缺少可完整认证的原生终态记录。'}历史缺口不会被推断成没有更早错误。</p>}
      {!progress.firstNativeFailure && progress.currentNativeFailure && <><h4>当前已认证的原生阻断</h4><p>{nativeCauseLabel(progress.currentNativeFailure.error_code)}。发生于 {assetTime(progress.currentNativeFailure.occurred_at)}，原回执入库于 {assetTime(progress.currentNativeFailure.recorded_at)}；当前记录不能替代尚未查全的历史首因。</p></>}
      {(progress.firstNativeFailure?.error_code === 'skill_callback_transport_timeout' || progress.currentNativeFailure?.error_code === 'skill_callback_transport_timeout') && <p>这是技能请求的传输等待边界，不能单独证明后台研究作业、原研究期限或观察器已经停止。有效思考超过一分钟会继续作为后台工作记录。</p>}
      {progress.finalError && <p role="status">{!errorStatusKnown || !run ? '已有错误记录；当前任务状态尚未确认，暂不能判断它是阶段错误还是最终收尾原因。请先刷新进度，原记录保留在下方审计中。'
        : active ? '最新基础状态显示任务仍在运行；已有阶段错误记录，不代表整次研究失败。可在下方“核对工作、材料与失败记录”查看原记录。'
        : ['completed', 'failed', 'cancelled', 'degraded'].includes(run.status) ? failed && run.error_code === progress.finalError ? '任务已结束并保存最终错误；请结合下方审计记录核对收尾原因与此前的阶段错误。' : '任务已结束；这里保留的是详情中已保存的错误，是否属于最终收尾原因需结合基础状态与下方审计记录核对。'
        : '已有错误记录；当前任务状态尚未确认，暂不能判断它是阶段错误还是最终收尾原因。请先刷新进度，原记录保留在下方审计中。'}</p>}
      {progress.nativeStop && <><h4>原生执行较早停止</h4><p>原生执行发生于 {assetTime(progress.nativeStop.terminal_at)}；之后还有 {progress.laterWork.length} 条已保存的工作或收尾记录。最终错误与这一较早阻断分别核对，不能把后续核验超时作为整轮没有工作的证据。</p></>}
      {progress.unfinishedAtStop.length > 0 && <p>停止前有 {progress.unfinishedAtStop.length} 个调用尚未保存匹配结束记录，当前不再把它们显示为正在执行。</p>}
      {progress.gaps.length > 0 && <><h4>下一步待补目标</h4><p>这些目标来自已保存覆盖评估，并不表示新任务已提交。</p><ul>{progress.gaps.map((gap, index) => <li key={`${gap.requirement_id}:${index}`}><strong>{requirementNames[gap.requirement_id] ?? `材料要求 ${index + 1}`}</strong> · {gap.importance === 'hard' ? '必需材料' : '补充材料'}<p>{technicalSummaryName(gap.reason_code, '未保存具体缺口原因', '缺口原因尚无中文说明，请展开原要求核对。')}</p><details><summary>查看这项材料的原要求</summary><p>要求标识：<code>{gap.requirement_id}</code>；原因：<code>{gap.reason_code}</code></p><p>{gap.query_hint || '未保存具体补充指引。'}</p></details></li>)}</ul></>}
      {(progress.duplicateCallRecords > 0 || progress.sameSummaryCalls > 0) && <p>同一调用标识的重复开始记录 {progress.duplicateCallRecords} 条；同轮不同标识、相同摘要的调用 {progress.sameSummaryCalls} 次。摘要相同不能证明参数相同或重复HTTP，需核对原调用记录。</p>}
      <details><summary>核对工作、材料与失败记录</summary><pre className="asset-json">{JSON.stringify({ materials: progress.materials, in_flight: progress.inFlight, unfinished_at_stop: progress.unfinishedAtStop, native_stop_current_link: progress.nativeStop, first_authenticated_native_failure: progress.firstNativeFailure, current_authenticated_native_failure: progress.currentNativeFailure, native_failure_coverage: progress.failureCoverage, later_work: progress.laterWork, final_error: progress.finalError, gaps: progress.gaps, issues: progress.issues }, null, 2)}</pre><p>已认证原件身份与历史覆盖情况在此分别列出；没有可认证原件时保持未知。此展示不授权重试或交易，也不暴露原始Provider消息。</p></details>
    </section>}
    {runId && <RunCommandRecovery state={command} />}
  </div>
}
