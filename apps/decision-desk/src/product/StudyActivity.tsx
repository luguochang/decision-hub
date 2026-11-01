import { researchTaskScene } from './ResearchProgressPage'
import type { StudyTimelineView } from '@decision-hub/contracts-ts'
import { useState } from 'react'
import { assetTime, type AssetNavigate } from './assetFormat'

type Activity = NonNullable<StudyTimelineView['activity']>
const decisions: Record<string, string> = { admitted: '已进入研究队列', ignored: '本次未触发研究', blocked: '本次研究准入受限', merged: '已合并到已有任务' }
const reasons: Record<string, string> = {
  no_high_impact_title_match: '标题规则未匹配到当前关注的重要市场事件。资料已登记，本次没有因此发起 AI 研究。',
  high_impact_title: '标题规则匹配到当前关注的重要事件。',
  high_impact_title_match: '标题规则匹配到当前关注的重要事件。',
  study_research_active: '这个主题已有研究正在排队或执行，本次暂未新建研究。',
  research_admission_budget_exhausted: '当前自动研究预算已用完，本次暂未新建研究。',
  study_host_terminal_unknown: '上一项研究的执行状态尚未确认，本次暂未新建研究。',
  discovery_recovery_admitted: '临时限制已解除，这条已登记的来源事件已进入研究队列。',
  discovery_recovery_expired: '这条事件的自动恢复期限已结束，本次没有发起研究。原资料和阻断原因仍保留。',
  discovery_recovery_source_changed: '来源配置已变化，本次自动恢复已停止，需核对来源后重新决定。',
  discovery_recovery_policy_changed: '研究准入规则已变化，本次自动恢复已停止，需按当前规则重新决定。',
  discovery_recovery_source_invalid: '来源范围尚未通过核对，本次自动恢复已停止。',
  discovery_recovery_original_changed: '原事件、资料或研究范围已变化，本次自动恢复已停止。',
  discovery_recovery_original_invalid: '原准入记录无法核对，本次自动恢复已停止。',
  discovery_recovery_run_scope_mismatch: '已有任务与原事件范围不一致，本次自动恢复已停止。',
  same_input_already_admitted: '相同资料已有研究任务，已关联原任务，避免重复发起。',
  study_paused: '这个主题已暂停自动复查。',
  duplicate_event: '相同事件已处理，避免重复发起。',
}
const statuses: Record<string, string> = { admitted: '排队等待', running: '研究执行中', completed: '任务已完成', degraded: '任务已结束，研究受限', failed: '任务失败', cancelled: '任务已停止' }
const origins: Record<string, string> = { manual: '手动发起', automatic: '来源自动发现', scheduled_recheck: '定时复查', event_recheck: '事件触发复查', invalidation_recheck: '条件失效复查' }
function taskAction(run: Activity['recent_runs'][number]) {
  return run.status === 'cancelled' ? '查看停止原因' : run.status === 'failed' ? '查看失败原因' : run.report_available ? '查看报告' : '查看任务'
}
export function StudyActivity({ activity, paused, back, onNavigate }: { activity: Activity | null | undefined; paused: boolean; back: string; onNavigate: AssetNavigate }) {
  const [tasksOpen, setTasksOpen] = useState(false)
  if (!activity) return <p className="product-notice" role="status">任务与准入记录暂不可读；请刷新主题后核对。</p>
  const decision = activity.latest_admission
  const activeTask = activity.recent_runs.find(run => (run.status === 'admitted' || run.status === 'running') && !run.report_available)
  const latestTask = activity.recent_runs[0]
  const needsAttention = latestTask && ['failed', 'cancelled', 'degraded'].includes(latestTask.status) ? latestTask : null
  const goToTask = (run: Activity['recent_runs'][number]) => onNavigate(researchTaskScene(run.status, run.report_available), { run: run.run_id, back })
  return <section className="study-activity study-activity-compact" aria-label="主题执行与准入">
    <div className="study-next-step"><div><h3>当前任务与下一步</h3><p>{activity.active_runs > 0 ? `正在排队或执行 ${activity.active_runs} 个研究任务。` : '当前没有排队或执行中的研究任务。'}{paused ? '自动复查已暂停。' : activity.next_review_at ? `最早待调度计划：${assetTime(activity.next_review_at)}；尚未开始执行。` : '暂无已登记的后续复查计划。'}</p></div>{activity.active_runs > 0 && (activeTask ? <button className="button secondary" onClick={() => goToTask(activeTask)}>查看当前任务</button> : <button className="button secondary" onClick={() => setTasksOpen(true)}>核对最近任务</button>)}{activity.active_runs === 0 && needsAttention && <button className="button secondary" onClick={() => goToTask(needsAttention)}>{taskAction(needsAttention)}</button>}</div>
    {activity.active_runs > 0 && !activeTask && <p className="study-task-attention">当前清单未提供可直接打开的在途任务，请先核对最近记录。</p>}
    {activity.active_runs === 0 && needsAttention && <p className="study-task-attention">最近任务：{statuses[needsAttention.status]} · {assetTime(needsAttention.created_at)}。这不影响已保存的历史报告。</p>}
    <details className="study-task-history" open={tasksOpen} onToggle={event => setTasksOpen(event.currentTarget.open)}><summary>最近任务 · 共登记 {activity.research_runs} 次研究{activity.active_runs > 0 ? `，${activity.active_runs} 项待处理` : ''}</summary>
      {activity.recent_runs.length > 0 ? <><p className="product-muted">显示最近 {activity.recent_runs.length} 项。登记次数不等于报告完成数。</p><ul className="study-task-list">{activity.recent_runs.map(run => <li key={run.run_id}><div><strong>{statuses[run.status] ?? '任务状态待确认'}</strong><small>{origins[run.admission_origin] ?? '其他触发方式'} · {assetTime(run.created_at)} · {run.report_available ? '报告已保存' : '尚无已保存报告'}</small></div><button className="button secondary" onClick={() => goToTask(run)}>{taskAction(run)}</button>{run.error_code && <details><summary>失败原始记录</summary><code>{run.error_code}</code></details>}</li>)}</ul></> : <p className="product-muted">当前没有最近任务记录。</p>}
    </details>
    <details className="study-admission-history"><summary>来源准入与复查计划{decision ? ` · ${decisions[decision.decision] ?? '已登记准入结果'}` : ''}</summary>
      {decision ? <div className="product-notice"><strong>{decisions[decision.decision] ?? '已登记准入结果'} · {assetTime(decision.decided_at)}</strong><p>{reasons[decision.reason_code] ?? '具体准入依据保留在原始记录中；本记录不代表报告已完成。'}</p><details><summary>查看准入原始记录</summary><p>来源：{decision.source_id} · 策略：{decision.policy_version}</p><p>原因：{decision.reason_code}</p><pre className="asset-json">{JSON.stringify(decision.details, null, 2)}</pre></details></div> : <p className="product-muted">没有关联的来源自动准入记录；手动发起或历史任务可能没有此记录。</p>}
      <p className="product-muted">{paused ? '自动复查已暂停。' : '已允许后续自动复查；发起任务仍需通过事件准入、复查计划和运行条件。'}{activity.next_review_at ? ` 已登记 ${activity.pending_review_plans} 项待调度计划，最早计划时间：${assetTime(activity.next_review_at)}。计划时间不代表已开始执行。` : ' 当前没有已登记的待调度复查计划。'}</p>
    </details>
  </section>
}
