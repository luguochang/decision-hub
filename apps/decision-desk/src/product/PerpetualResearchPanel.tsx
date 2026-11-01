import { useEffect, useRef, useState } from 'react'
import type { DecisionSettingsCurrentViewV1 } from '../api/decisionSettings'
import { settingsApi } from '../api/decisionSettings'
import { ApiRequestError } from '../api/client'
import {
  makePerpetualResearchIntent, perpetualResearch, readPerpetualResearchIntent,
  researchIntentMatchesCurrent, savePerpetualResearchIntent, readPerpetualResearchHistory,
  receiptRecoveryDisposition,
  type PerpetualResearchIntent, type PerpetualResearchReceipt, type DecisionHorizonV2,
  type PerpetualResearchClosureReceiptV1,
} from '../api/perpetualResearch'
import './PerpetualResearchPanel.css'

function owner() { try { return settingsApi.session()?.owner_id ?? null } catch { return null } }
function identity() { try { return settingsApi.session() } catch { return null } }
function sameIdentity(left: ReturnType<typeof identity>, right: ReturnType<typeof identity>) {
  return Boolean(left && right && left.owner_id === right.owner_id && left.csrf_token === right.csrf_token && left.expires_at === right.expires_at)
}
const rejectionKey = (original: PerpetualResearchIntent) => `decision-hub:perpetual-research-rejection.v1:${original.owner_id}:${original.command.request_id}`
function activeRejection(original: PerpetualResearchIntent) {
  const raw = localStorage.getItem(rejectionKey(original))
  if (!raw) return false
  const value = JSON.parse(raw)
  if (value.owner_id !== original.owner_id || value.request_id !== original.command.request_id || value.command_hash !== original.command_hash
    || value.status !== 409 || value.reason_code !== 'study_research_active') throw new Error('perpetual_research_rejection_identity_mismatch')
  return true
}

function failureMessage(error: unknown) {
  const code = error instanceof Error ? error.message : ''
  if (code.includes('settings_changed') || code.includes('settings_conflict') || code.includes('settings_state_conflict') || code.includes('saved_settings_required')) return '已保存的设置已变化。请重新读取并保存当前设置，用新原件发起研究。原请求会保留。'
  if (code.includes('owner') || code.includes('session')) return '本机身份已变化或过期。请重新连接本机身份后核对原研究回执。'
  if (code.includes('identity_mismatch') || code.includes('ZodError')) return '回执与原研究请求不一致，尚未确认结果。请核对原回执。'
  if (error instanceof ApiRequestError && error.kind==='http') return `服务端返回 HTTP ${error.status ?? '未知'}${/^[a-z][a-z0-9_]{0,159}$/.test(code) ? `（${code}）` : ''}。已保存请求编号、状态和原因记录；尚未确认受理，请核对原回执。`
  return '研究请求结果尚未确认。已保留原请求，请核对回执；不会自动再次提交。'
}

export function PerpetualResearchPanel({ current, disabled = false, horizon = '4h', accountLabel }: { current: DecisionSettingsCurrentViewV1 | null; disabled?: boolean; horizon?: DecisionHorizonV2; accountLabel?: string }) {
  const [ownerId, setOwner] = useState(owner)
  const [sessionGeneration, setSessionGeneration] = useState(0)
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== 'hidden')
  const [intent, setIntent] = useState<PerpetualResearchIntent | null>(null)
  const [confirmed, setConfirmed] = useState<PerpetualResearchReceipt | null>(null)
  const [closure,setClosure]=useState<PerpetualResearchClosureReceiptV1|null>(null)
  const [missingRequestId,setMissingRequestId]=useState<string|null>(null)
  const [rejectedRequestId, setRejectedRequestId] = useState<string | null>(null)
  const [history, setHistory] = useState<PerpetualResearchIntent[]>([])
  const [historicalReceipt, setHistoricalReceipt] = useState<PerpetualResearchReceipt | null>(null)
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [storageBad, setStorageBad] = useState(false)
  const [message, setMessage] = useState('')
  const active = useRef<number | null>(null)
  const epoch = useRef(0)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const receiptController = useRef<AbortController | null>(null)
  const pendingOriginal = useRef<PerpetualResearchIntent | null>(null)
  const retryAttempt = useRef(0)
  const reconnectRequired = useRef(false)
  const expectedIdentity = useRef(identity())
  const visible = intent?.owner_id === ownerId && intent.command.horizon === horizon ? intent : null
  const accepted = confirmed?.owner_id === ownerId && (confirmed.schema_version === 'perpetual-research-receipt.v1' ? '4h' : confirmed.horizon) === horizon ? confirmed : null
  const rejected = Boolean(visible && rejectedRequestId === visible.command.request_id)
  const closed=Boolean(visible && closure?.owner_id===ownerId && closure.request_id===visible.command.request_id)
  const matches = visible ? researchIntentMatchesCurrent(visible, current, horizon) : false
  const historicalIntents = history.filter(item => item.owner_id === ownerId && item.command.request_id !== visible?.command.request_id)
  const hours = horizon.replace('h', '')
  const partitionKey = current ? JSON.stringify(current.revision.partition) : ''
  const saved = Boolean(ownerId && current?.revision.owner_id === ownerId && current.revision.operation === 'upsert' && current.revision.values)
  const asset = current?.revision.partition.instrument_id.includes('ETH') ? 'ETH' : 'BTC'

  function clearRetryTimer() {
    if (retryTimer.current !== null) clearTimeout(retryTimer.current)
    retryTimer.current = null
  }
  function stopRecovery() {
    clearRetryTimer(); receiptController.current?.abort(); receiptController.current = null
    pendingOriginal.current = null; retryAttempt.current = 0
  }
  function requireReconnect() {
    stopRecovery(); reconnectRequired.current = true
    setConfirmed(null); setHistoricalReceipt(null); setOwner(owner())
    setMessage('本机身份已变化或过期。请重新连接本机身份后核对原研究回执。')
  }
  function checkIdentity(original: PerpetualResearchIntent) {
    if (reconnectRequired.current || !sameIdentity(expectedIdentity.current, identity()) || owner() !== original.owner_id) {
      requireReconnect(); return false
    }
    return true
  }
  function scheduleRecovery(original: PerpetualResearchIntent, generation: number) {
    if (generation !== epoch.current || !checkIdentity(original)) return
    clearRetryTimer(); pendingOriginal.current = original
    setMessage('原研究请求结果尚未确认，正在自动核对原研究回执；不会再次提交。')
    if (document.visibilityState === 'hidden') return
    const delay = [1_000, 2_000, 4_000, 8_000, 15_000][Math.min(retryAttempt.current++, 4)]
    retryTimer.current = setTimeout(() => { retryTimer.current = null; void recover(original) }, delay)
  }
  function recoveryError(error: unknown, original: PerpetualResearchIntent, generation: number) {
    const disposition = receiptRecoveryDisposition(error)
    if (disposition === 'reconnect') { requireReconnect(); return }
    if (disposition === 'retry') {
      if (error instanceof ApiRequestError && error.status === 404) setMissingRequestId(original.command.request_id)
      scheduleRecovery(original, generation)
    } else { stopRecovery(); setMessage(failureMessage(error)) }
  }
  async function recover(original: PerpetualResearchIntent, historical = false) {
    if (active.current === epoch.current) return
    if (!checkIdentity(original) || document.visibilityState === 'hidden') return
    const generation = epoch.current
    clearRetryTimer()
    active.current = generation; setBusy(true); setMessage('')
    const controller = new AbortController(); receiptController.current = controller
    try {
      const receipt = await perpetualResearch.receipt(original, controller.signal)
      if (generation === epoch.current && checkIdentity(original)) {
        if (!historical) stopRecovery()
        if (receipt.status==='closed_unaccepted') { if(!historical) {setClosure(receipt);setMissingRequestId(null)} else setMessage('历史原请求已由服务端关闭，未受理为研究。') }
        else if (historical) setHistoricalReceipt(receipt); else {setConfirmed(receipt);setMissingRequestId(null)}
      }
    } catch (error) {
      if (generation === epoch.current && !controller.signal.aborted) {
        if (historical) {
          if (receiptRecoveryDisposition(error) === 'reconnect') requireReconnect()
          else setMessage(failureMessage(error))
        } else recoveryError(error, original, generation)
      }
    } finally {
      if (receiptController.current === controller) receiptController.current = null
      if (active.current === generation) active.current = null
      if (generation === epoch.current) {
        setBusy(false)
        if (historical && pendingOriginal.current) scheduleRecovery(pendingOriginal.current, generation)
      }
    }
  }
  useEffect(() => {
    const changed = () => {
      ++epoch.current; stopRecovery(); active.current = null
      reconnectRequired.current = false; expectedIdentity.current = identity()
      setOwner(owner()); setSessionGeneration(value => value + 1)
    }
    const visibilityChanged = () => setPageVisible(document.visibilityState !== 'hidden')
    window.addEventListener('decision-hub-settings-session-changed', changed)
    document.addEventListener('visibilitychange', visibilityChanged)
    return () => {
      window.removeEventListener('decision-hub-settings-session-changed', changed)
      document.removeEventListener('visibilitychange', visibilityChanged)
    }
  }, [])
  useEffect(() => {
    const generation = ++epoch.current
    stopRecovery(); active.current = null; setBusy(false)
    const cleanup = () => { ++epoch.current; stopRecovery(); active.current = null }
    if (!pageVisible) return cleanup
    setIntent(null); setConfirmed(null); setClosure(null); setMissingRequestId(null); setRejectedRequestId(null); setHistory([]); setHistoricalReceipt(null); setMessage(''); setReady(false); setStorageBad(false); setBusy(false)
    if (!ownerId || reconnectRequired.current) {
      setMessage('请重新连接本机身份后核对原研究回执。'); return cleanup
    }
    expectedIdentity.current = identity()
    void readPerpetualResearchIntent(ownerId, current?.revision.partition, horizon).then(original => {
      if (generation !== epoch.current) return
      setIntent(original); setReady(true)
      const refused = original && activeRejection(original)
      setRejectedRequestId(refused ? original.command.request_id : null)
      if (original && !refused) void recover(original)
      if (current) void readPerpetualResearchHistory(ownerId, current.revision.partition, horizon).then(values => { if (generation === epoch.current) setHistory(values) }).catch(() => { if (generation === epoch.current) setStorageBad(true) })
    }).catch(() => { if (generation === epoch.current) { setStorageBad(true); setReady(true); setMessage('无法核对本机原研究记录，尚未提交新请求。') } })
    return cleanup
  }, [ownerId, partitionKey, horizon, sessionGeneration, pageVisible])

  async function start() {
    if (!saved || !current || disabled || !ready || storageBad || active.current === epoch.current || (visible && !accepted && !rejected && !closed)) return
    if (reconnectRequired.current || !sameIdentity(expectedIdentity.current, identity())) { requireReconnect(); return }
    stopRecovery()
    const generation = epoch.current
    active.current = generation; setBusy(true); setMessage('')
    let attempted: PerpetualResearchIntent | null = null
    try {
      const original = await makePerpetualResearchIntent(current, horizon)
      attempted = original
      await savePerpetualResearchIntent(original)
      if (generation !== epoch.current) return
      const previous = await readPerpetualResearchHistory(original.owner_id, original.command.partition, horizon)
      if (generation !== epoch.current) return
      setHistory(previous); setHistoricalReceipt(null); setIntent(original); setConfirmed(null); setClosure(null); setMissingRequestId(null); setRejectedRequestId(null)
      pendingOriginal.current = original
      const receipt = await perpetualResearch.command(original)
      if (generation === epoch.current && checkIdentity(original)) { stopRecovery(); setConfirmed(receipt) }
    } catch (error) {
      if (generation === epoch.current) {
        if (attempted && owner() === attempted.owner_id && error instanceof ApiRequestError && error.kind === 'http' && error.status === 409 && error.message === 'study_research_active') {
          try {
            const record = JSON.stringify({owner_id:attempted.owner_id,request_id:attempted.command.request_id,command_hash:attempted.command_hash,status:409,reason_code:'study_research_active'})
            localStorage.setItem(rejectionKey(attempted),record)
            if (localStorage.getItem(rejectionKey(attempted)) !== record || !activeRejection(attempted)) throw new Error('rejection_not_saved')
            setRejectedRequestId(attempted.command.request_id)
          } catch { setStorageBad(true); setMessage('无法保存已拒绝请求记录，请先核对本机原记录。') }
          stopRecovery()
        } else if (attempted) recoveryError(error, attempted, generation)
        else setMessage(failureMessage(error))
      }
    }
    finally { if (active.current === generation) active.current = null; if (generation === epoch.current) setBusy(false) }
  }

  async function closeUnaccepted() {
    if(!visible || accepted || closed || missingRequestId!==visible.command.request_id || busy || active.current===epoch.current) return
    if (!checkIdentity(visible)) return
    clearRetryTimer()
    const original=visible,generation=epoch.current
    active.current=generation;setBusy(true);setMessage('')
    try {
      const result=await perpetualResearch.close(original)
      if(generation===epoch.current && checkIdentity(original)) {
        stopRecovery()
        if(result.status==='closed_unaccepted') {setClosure(result);setMissingRequestId(null)}
        else {setConfirmed(result);setMissingRequestId(null)}
      }
    } catch(error) {if(generation===epoch.current) recoveryError(error, original, generation)}
    finally {if(active.current===generation) active.current=null;if(generation===epoch.current)setBusy(false)}
  }

  return <section className="perpetual-research-panel" aria-label={`永续${hours}小时研究`}>
    <h3>研究当前账户的 {hours} 小时方案</h3>
    <p>使用服务端已保存的账户与合约设置；表单修改需先保存。研究排队后，仍需完成资料核对、独立审核和方案发布。</p>
    {!saved && <p role="status">请先保存当前账户的设置，再发起研究。</p>}
    {saved && <p>{asset} 永续 · {hours} 小时预测 · {accountLabel ?? `${current!.revision.partition.account_kind === 'paper' ? '模拟账户' : '自报账户'} ${current!.revision.partition.account_id}`} · 已保存版本 {current!.state_version}。</p>}
    {saved && current!.revision.partition.account_kind === 'paper' && <p>发起新研究时，服务端将核验并冻结当前模拟账本；使用已保存的风险偏好，保存时的账户快照不会替代本次账本观察。旧请求仍按原策略核对。</p>}
    {horizon === '72h' && <p>72 小时是预测目标窗口，不表示已支持连续持仓 72 小时；实际资金费、保证金与退出仍需独立核验。</p>}
    {current?.revision.partition.account_kind !== 'paper' && current?.revision.values?.position_context.state === 'unknown' && <p>仓位未知；若可研究，仅按假设空仓作比较，不代表你的真实仓位或账户操作。</p>}
    {current?.revision.partition.account_kind !== 'paper' && current?.revision.values && ['long', 'short'].includes(current.revision.values.position_context.state) && <p>已保存持仓的完整减仓口径尚未覆盖，可能返回方案暂不可用；不会自动按空仓生成账户操作。</p>}
    {visible && current && !matches && <p className="perpetual-research-warning" role="status">原研究使用的是另一份已保存设置，不代表当前设置的新预测。请先核对原回执；新研究需使用当前保存的新原件。</p>}
    {accepted ? <div role="status"><strong>研究请求已接受</strong><p>已接受原研究请求；不表示报告或预测已经完成，也不表示已通过金融资格核验或开始模拟成交。</p><a className="perpetual-research-progress" href={`#/progress?run=${encodeURIComponent(accepted.run_id)}&back=${encodeURIComponent('#/settings')}`}>查看原研究进度</a></div>
      : closed ? <p role="status">服务端已关闭未受理的原请求。原请求与关闭回执已保留；晚到的原提交不能创建研究。现在可显式发起新研究。</p> : rejected ? <p role="status">服务端已明确拒绝这笔请求：同一研究仍在进行，未创建新研究回执。原请求和拒绝记录已保留，可稍后显式发起新研究。</p> : visible && <p role="status">原研究请求结果待确认，尚未再次提交。</p>}
    <div className="action-buttons">
      {(!visible || accepted && !matches) && <button data-research-start type="button" disabled={!saved || disabled || busy || !ready || storageBad} onClick={() => void start()}>{visible ? '按当前已保存设置发起新研究' : `研究 ${asset} ${hours} 小时方案`}</button>}
      {(accepted && matches || rejected || closed) && <button type="button" disabled={!saved || disabled || busy || !ready || storageBad} onClick={() => void start()}>重新研究当前已保存设置</button>}
      {visible && !accepted && !closed && missingRequestId===visible.command.request_id && <button type="button" disabled={busy} onClick={()=>void closeUnaccepted()}>关闭未受理的原请求</button>}
      {visible && !rejected && <button type="button" disabled={busy} onClick={() => void recover(visible)}>核对原研究回执</button>}
    </div>
    {visible && !accepted && !closed && missingRequestId===visible.command.request_id && <p>关闭只停止这笔尚未受理的请求；若已受理会恢复原研究。关闭不会取消已有研究，也不会自动发起新研究。关闭结果不明时请继续核对原回执。</p>}
    {accepted && matches && <p>重新研究将发起一次新研究，可能产生新的模型用量；使用同一份已保存设置。服务端仍会核验是否已有活跃研究，不会自动再次提交。</p>}
    {historicalIntents.length > 0 && <details data-research-history key={`${ownerId}:${partitionKey}:${horizon}`}>
      <summary>历史研究回执（{historicalIntents.length}）</summary>
      {historicalIntents.map((item, index) => <div key={item.command.request_id}>
        <button type="button" disabled={busy} onClick={() => void recover(item, true)}>核对历史回执 {index + 1}</button>
        <p>原请求编号：{item.command.request_id}</p>
      </div>)}
    </details>}
    {historicalReceipt?.owner_id === ownerId && <p>历史请求原回执已确认；不表示研究已完成。<a href={`#/progress?run=${encodeURIComponent(historicalReceipt.run_id)}&back=${encodeURIComponent('#/settings')}`}>查看历史研究进度</a></p>}
    {message && <p role="alert">{message}</p>}
  </section>
}
