import { useEffect, useRef, useState } from 'react'
import { checkedRecoveryReceipt, readRecoveryIntent, reportRecoveryApi, saveRecoveryIntent,
  type ReportRecoveryIntent, type ReportRecoveryReceipt } from '../api/reportRecovery'
import type { ReportRecoveryStatus } from '../api/researchQuality'
import { ApiRequestError } from '../api/client'

const owner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const busyRequests = new Set<string>()
export function ReportRecoveryCommand({ runId, status, onAccepted }: {
  runId: string; status: ReportRecoveryStatus | null; onAccepted: () => void
}) {
  const [intent, setIntent] = useState<ReportRecoveryIntent | null>(null)
  const [receipt, setReceipt] = useState<ReportRecoveryReceipt | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const active = useRef<AbortController | null>(null)
  const callback = useRef(onAccepted); callback.current = onAccepted

  const perform = async (saved: ReportRecoveryIntent, allowPost: boolean, version: number) => {
    if (busyRequests.has(saved.command.request_id)) { setBusy(false); return }
    busyRequests.add(saved.command.request_id)
    const controller = new AbortController(); active.current = controller
    const live = () => generation.current === version && !controller.signal.aborted
    if (live()) { setBusy(true); setError(null); setReceipt(null) }
    try {
      let found = await reportRecoveryApi.receipt(saved, controller.signal)
      let posted: ReportRecoveryReceipt | undefined
      if (found === null && allowPost && live()) {
        posted = await reportRecoveryApi.submit(saved, controller.signal)
        found = await reportRecoveryApi.receipt(saved, controller.signal)
      }
      if (!live()) return
      if (found === null) throw new Error('尚未找到原恢复请求的完整回执。请求键已保留，可核对并按原请求重试。')
      const checked = checkedRecoveryReceipt(found, saved, posted)
      setReceipt(checked)
      if (checked.status === 'accepted') callback.current()
    } catch (error) {
      if (live()) setError(error instanceof ApiRequestError && error.status === 403
        ? '本次用户身份与原任务创建记录未通过核对，无法确认恢复请求。请保留原请求并核对原任务归属。'
        : error instanceof ApiRequestError && error.status === 409
        ? '原来源、旧版本、保存材料或请求回执尚未通过核对，无法确认恢复请求。原请求已保留；可核对回执，系统不会自动重新提交。'
        : '恢复请求尚未确认。原请求已保留，请先核对回执；这不代表报告已保存或研究已重新执行。')
    } finally {
      busyRequests.delete(saved.command.request_id)
      if (active.current === controller) active.current = null
      if (live()) setBusy(false)
    }
  }
  useEffect(() => {
    const version = ++generation.current
    setIntent(null); setReceipt(null); setBusy(false); setError(null)
    try {
      const saved = readRecoveryIntent(runId, owner)
      setIntent(saved)
      if (saved) void perform(saved, false, version)
    } catch (error) { setError(error instanceof Error ? error.message : '原恢复请求无法读取。') }
    return () => { generation.current += 1; active.current?.abort(); active.current = null }
    // A returned page reconciles the retained request with a GET only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])

  const applicable = status?.run_id === runId && status.action === 'owner_request_required'
  const sameTarget = Boolean(intent && status && intent.command.expected_request_id === status.request_id
    && intent.command.expected_generation === status.generation && intent.command.expected_state_version === status.state_version)
  const unresolved = Boolean(intent && receipt?.status !== 'accepted')
  const execute = async () => {
    if (!applicable || !status || busy || unresolved || error || sameTarget) return
    const version = generation.current
    setBusy(true)
    try {
      const saved = await saveRecoveryIntent(runId, owner, status, receipt)
      if (generation.current !== version) return
      setIntent(saved); setReceipt(null)
      void perform(saved, true, version)
    } catch (error) {
      if (generation.current === version) { setBusy(false); setError(error instanceof Error ? error.message : '恢复请求凭据未保存，未发送请求。') }
    }
  }
  if (!applicable && !intent && !error) return null
  return <div className="product-callout" aria-label="报告整理恢复回执">
    {applicable && !unresolved && !sameTarget && <><p>可请求恢复这次报告整理。服务端会重新核对原任务、来源、旧版本和保存材料；请求被接受后仍需完成整理、保存与独立确认。</p>
      <button className="button secondary" disabled={busy || Boolean(error)} onClick={execute}>恢复本次报告整理</button></>}
    {intent && <>
      {busy && <p role="status">正在核对原恢复请求，尚未确认报告已保存。</p>}
      {receipt?.status === 'accepted' ? <p role="status">已确认请求恢复本次报告整理。未新建研究；报告保存与确认状态以上方最新读取结果为准。</p>
        : <p>这笔恢复请求尚未确认，当前不能发送另一笔恢复请求。</p>}
      <button className="button secondary" disabled={busy} onClick={() => void perform(intent, false, generation.current)}>核对原恢复回执</button>
      {receipt?.status !== 'accepted' && <button className="button secondary" disabled={busy}
        onClick={() => void perform(intent, true, generation.current)}>核对并按原请求重试恢复</button>}
      <details><summary>查看原恢复请求与回执</summary><pre>{JSON.stringify({ intent, receipt }, null, 2)}</pre></details>
    </>}
    {error && <p role="alert">{error}</p>}
  </div>
}

import { preparationRecoveryApi, readPreparationRecoveryIntent, savePreparationRecoveryIntent,
  checkedPreparationRecoveryReceipt, samePreparationRecoveryTarget, type PreparationRecoveryStatus,
  type PreparationRecoveryIntent, type PreparationRecoveryReceipt } from '../api/reportRecovery'

const preparationBusy79 = new Set<string>()
export function PreparationRecoveryCommand({ runId, stateVersion, allowRecovery, onAccepted }: {
  runId: string; stateVersion: number | null; allowRecovery: boolean; onAccepted: () => void
}) {
  const [status, setStatus] = useState<PreparationRecoveryStatus | null>(null)
  const [intent, setIntent] = useState<PreparationRecoveryIntent | null>(null)
  const [receipt, setReceipt] = useState<PreparationRecoveryReceipt | null>(null)
  const [notFoundIntent, setNotFoundIntent] = useState<PreparationRecoveryIntent | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const epoch = useRef(0), active = useRef<AbortController | null>(null), busyRef = useRef(false)
  const notified = useRef<string | null>(null), callback = useRef(onAccepted); callback.current = onAccepted
  const gate = useRef({ runId, stateVersion, allowRecovery }); gate.current = { runId, stateVersion, allowRecovery }
  const liveGate = () => gate.current.runId === runId && gate.current.allowRecovery && gate.current.stateVersion === stateVersion
  const perform = async (saved: PreparationRecoveryIntent, allowPost: boolean, version: number) => {
    const request = JSON.stringify([saved.owner_id, saved.run_id, saved.command.request_id])
    if (busyRef.current || preparationBusy79.has(request)) return
    busyRef.current = true; preparationBusy79.add(request)
    const controller = new AbortController(); active.current = controller
    const live = () => epoch.current === version && !controller.signal.aborted
    if (live()) { setBusy(true); setError(null); setReceipt(null) }
    try {
      let found = await preparationRecoveryApi.receipt(saved, controller.signal)
      if (live()) setNotFoundIntent(found === null ? saved : null)
      let posted: PreparationRecoveryReceipt | undefined
      if (found === null && allowPost && live() && liveGate() && status?.run_id === runId
        && status.state_version === stateVersion && samePreparationRecoveryTarget(saved, status)) {
        posted = await preparationRecoveryApi.submit(saved, controller.signal)
        if (!live()) return
        found = await preparationRecoveryApi.receipt(saved, controller.signal)
        if (live()) setNotFoundIntent(found === null ? saved : null)
      }
      if (!live()) return
      if (found === null) { setError('原键已核对为尚未受理。请核对当前准备恢复条件，再显式按原键重试；若条件已变，可显式归档旧意图后请求当前条件。'); return }
      const value = checkedPreparationRecoveryReceipt(found, saved, posted)
      setReceipt(value)
      if (notified.current !== request) { notified.current = request; callback.current() }
    } catch { if (live()) { setNotFoundIntent(null); setError('原准备恢复请求尚未确认，原键保留。来源、归属、档案或提交回执未通过核对时，不能据此重新提交或读取报告。') } }
    finally {
      preparationBusy79.delete(request)
      if (active.current === controller) { busyRef.current = false; active.current = null; if (live()) setBusy(false) }
    }
  }
  useEffect(() => {
    const version = ++epoch.current
    busyRef.current = false
    setStatus(null); setIntent(null); setReceipt(null); setNotFoundIntent(null); setBusy(false); setError(null); notified.current = null
    try {
      const saved = readPreparationRecoveryIntent(runId, owner)
      setIntent(saved)
      if (saved) void perform(saved, false, version)
    } catch (error) { setError(error instanceof Error ? error.message : '原准备恢复请求无法读取，请保留现场。') }
    return () => { epoch.current += 1; active.current?.abort(); active.current = null }
    // Returned report reconciles only the retained key with GET, never POST.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])
  useEffect(() => { setStatus(null) }, [stateVersion, allowRecovery])
  const check = () => {
    if (!liveGate() || busyRef.current) return
    const version = epoch.current, controller = new AbortController(); active.current = controller
    busyRef.current = true; setBusy(true); setStatus(null); setError(null)
    void preparationRecoveryApi.status(runId, owner, controller.signal).then(value => {
      if (epoch.current === version && !controller.signal.aborted && liveGate() && value.state_version === stateVersion) setStatus(value)
    }).catch(() => {
      if (epoch.current === version && !controller.signal.aborted) { setStatus(null); setNotFoundIntent(null); setError('计划、原拥有者、来源或终态尚未核对，未发送恢复请求。') }
    }).finally(() => {
      if (active.current === controller) { busyRef.current = false; active.current = null; if (epoch.current === version) setBusy(false) }
    })
  }
  const execute = async () => {
    if (!status?.eligible || status.run_id !== runId || status.state_version !== stateVersion || !liveGate() || busyRef.current) return
    const version = epoch.current
    try {
      const saved = await savePreparationRecoveryIntent(runId, owner, status, receipt, notFoundIntent)
      if (epoch.current !== version || !liveGate()) return
      setIntent(saved); setReceipt(null); setNotFoundIntent(null)
      void perform(saved, true, version)
    } catch (error) { if (epoch.current === version) setError(error instanceof Error ? error.message : '准备恢复请求键未保存，未发送请求。') }
  }
  if (!allowRecovery && !intent && !error) return null
  return <div className="product-callout" aria-label="原计划报告准备恢复">
    {allowRecovery && <><p>原计划研究已完成模型计算，报告准备的来源授权检查未通过。可核对原计划和归属后恢复同一 Run 的报告准备；不会新建研究或调用模型，原严格来源检查仍须通过。</p>
      <button className="button secondary" disabled={busy} onClick={check}>核对准备恢复条件</button>
      {status?.run_id === runId && status.eligible && status.state_version === stateVersion && <button className="button secondary" disabled={busy} onClick={() => void execute()}>恢复原计划报告准备</button>}</>}
    {busy && <p role="status">正在核对原准备恢复请求，尚未确认报告保存。</p>}
    {intent?.run_id === runId && <>
      <p>{receipt?.run_id === runId ? '同 Run 准备恢复请求已受理；报告仍需正常准备、保存并独立确认，受理不代表 ACK。' : '原准备恢复请求尚未确认，当前不能另发一键。'}</p>
      <button className="button secondary" disabled={busy} onClick={() => void perform(intent, false, epoch.current)}>核对原准备恢复回执</button>
      {!receipt && status?.eligible && samePreparationRecoveryTarget(intent, status) && <button className="button secondary" disabled={busy}
        onClick={() => void perform(intent, true, epoch.current)}>核对并按原键重试准备恢复</button>}
    </>}
    {error && <p role="alert">{error}</p>}
  </div>
}
