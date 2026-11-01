import { useEffect, useRef, useState } from 'react'
import type { DecisionSettingsCurrentViewV1 } from '../api/decisionSettings'
import { settingsApi } from '../api/decisionSettings'
import type { DecisionHorizonV2 } from '../api/perpetualResearch'
import { acknowledgeScheduleIntent, makeScheduleIntent, perpetualResearchSchedules, readScheduleIntent, type ScheduleIntent, type ScheduleView } from '../api/perpetualResearchSchedules'

export function PerpetualResearchSchedulePanel({ current, horizon }: { current: DecisionSettingsCurrentViewV1 | null; horizon: DecisionHorizonV2 }) {
  const [owner, setOwner] = useState(() => settingsApi.session()?.owner_id ?? null)
  const [view, setView] = useState<ScheduleView | null>(null)
  const [pending, setPending] = useState<ScheduleIntent | null>(null)
  const [busy, setBusy] = useState(false), [message, setMessage] = useState('')
  const [refreshing, setRefreshing] = useState(false), [refreshError, setRefreshError] = useState('')
  const epoch = useRef(0), posting = useRef(false)
  const readSequence = useRef(0), intentSequence = useRef(0)
  const partition = current?.revision.partition, identity = JSON.stringify(partition)
  const saved = current?.revision.operation === 'upsert' && Boolean(current.revision.values) && current.revision.owner_id === owner
  useEffect(() => { const changed = () => setOwner(settingsApi.session()?.owner_id ?? null); window.addEventListener('decision-hub-settings-session-changed', changed); return () => window.removeEventListener('decision-hub-settings-session-changed', changed) }, [])
  async function refresh(generation: number) {
    if (!partition) return
    const sequence = ++readSequence.current
    setRefreshing(true)
    try {
      const next = await perpetualResearchSchedules.current(partition, horizon)
      if (generation === epoch.current && sequence === readSequence.current) { setView(next); setRefreshError('') }
    } catch (error) {
      if (generation === epoch.current && sequence === readSequence.current) throw error
    } finally {
      if (generation === epoch.current && sequence === readSequence.current) setRefreshing(false)
    }
  }
  async function refreshSafely(generation: number) {
    try { await refresh(generation) }
    catch { if (generation === epoch.current) setRefreshError('周期状态暂时无法更新，上次读取结果已保留；可以稍后重新读取。') }
  }
  async function recover(original = pending) {
    if (!original || posting.current) return
    const generation = epoch.current
    setBusy(true)
    try {
      await perpetualResearchSchedules.receipt(original)
      if (generation === epoch.current) { acknowledgeScheduleIntent(original); ++intentSequence.current; setPending(null); await refresh(generation); if (generation === epoch.current) setMessage('原周期设置回执已确认。') }
    } catch { if (generation === epoch.current) setMessage('原周期设置尚未确认。已保留请求，没有再次提交。') }
    finally { if (generation === epoch.current) setBusy(false) }
  }
  useEffect(() => {
    const generation = ++epoch.current
    const sequence = ++readSequence.current, intentVersion = intentSequence.current
    setView(null); setPending(null); setMessage(''); setBusy(false); setRefreshing(false); setRefreshError('')
    if (!owner || !partition) return
    void Promise.all([perpetualResearchSchedules.current(partition, horizon), readScheduleIntent(partition, horizon)]).then(([value, original]) => {
      if (generation !== epoch.current) return
      if (sequence === readSequence.current) setView(value)
      if (intentVersion === intentSequence.current) { setPending(original); if (original) void recover(original) }
    }).catch(() => { if (generation === epoch.current && sequence === readSequence.current) setMessage('周期设置暂不可核验，请重新连接本机身份后读取。') })
    return () => { ++epoch.current }
  }, [owner, identity, horizon])
  const lastStatus = view?.last_slot?.run_status ?? view?.last_slot?.status
  const keepReading = view?.state === 'active' || Boolean(view?.last_slot && !['completed', 'degraded', 'failed', 'cancelled', 'unsupported_protocol'].includes(lastStatus ?? ''))
  useEffect(() => {
    if (!owner || !partition || !keepReading || busy || pending || refreshing) return
    const generation = epoch.current
    const timer = window.setInterval(() => { if (!posting.current) void refreshSafely(generation) }, 15_000)
    return () => window.clearInterval(timer)
  }, [owner, identity, horizon, keepReading, busy, pending, refreshing])
  async function submit(operation: 'enable' | 'pause') {
    if (!partition || !view || pending || posting.current || busy) return
    posting.current = true; setBusy(true); setMessage('')
    ++readSequence.current; setRefreshing(false)
    const generation = epoch.current
    try {
      const original = await makeScheduleIntent(partition, horizon, view.state_version, operation, view.receipt)
      if (generation !== epoch.current) return
      ++intentSequence.current; setPending(original)
      await perpetualResearchSchedules.command(original)
      if (generation === epoch.current) { acknowledgeScheduleIntent(original); ++intentSequence.current; setPending(null); await refresh(generation); if (generation === epoch.current) setMessage('周期设置已按服务端原件保存。') }
    } catch { if (generation === epoch.current) setMessage('周期设置结果尚未确认，请核对原回执。设置版本变化时不会覆盖新版本。') }
    finally { posting.current = false; if (generation === epoch.current) setBusy(false) }
  }
  async function retryOriginal() {
    if (!pending || posting.current || busy) return
    const original = pending, generation = epoch.current
    posting.current = true; setBusy(true)
    try {
      await perpetualResearchSchedules.command(original)
      if (generation === epoch.current) { acknowledgeScheduleIntent(original); ++intentSequence.current; setPending(null); await refresh(generation); if (generation === epoch.current) setMessage('同一周期设置的原回执已确认。') }
    } catch { if (generation === epoch.current) setMessage('同一请求尚未确认，原件已保留；版本冲突不会覆盖服务端新版本。') }
    finally { posting.current = false; if (generation === epoch.current) setBusy(false) }
  }
  function slotSummary() {
    const slot = view?.last_slot
    if (!slot) return ''
    if (slot.run_status === 'failed') return '本次研究失败，请打开原任务核对原因'
    if (slot.run_status === 'cancelled') return '本次研究已取消'
    if (slot.run_status === 'completed' || slot.run_status === 'degraded') return '本次研究已结束，请核对报告和建议'
    if (slot.run_status === 'running') return '本次研究正在执行'
    if (slot.status === 'unsupported_protocol') return '协议暂不支持，原件保留'
    if (slot.error_code) return '尚未完成准入，存在阻断或已过期'
    return slot.status === 'queued' ? '研究已排队，结果尚需核对' : '等待准入'
  }
  return <section aria-label="永续周期研究">
    <h3>{horizon.replace('h', '')} 小时预测的持续研究</h3>
    <p>明确启用后，从下个整点开始，服务端按每小时槽位使用已保存的风险偏好与当前模拟账户账本研究；账户观察在每次研究受理时由服务端冻结。如需立即研究，可使用上方手动研究入口。是否能入队还须通过来源、预算和活跃研究核验。不会自动下单。</p>
    {view?.receipt?.command.schema_version === 'perpetual-research-schedule-command.v1' && <p>现有旧版周期授权保留原账户设置语义；不会自动升级。可先暂停，再在模拟账户下明确启用新授权。</p>}
    {partition?.account_kind !== 'paper' && <p>新周期授权仅支持模拟账户；旧版待确认请求和暂停仍使用原版本。</p>}
    {view && <p role="status">{view.state === 'active' ? '周期研究已启用' : view.state === 'paused' ? '周期研究已暂停' : '周期研究尚未启用'}{view.next_due_at && view.state === 'active' ? `；下次检查 ${new Date(view.next_due_at).toLocaleString('zh-CN')}` : ''}。</p>}
    {view?.last_slot && <p>最近槽位：{slotSummary()}。{view.last_slot.run_id && <a href={`#/progress?run=${encodeURIComponent(view.last_slot.run_id)}`}>查看该次进度</a>}</p>}
    {Boolean(view?.missed_slots) && <p>已记录 {view!.missed_slots} 个未完成准入的过期槽位；没有补造历史研究。</p>}
    {pending ? <div><p>原周期设置结果待确认。重试只提交同一请求，不创建新请求。</p><button disabled={busy} onClick={() => void recover()}>核对原周期回执</button><button disabled={busy} onClick={() => void retryOriginal()}>重试同一周期设置</button></div> : <div>
      <button disabled={!saved || partition?.account_kind !== 'paper' || !view || busy || view.state === 'active'} onClick={() => void submit('enable')}>启用此期限周期研究</button>
      <button disabled={!view || busy || view.state !== 'active'} onClick={() => void submit('pause')}>暂停此期限周期研究</button>
    </div>}
    <button disabled={!owner || !partition || busy || refreshing || Boolean(pending)} onClick={() => void refreshSafely(epoch.current)}>刷新周期研究状态</button>
    {refreshError && <p role="alert">{refreshError}</p>}
    <p>暂停只停止后续研究准入；已启动研究与已有模拟仓位的保护分别保留。已归档的事件动因复用同一授权，重合动因不会重复创建研究。</p>
    {message && <p role="alert">{message}</p>}
  </section>
}
