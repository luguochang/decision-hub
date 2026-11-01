import { useEffect, useRef, useState } from 'react'
import { freezeSubmission, pendingSubmissions, receivedSubmissions, rememberSubmission, submissionConflicts, submissionJournalEvent, submissionRefusalCodes, volatileSubmissions, type SubmissionKind, type SubmissionOutcome, type SubmissionReceipt } from './submissionReceipts'

const busyRequests = new Set<string>()
const notify = () => window.dispatchEvent(new Event(submissionJournalEvent))

export function useSubmission(kind: SubmissionKind, payload: unknown, context: string, send: (payload: unknown, key: string) => Promise<SubmissionReceipt>, onConfirmed: (receipt: SubmissionReceipt, original: unknown, originHash: string) => void) {
  const [, refresh] = useState(0)
  const [errorState, setErrorState] = useState<{ context: string; error: Error; requestId?: string } | null>(null)
  const [busyContext, setBusyContext] = useState<string | null>(null)
  const mounted = useRef(false)
  const epoch = useRef(0)
  const token = useRef(0)
  const currentContext = JSON.stringify([context, payload])
  const activeContext = useRef(currentContext)
  if (activeContext.current !== currentContext) { epoch.current += 1; activeContext.current = currentContext }
  const confirmedCallback = useRef(onConfirmed)
  confirmedCallback.current = onConfirmed
  useEffect(() => {
    mounted.current = true
    const update = () => refresh(value => value + 1)
    const leave = () => { epoch.current += 1; update() }
    update()
    window.addEventListener(submissionJournalEvent, update)
    window.addEventListener('popstate', leave)
    window.addEventListener('hashchange', leave)
    return () => {
      mounted.current = false; epoch.current += 1
      window.removeEventListener(submissionJournalEvent, update)
      window.removeEventListener('popstate', leave)
      window.removeEventListener('hashchange', leave)
    }
  }, [])
  let readError: Error | null = null
  let outcomes: SubmissionOutcome[] = [], pending: ReturnType<typeof pendingSubmissions> = []
  try { pending = pendingSubmissions(kind); outcomes = receivedSubmissions(kind) }
  catch { readError = new Error('本标签页的提交凭据暂时无法读取，尚未发送新请求。请保留页面与原文后核对浏览器存储。'); outcomes = volatileSubmissions(kind) }
  const conflicts = submissionConflicts(kind)
  const signature = JSON.stringify(payload)
  const renderedHash = window.location.hash
  const renderedEpoch = epoch.current
  const originalPending = pending.find(record => record.signature === signature)
  const busy = Boolean((originalPending && busyRequests.has(originalPending.id)) || busyContext === currentContext)
  const alreadyReceived = errorState?.requestId && outcomes.some(outcome => outcome.record.request.id === errorState.requestId)
  const error = readError ?? (errorState?.context === currentContext && (!alreadyReceived || errorState.error.message.startsWith('接收回执与本标签页已有请求绑定不一致')) ? errorState.error : null)
  const refusal = error && submissionRefusalCodes.has(error.message)
  const execute = () => {
    if (payload == null || readError || busy || refusal) return
    let saved: ReturnType<typeof freezeSubmission>
    try {
      const pending = pendingSubmissions(kind).find(record => record.signature === signature)
      if (pending && conflicts.some(conflict => conflict.record.request.id === pending.id)) throw new Error('接收回执与本标签页已有请求绑定不一致，原记录未覆盖或清理。请先核对原始记录，尚未发送新请求。')
      const known = pending && receivedSubmissions(kind).find(outcome => outcome.record.request.id === pending.id)
      if (known) { refresh(value => value + 1); return }
      saved = freezeSubmission(kind, payload)
    } catch (caught) { setErrorState({ context: currentContext, error: caught instanceof Error ? caught : new Error('无法保存本次提交凭据，尚未发送请求。') }); return }
    if (busyRequests.has(saved.id)) return
    const site = { context: currentContext, hash: window.location.hash, epoch: epoch.current, token: ++token.current }
    const live = () => mounted.current && activeContext.current === site.context && window.location.hash === site.hash && epoch.current === site.epoch && token.current === site.token
    busyRequests.add(saved.id); setBusyContext(currentContext); setErrorState(null); notify()
    void (async () => {
      try {
        const receipt = await send(saved.payload, saved.id)
        // A late success is saved independently of the old component's lifetime.
        const outcome = rememberSubmission(kind, saved, site.hash, receipt)
        notify()
        if (live() && outcome.persisted && !outcome.cleanupPending) confirmedCallback.current(receipt, saved.payload, site.hash)
      } catch (caught) {
        if (live()) setErrorState({ context: site.context, requestId: saved.id, error: caught instanceof Error ? caught : new Error('提交结果尚未确认，原提交凭据已保留。') })
      } finally {
        busyRequests.delete(saved.id)
        if (mounted.current) setBusyContext(previous => previous === site.context ? null : previous)
        notify()
      }
    })()
  }
  const open = (outcome: SubmissionOutcome) => {
    if (!mounted.current || activeContext.current !== currentContext || window.location.hash !== renderedHash || epoch.current !== renderedEpoch) return
    // This is an explicit opening of a locally associated receipt, not a fresh
    // server check of request identity or a claim that research has completed.
    confirmedCallback.current(outcome.record.receipt, outcome.record.request.payload, outcome.record.origin_hash)
  }
  const alreadyConfirmedPending = originalPending && outcomes.some(outcome => outcome.record.request.id === originalPending.id)
  const conflictedPending = originalPending && conflicts.some(conflict => conflict.record.request.id === originalPending.id)
  return { busy, error, blocked: Boolean(readError || alreadyConfirmedPending || conflictedPending || refusal), pending, outcomes, conflicts, originalPending, execute, open }
}
export type SubmissionState = ReturnType<typeof useSubmission>
