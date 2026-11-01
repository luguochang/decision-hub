import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import type { PerpetualAdviceSelection, PerpetualAdviceView } from '../api/perpetualAdvice'
import { planWatchApi, type ContinuousPlanWatchView } from '../api/perpetualPlanWatch'

export function PerpetualPlanWatchPanel({ selection, value }: { selection: PerpetualAdviceSelection; value: PerpetualAdviceView }) {
  const [research, setResearch] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const query = useQuery({ queryKey: ['perpetual-plan-watch', selection.owner_id, value.revision!.revision_id], queryFn: () => planWatchApi.current(selection, value), retry: false, refetchOnWindowFocus: false })
  const receipt = query.data?.receipt, expiry = query.data?.expiry
  async function save(operation: 'watch' | 'pause') {
    setBusy(true); setError('')
    try { await planWatchApi.save(selection, value, query.data, operation, research); await query.refetch() }
    catch { setError('未确认操作成功；请重新读取原回执后重试。设置或这版方案可能已变化。') }
    finally { setBusy(false) }
  }
  return <><section aria-label="这版方案的到期提醒"><h4>这版方案到期后</h4>
    <p>仅跟踪当前版本：{value.revision!.revision_no}；到期时间：{new Date(value.revision!.valid_until).toLocaleString('zh-CN')}。提醒不代表条件已失效、方向反转或授权下单。</p>
    {(!receipt || receipt.state === 'paused') && <><label><input type="checkbox" checked={research} onChange={event => setResearch(event.target.checked)} />另行同意：到期后的下一个完整 UTC 小时，发起一次新的研究（会使用模型额度）</label><p>默认只提醒，不创建新研究；不会自动跟随下一版方案。</p></>}
    {receipt && <p role="status">{receipt.state === 'paused' ? '后续跟踪已暂停' : expiry ? '已生成到期提醒（不代表已送达）' : '已登记这版方案的到期提醒'}{receipt.auto_review_slot && `；单独同意的复查时段：${new Date(receipt.auto_review_slot).toLocaleString('zh-CN')}`}。</p>}
    {expiry?.status === 'settings_or_revision_changed' && <p>设置或方案已变化，未自动创建新研究；请读取当前设置后重新决定。</p>}
    {expiry?.status === 'slot_missed' && <p>复查时段已错过，不补跑旧时段；可使用现有手动研究入口。</p>}
    {!expiry && (!receipt || receipt.state === 'paused') && <button disabled={busy || query.isPending || query.isError} onClick={() => void save('watch')}>登记到期提醒一次</button>}
    {receipt?.state === 'active' && <button disabled={busy} onClick={() => void save('pause')}>暂停后续跟踪</button>}
    <button disabled={busy || query.isFetching} onClick={() => void query.refetch()}>重新读取跟踪回执</button>
    {(error || query.isError) && <p role="alert">{error || '跟踪原件暂不可用。'}</p>}
    <p>此一次提醒入口仅跟踪到期，不监测价格、资金费率或新闻条件；已冻结的研究不会因暂停而被改写。</p>
  </section><ContinuousPlanWatchPanel selection={selection} value={value} /></>
}

function ContinuousPlanWatchPanel({ selection, value }: { selection: PerpetualAdviceSelection; value: PerpetualAdviceView }) {
  const [research, setResearch] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const query = useQuery({ queryKey: ['perpetual-plan-watch-continuous', selection.owner_id, selection.partition, selection.horizon, selection.settings_revision],
    queryFn: () => planWatchApi.continuousCurrent(selection), retry: false, refetchOnWindowFocus: false })
  const receipt = query.data?.receipt, progress = query.data?.progress
  const active = receipt?.state === 'active'
  async function save(operation: 'watch' | 'pause') {
    setBusy(true); setError('')
    try { await planWatchApi.saveContinuous(selection, value, query.data, operation, research); await query.refetch() }
    catch { setError('持续跟踪操作尚未确认；请重新读取服务端原回执，核对后再操作。') }
    finally { setBusy(false) }
  }
  return <section aria-label="持续跟踪已发布方案"><h4>持续跟踪已发布方案</h4>
    <p>仅此账户、合约、{selection.horizon} 期限和当前已保存设置。开启后接续正式发布的新方案；相同结果不重复提醒，发生实质变化或方案到期时生成提醒。关闭页面后仍由后台跟踪，主机或服务中断期间不保证覆盖。</p>
    <p>旧的一次提醒不会自动升级。跟踪不授权下单，也不改变已有模拟仓位和原回执。</p>
    <p>默认仅提醒。另行同意后，条件确认或失效可立即发起新研究；每版到期仍在下一完整 UTC 小时复查。每次研究使用已保存的风险偏好与当前模拟账户账本，由服务端在受理时冻结。</p>
    {selection.partition.account_kind !== 'paper' && <p>此持续授权仅支持模拟账户；旧的一次提醒保持原版本。</p>}
    {!active && <label><input type="checkbox" checked={research} onChange={event => setResearch(event.target.checked)} />另行同意条件触发时立即研究，以及每版到期后的下一完整 UTC 小时复查；可持续使用模型额度</label>}
    {receipt && <p role="status">{active ? '持续跟踪已开启' : '持续跟踪已暂停'}；{receipt.command.mode === 'remind_and_research'
      ? receipt.command.schema_version === 'perpetual-plan-watch-command.v3' && receipt.command.condition_research === true
        ? '已单独同意条件触发即时研究及每版到期复查，使用当前模拟账户账本'
        : receipt.command.schema_version === 'perpetual-plan-watch-command.v2'
          ? '旧版授权仅涵盖每版到期复查，未授权条件即时研究'
          : '已单独同意每版到期复查，未授权条件即时研究'
      : '仅提醒，不创建新研究'}。</p>}
    {progress && <p>最近跟踪的原研究：{progress.origin_run_id}；方案 {progress.plan_revision_ref.ref}。服务端最近观察：{new Date(progress.observed_at).toLocaleString('zh-CN')}。到期：{new Date(progress.review_due_at).toLocaleString('zh-CN')}。{progress.outbox_ids.length > 0 && '提醒已入队，不代表已送达。'}</p>}
    {progress?.status === 'settings_changed' && <p>原设置已变化，已停止本订阅的新增复查；请在当前设置下重新明确开启。</p>}
    {progress?.status === 'invalidated' && <p>这版方案已失效，等待后续正式发布的新方案。</p>}
    {progress?.status === 'slot_missed' && <p>原复查时段已错过，不追跑旧时段；后续正式发布的新方案仍可接续。</p>}
    {progress?.status === 'expired' && <p>这版方案已到期，等待后续正式发布的新方案；不会对同一版反复发起研究。</p>}
    {active ? <button disabled={busy || query.isError} onClick={() => void save('pause')}>暂停持续跟踪</button>
      : <button disabled={selection.partition.account_kind !== 'paper' || busy || query.isPending || query.isError} onClick={() => void save('watch')}>开启持续跟踪</button>}
    <button disabled={busy || query.isFetching} onClick={() => void query.refetch()}>重新读取持续跟踪状态</button>
    {(error || query.isError) && <p role="alert">{error || '持续跟踪原件暂不可用。'}</p>}
    {query.data && <ConditionMonitoring view={query.data} />}
    <p>条件监测当前仅支持经审核明确声明的 BTC/ETH 已收盘 1 分钟价格条件；不覆盖一般资金费率或新闻条件。未监测与证据未知均不代表条件为假。</p>
  </section>
}

function SourceTime({ value }: { value: string | null }) {
  return value ? <time dateTime={value}>{new Date(value).toLocaleString('zh-CN')}</time> : <>暂无</>
}

function ConditionMonitoring({ view }: { view: ContinuousPlanWatchView }) {
  const monitoring = view.schema_version === 'perpetual-plan-watch-view.v3' ? view.condition_monitoring : null
  if (!monitoring) return <section aria-label="当前方案的条件监测"><h5>当前方案的条件监测</h5><p>{view.schema_version === 'perpetual-plan-watch-view.v2'
    ? '此旧版回执未提供条件监测信息；不能据此认定条件已被监测。' : '尚无可读取的条件监测记录；不能据此认定条件已确认或失效。'}</p></section>
  const states = { unregistered: '未登记条件监测', monitoring: '条件监测中', unknown: '条件证据未知', invalidated: '原方案已失效' }
  const statuses = { unmonitored: '未监测', waiting: '待确认', confirmed: '已确认', invalidated: '已失效', not_confirmed: '未确认', unknown: '证据未知' }
  const truths = { true: '真', false: '假', unknown: '未知' }
  return <section aria-label="当前方案的条件监测"><h5>当前方案的条件监测</h5>
    <p role="status">{states[monitoring.state]}。原研究：{monitoring.source_run_id}；方案：{monitoring.plan_revision_ref.ref}。最近观察：<SourceTime value={monitoring.observed_at} />。</p>
    {monitoring.reason_codes.length > 0 && <p>监测登记原因：{monitoring.reason_codes.join('、')}。</p>}
    {monitoring.new_exposure_blocked && <p>服务端已拒绝这版旧建议的新增模拟开仓或加仓；已有模拟仓位的保护继续有效。此限制在暂停跟踪或关闭页面后仍有效。</p>}
    <p>条件确认不会改变原概率；新建议必须来自经审核后正式发布的后续方案。</p>
    {monitoring.conditions.length === 0 && <p>暂无已登记的条件；未监测的条件不作确认或失效判断。</p>}
    <ul>{monitoring.conditions.map(condition => <li key={condition.assumption_ref.ref}>
      <p>{condition.purpose === 'confirmation' ? '确认条件' : condition.purpose === 'invalidation' ? '失效条件' : '未登记监测用途'}：{condition.text}</p>
      <p>{statuses[condition.status]}；当前判定：{truths[condition.truth]}；连续样本：{condition.streak} / {condition.required_samples ?? '未声明'}。</p>
      <p>源 K 线时间：<SourceTime value={condition.last_bar_at} />；服务端观察：<SourceTime value={condition.observed_at} />。</p>
      {condition.status === 'unmonitored' && <p>未监测的条件不作确认或失效判断。</p>}
      {condition.reason_codes.length > 0 && <p>原因：{condition.reason_codes.join('、')}。</p>}
      {condition.request_id ? <p>已登记后续研究请求：{condition.request_id}；不代表研究已完成，后续建议须等待正式发布。</p>
        : (condition.status === 'confirmed' || condition.status === 'invalidated') && <p>未登记后续研究请求；仅提醒授权不会创建新研究。</p>}
      {condition.outbox_ids.length > 0 && <p>提醒已入队（{condition.outbox_ids.join('、')}），不代表已送达。</p>}
    </li>)}</ul>
  </section>
}
