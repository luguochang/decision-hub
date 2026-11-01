import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ResearchRunCommand, ResearchRunCommandReceipt } from '../api/types'
import { acknowledgeCommand, checkedCommandReceipt, checkedCommandResult, commandJournalEvent, createPendingCommand, readCommandLocator, readPendingCommand, rememberCommandLocator, type PendingCommand } from './commandRequests'

// A synchronous guard also covers two mounted entry points in the same tab.
const busyRequests = new Set<string>()
type Outcome = { receipt: ResearchRunCommandReceipt; cleanupPending: boolean }
type CallSite = { runId: string; hash: string }

export function useRunCommand(runId: string | null | undefined, owner: string, onConfirmed?: (receipt: ResearchRunCommandReceipt) => void) {
  const queryClient = useQueryClient()
  const [revision, setRevision] = useState(0)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, Error | undefined>>({})
  const [outcomes, setOutcomes] = useState<Record<string, Outcome | undefined>>({})
  const mounted = useRef(false)
  const activeRun = useRef(runId)
  const activeOwner = useRef(owner)
  const confirmCallback = useRef(onConfirmed)
  activeRun.current = runId
  activeOwner.current = owner
  confirmCallback.current = onConfirmed
  const snapshot = useMemo(() => {
    try {
      const pending = runId ? readPendingCommand(runId, owner) : null
      return { pending, locator: runId && !pending ? readCommandLocator(runId, owner) : null, error: undefined }
    }
    catch (error) { return { pending: null, locator: null, error: error instanceof Error ? error : new Error('任务操作凭据无法读取，尚未发送新操作。') } }
  }, [runId, owner, revision])
  const pending = snapshot.pending
  const locator = snapshot.locator
  const currentOutcome = runId ? (outcomes[runId]?.receipt.owner_id === owner ? outcomes[runId] : undefined) ?? Object.values(outcomes).find(outcome => outcome?.receipt.owner_id === owner && outcome.receipt.result.status !== 'rejected' && outcome.receipt.result.target_run_id === runId) : undefined
  const currentError = snapshot.error ?? (runId ? errors[runId] : undefined)
  const renderedHash = window.location.hash
  const live = (site: CallSite) => mounted.current && activeRun.current === site.runId && window.location.hash === site.hash
  const notify = () => window.dispatchEvent(new Event(commandJournalEvent))
  useEffect(() => {
    mounted.current = true
    const refresh = () => setRevision(value => value + 1)
    window.addEventListener(commandJournalEvent, refresh)
    return () => { mounted.current = false; window.removeEventListener(commandJournalEvent, refresh) }
  }, [])

  const perform = async (saved: PendingCommand, allowPost: boolean, site: CallSite, explicit: boolean, known?: ResearchRunCommandReceipt) => {
    const key = saved.record.id
    if (busyRequests.has(key)) return
    busyRequests.add(key)
    notify()
    if (live(site)) setErrors(previous => ({ ...previous, [site.runId]: undefined }))
    try {
      let receipt = await api.researchCommandReceipt(saved.intent.run_id, saved.intent.command.request_id, saved.intent.owner_id)
      let posted
      if (receipt === null && allowPost) {
        // Leaving during the initial lookup keeps the intent for a later explicit retry.
        if (!live(site)) return
        if (saved.legacy) throw new Error('旧任务操作凭据缺少冻结的用户身份，未找到原回执；不能直接重发，请先核对原始记录。')
        posted = checkedCommandResult(await api.researchCommand(saved.intent.run_id, saved.intent.command, saved.intent.owner_id), saved)
        receipt = await api.researchCommandReceipt(saved.intent.run_id, saved.intent.command.request_id, saved.intent.owner_id)
        if (receipt === null) throw new Error('任务操作尚无可核对的完整回执，原请求已保留。请稍后核对，暂不发送新操作。')
      }
      if (receipt === null) {
        if (known || locator?.record.id === key) throw new Error('任务操作原请求键已保留，但当前无法重新读取完整回执。请刷新后核对，尚未发送新操作。')
        return
      }
      const confirmed = checkedCommandReceipt(receipt, saved, posted ?? known?.result)
      void queryClient.invalidateQueries({ queryKey: ['research-runs'] })
      void queryClient.invalidateQueries({ queryKey: ['research-run', saved.intent.run_id] })
      void queryClient.invalidateQueries({ queryKey: ['design-failed-run', saved.intent.run_id] })
      if (live(site)) {
        // An unmounted/other-route observer must retain the original intent for a fresh GET on return.
        const currentPointer = readCommandLocator(saved.intent.run_id, saved.intent.owner_id)
        const currentPending = readPendingCommand(saved.intent.run_id, saved.intent.owner_id)
        if (currentPointer && currentPointer.record.id !== key && currentPending?.record.id !== key) return
        rememberCommandLocator(saved)
        const cleaned = acknowledgeCommand(saved)
        setOutcomes(previous => ({ ...previous, [site.runId]: { receipt: confirmed, cleanupPending: !cleaned } }))
        setDrafts(previous => ({ ...previous, [site.runId]: confirmed.result.status === 'rejected' ? saved.intent.command.reason : '' }))
        setErrors(previous => ({ ...previous, [site.runId]: undefined }))
        if (explicit && confirmed.result.status !== 'rejected' && cleaned) confirmCallback.current?.(confirmed)
      }
    } catch (error) {
      if (live(site)) setErrors(previous => ({ ...previous, [site.runId]: error instanceof Error ? error : new Error('任务操作未确认，原请求已保留。') }))
    } finally {
      busyRequests.delete(key)
      notify()
    }
  }

  useEffect(() => {
    if (pending && runId) void perform(pending, false, { runId, hash: window.location.hash }, false)
    // Journal changes refresh UI, but do not repeatedly poll the same request.
    // No effect is allowed to submit a command.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, owner, pending?.record.id])

  useEffect(() => {
    if (!runId || pending || snapshot.error) return
    const site = { runId, hash: window.location.hash }
    const controller = new AbortController()
    let active = true
    const current = () => active && live(site) && activeOwner.current === owner
    void (async () => {
      try {
        const receipt = await api.researchLinkedCommandReceipt(runId, owner, controller.signal)
        if (!current()) return
        const currentPointer = readCommandLocator(runId, owner)
        const pointerChanged = currentPointer?.record.id !== locator?.record.id || currentPointer?.record.signature !== locator?.record.signature
        if ((pointerChanged && currentPointer?.record.id !== receipt?.request_id) || readPendingCommand(runId, owner)) return
        if (receipt === null) {
          if (locator) throw new Error('任务操作原请求键已保留，但后台尚未返回当前操作原回执，请刷新后核对。')
          return
        }
        const intent = { schema_version: 'research-command-intent.v1' as const, run_id: receipt.source_run_id, owner_id: owner, command: receipt.command }
        const saved = { intent, legacy: false, record: { id: receipt.request_id, signature: JSON.stringify(intent), payload: intent, createdAt: receipt.result.created_at } }
        const own = receipt.source_run_id === runId
        const confirmed = checkedCommandReceipt(receipt, own && currentPointer?.record.id === receipt.request_id ? currentPointer : saved)
        if (own) {
          // The display endpoint authenticates the latest DB operation, including
          // receipts acknowledged before this browser locator existed.
          if (readPendingCommand(runId, owner)) return
          rememberCommandLocator(saved, true)
          setOutcomes(previous => ({ ...previous, [runId]: { receipt: confirmed, cleanupPending: false } }))
          setErrors(previous => ({ ...previous, [runId]: undefined }))
          notify()
          return
        }
        if (locator) throw new Error('任务操作原请求键已保留，但后台未返回当前操作原回执；父研究来源不能替代本次操作。')
        if (confirmed.result.status !== 'accepted' || !['retry', 'recheck'].includes(confirmed.command.command) || confirmed.result.target_run_id !== runId || confirmed.source_run_id === runId) {
          throw Object.assign(new Error('任务操作关联回执与当前研究不一致，尚未恢复原操作。'), { originalRecord: { returned: receipt, expected_target_run_id: runId, expected_owner_id: owner } })
        }
        // Discovery restores an existing receipt; only explicit actions can open
        // another Run or submit a command. The pending journal is untouched.
        const originalPending = readPendingCommand(confirmed.source_run_id, owner)
        const cleanupPending = originalPending?.intent.command.request_id === confirmed.request_id
        setOutcomes(previous => {
          const existing = previous[confirmed.source_run_id]
          if (previous[runId]?.receipt.owner_id === owner || (existing?.receipt.owner_id === owner && existing.receipt.request_id === confirmed.request_id)) return previous
          return { ...previous, [confirmed.source_run_id]: { receipt: confirmed, cleanupPending } }
        })
      } catch (error) {
        if (current() && !controller.signal.aborted) setErrors(previous => ({ ...previous, [runId]: error instanceof Error ? error : new Error('任务操作关联回执暂时无法读取，尚未恢复原操作。') }))
      }
    })()
    return () => { active = false; controller.abort() }
    // Route/owner changes cancel discovery; journal recovery remains GET-only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, owner, pending?.record.id, Boolean(snapshot.error)])

  const execute = (action: ResearchRunCommand['command'], reason: string) => {
    if (!runId || !reason.trim()) return
    const site = { runId, hash: window.location.hash }
    try {
      const existing = readPendingCommand(runId, owner)
      if (existing) {
        if (busyRequests.has(existing.record.id)) return
        throw new Error('这个任务还有尚未确认的操作，请先核对原请求。尚未发送新操作。')
      }
      const original = readCommandLocator(runId, owner)
      if (original && (currentOutcome?.receipt.source_run_id !== runId || currentOutcome.receipt.request_id !== original.record.id || currentOutcome.receipt.owner_id !== owner)) {
        throw new Error('任务操作原请求键仍待后台核对，请刷新后核对回执，尚未发送新操作。')
      }
      const saved = createPendingCommand(runId, owner, action, reason.trim())
      setDrafts(previous => ({ ...previous, [runId]: saved.intent.command.reason }))
      setOutcomes(previous => Object.fromEntries(Object.entries(previous).filter(([source, outcome]) => source !== runId && outcome?.receipt.result.target_run_id !== runId)))
      void perform(saved, true, site, true)
    } catch (error) {
      setErrors(previous => ({ ...previous, [runId]: error instanceof Error ? error : new Error('任务操作凭据无法保存，尚未发送新操作。') }))
      setRevision(value => value + 1)
    }
  }
  const recover = (allowPost: boolean) => {
    if (!runId) return
    try {
      const saved = readPendingCommand(runId, owner)
      if (saved) void perform(saved, allowPost, { runId, hash: window.location.hash }, true)
      else {
        const original = readCommandLocator(runId, owner)
        if (original) void perform(original, false, { runId, hash: window.location.hash }, false)
      }
    } catch (error) {
      setErrors(previous => ({ ...previous, [runId]: error instanceof Error ? error : new Error('任务操作凭据无法读取，尚未发送新操作。') }))
    }
  }
  const locatorVerified = !locator || Boolean(currentOutcome && currentOutcome.receipt.source_run_id === runId && currentOutcome.receipt.request_id === locator.record.id && currentOutcome.receipt.owner_id === owner)
  const busy = Boolean((pending && busyRequests.has(pending.record.id)) || (locator && busyRequests.has(locator.record.id)) || (currentOutcome && busyRequests.has(currentOutcome.receipt.request_id)))
  return {
    pending, busy, locked: Boolean(pending || snapshot.error || !locatorVerified), error: currentError, outcome: locatorVerified ? currentOutcome : undefined,
    viewingConfirmedTarget: Boolean(locatorVerified && runId && currentOutcome?.receipt.result.status === 'accepted' && currentOutcome.receipt.result.target_run_id === runId),
    reason: pending?.intent.command.reason ?? (runId ? drafts[runId] ?? '' : ''),
    setReason: (reason: string) => { if (runId && !pending && !snapshot.error) setDrafts(previous => ({ ...previous, [runId]: reason })) },
    execute, check: () => recover(false), retryOriginal: () => recover(true),
    openConfirmed: () => {
      if (!locatorVerified || pending || snapshot.error || !mounted.current || activeRun.current !== runId || window.location.hash !== renderedHash || !currentOutcome || !runId || currentOutcome.receipt.result.status === 'rejected' || currentOutcome.cleanupPending) return
      const receipt = currentOutcome.receipt
      const intent = { schema_version: 'research-command-intent.v1' as const, run_id: receipt.source_run_id, owner_id: receipt.owner_id, command: receipt.command }
      void perform({ intent, legacy: false, record: { id: receipt.request_id, signature: JSON.stringify(intent), payload: intent, createdAt: receipt.result.created_at } }, false, { runId, hash: renderedHash }, true, receipt)
    },
  }
}

export type RunCommandState = ReturnType<typeof useRunCommand>
