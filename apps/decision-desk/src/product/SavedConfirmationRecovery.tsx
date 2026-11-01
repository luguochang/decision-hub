import { useEffect, useRef, useState } from 'react'
import { ZodError } from 'zod'
import { ApiRequestError } from '../api/client'
import { checkedSavedConfirmationReceipt, readSavedConfirmationIntent, savedConfirmationApi,
  saveSavedConfirmationIntent, sameSavedConfirmationTarget, type SavedConfirmationIntent,
  type SavedConfirmationReceipt, type SavedConfirmationStatus } from '../api/savedConfirmation'

const owner = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const busyRequests = new Set<string>()
export function SavedConfirmationRecovery({ runId, stateVersion, allowAuthorization, reportConfirmed, onAccepted }: {
  runId: string; stateVersion: number | null; allowAuthorization: boolean; reportConfirmed: boolean
  onAccepted: (receipt: SavedConfirmationReceipt) => void
}) {
  const [status, setStatus] = useState<SavedConfirmationStatus | null>(null)
  const [intent, setIntent] = useState<SavedConfirmationIntent | null>(null)
  const [receipt, setReceipt] = useState<SavedConfirmationReceipt | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const epoch = useRef(0)
  const busyRef = useRef(false)
  const active = useRef<AbortController | null>(null)
  const notifiedRequest = useRef<string | null>(null)
  const callback = useRef(onAccepted); callback.current = onAccepted
  const authorization = useRef({ runId, stateVersion, enabled: allowAuthorization && stateVersion !== null && !reportConfirmed })
  authorization.current = { runId, stateVersion, enabled: allowAuthorization && stateVersion !== null && !reportConfirmed }
  const authorizationAllowed = (expectedVersion = stateVersion) => authorization.current.runId === runId
    && authorization.current.enabled && authorization.current.stateVersion === expectedVersion
  const errorText = (error: unknown) => error instanceof ZodError
    ? '补确认返回内容未通过核对，原请求已保留；当前不能确认结果或重新提交。'
    : error instanceof ApiRequestError && error.status === 403
    ? '本次用户身份与原任务创建记录未通过核对，不能补确认。原请求保留，请核对任务归属。'
    : error instanceof ApiRequestError && error.status === 404
      ? '尚未找到可核对的已保存报告记录，不能据此发送补确认请求。'
      : error instanceof ApiRequestError && error.status === 409
        ? '原报告、材料、代次或回执尚未通过核对。原请求已保留，系统不会自动重新提交。'
        : error instanceof Error && !(error instanceof ApiRequestError) ? error.message
          : '补确认结果尚未确认。原请求保留；请先核对回执，这不代表报告已独立确认。'
  const perform = async (saved: SavedConfirmationIntent, allowPost: boolean, version: number) => {
    const requestKey = JSON.stringify([saved.owner_id, saved.run_id, saved.command.request_id])
    if (busyRef.current || busyRequests.has(requestKey)) return
    busyRef.current = true; busyRequests.add(requestKey)
    const controller = new AbortController(); active.current = controller
    const live = () => epoch.current === version && !controller.signal.aborted
    if (live()) { setBusy(true); setError(null); setReceipt(null) }
    try {
      let found = await savedConfirmationApi.receipt(saved, controller.signal)
      let posted: SavedConfirmationReceipt | undefined
      if (found === null && allowPost && live() && authorizationAllowed(saved.command.expected_state_version)) {
        posted = await savedConfirmationApi.submit(saved, controller.signal)
        if (!live()) return
        found = await savedConfirmationApi.receipt(saved, controller.signal)
      }
      if (!live()) return
      if (found === null) throw new Error(authorizationAllowed(saved.command.expected_state_version)
        ? '尚未找到原补确认请求的完整回执。请求键已保留，可核对并按原请求重试。'
        : '尚未找到原补确认请求的完整回执。原请求键已保留，当前仅可核对原回执，未重新提交。')
      const checked = checkedSavedConfirmationReceipt(found, saved, posted)
      setReceipt(checked)
      if (notifiedRequest.current !== requestKey) { notifiedRequest.current = requestKey; callback.current(checked) }
    } catch (error) { if (live()) setError(errorText(error)) }
    finally {
      busyRequests.delete(requestKey)
      if (active.current === controller) { active.current = null; busyRef.current = false }
      if (live()) setBusy(false)
    }
  }
  useEffect(() => {
    const version = ++epoch.current
    active.current?.abort(); active.current = null; busyRef.current = false
    notifiedRequest.current = null
    setStatus(null); setIntent(null); setReceipt(null); setBusy(false); setError(null)
    try {
      const saved = readSavedConfirmationIntent(runId, owner)
      setIntent(saved)
      if (saved) void perform(saved, false, version)
    } catch (error) { setError(errorText(error)) }
    return () => { epoch.current += 1; active.current?.abort(); active.current = null; busyRef.current = false }
    // Returning to this run reconciles only the retained request; it never posts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId])
  useEffect(() => { setStatus(null) }, [stateVersion, allowAuthorization, reportConfirmed])
  const checkConditions = async () => {
    if (busyRef.current || !authorizationAllowed() || stateVersion === null) return
    busyRef.current = true; setBusy(true); setError(null); setStatus(null)
    const version = epoch.current
    const controller = new AbortController(); active.current = controller
    const live = () => epoch.current === version && !controller.signal.aborted
    try {
      const value = await savedConfirmationApi.status(runId, owner, stateVersion, controller.signal)
      if (live() && authorizationAllowed()) setStatus(value)
    } catch (error) { if (live()) setError(errorText(error)) }
    finally {
      if (active.current === controller) { active.current = null; busyRef.current = false }
      if (live()) setBusy(false)
    }
  }
  const currentStatus = authorizationAllowed() && status?.run_id === runId && status.state_version === stateVersion ? status : null
  const currentIntent = intent?.run_id === runId && intent.owner_id === owner ? intent : null
  const currentReceipt = receipt?.run_id === runId && receipt.owner_id === owner ? receipt : null
  const unresolved = Boolean(currentIntent && !currentReceipt)
  const sameTarget = Boolean(currentIntent && currentStatus && sameSavedConfirmationTarget(currentIntent, currentStatus))
  const execute = async () => {
    if (busyRef.current || !authorizationAllowed() || !currentStatus?.eligible || unresolved || sameTarget || error) return
    busyRef.current = true; setBusy(true)
    const version = epoch.current
    try {
      const saved = await saveSavedConfirmationIntent(runId, owner, currentStatus, currentReceipt)
      if (epoch.current !== version) return
      setIntent(saved); setReceipt(null)
      busyRef.current = false
      void perform(saved, authorizationAllowed(), version)
    } catch (error) {
      if (epoch.current === version) { busyRef.current = false; setBusy(false); setError(errorText(error)) }
    }
  }
  if (!authorizationAllowed() && !currentIntent && !error) return null
  return <div className="product-callout" aria-label="报告补确认与原回执">
    <p>可核对原报告的补确认条件或保留的原请求回执；不会新建研究或重新调用模型。请求被接受仍不代表报告已独立确认。</p>
    {authorizationAllowed() && <button className="button secondary" disabled={busy} onClick={() => void checkConditions()}>核对补确认条件</button>}
    {!authorizationAllowed() && currentIntent && <p>当前仅提供原补确认回执的只读核对入口；是否已独立确认，以上方最新报告状态为准。不会重新提交请求。</p>}
    {busy && <p role="status">正在核对条件或原请求回执；回执只证明请求接受，报告是否独立确认请以上方最新状态为准。</p>}
    {currentStatus && !currentStatus.eligible && <p>当前尚不具备补确认条件。是否已独立确认，请以上方最新报告状态为准；系统不会自动提交。</p>}
    {currentStatus?.eligible && !unresolved && !sameTarget && <button className="button secondary" disabled={busy || Boolean(error)}
      onClick={() => void execute()}>继续确认这份已保存报告</button>}
    {currentIntent && <>
      {currentReceipt ? <p role="status">{reportConfirmed
        ? '原补确认请求已被接受；最新报告状态已确认。原回执只证明请求接受，报告确认依据为上方最新读取状态。'
        : '原补确认请求已被接受；当前尚未确认报告已独立确认。原回执只证明请求接受，以上方最新报告状态为准。'}</p>
        : <p>这笔补确认请求尚未确认，当前不能发送另一笔请求。原请求键已保留。</p>}
      <button className="button secondary" disabled={busy} onClick={() => void perform(currentIntent, false, epoch.current)}>核对原补确认回执</button>
      {currentIntent && authorizationAllowed(currentIntent.command.expected_state_version) && !currentReceipt && <button className="button secondary" disabled={busy}
        onClick={() => void perform(currentIntent, true, epoch.current)}>核对并按原请求重试补确认</button>}
      <details><summary>查看原补确认请求与回执</summary><pre>{JSON.stringify({ intent: currentIntent, receipt: currentReceipt }, null, 2)}</pre></details>
    </>}
    {error && <p role="alert">{error}</p>}
  </div>
}
