import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { z } from 'zod'
import { checkedEffectReceipt, EffectTrackingAdmissionRejection, effectTracking, type EffectTrackingIntent, type EffectTrackingRejectionCode, type EffectTrackingRequestRead } from '../api/effectTracking'
import { productOwner } from '../api/delivery'
import { assetTime, horizonName } from './assetFormat'

const identifier = z.string().min(1).max(128).refine(value => value === value.trim())
const intentSchema = z.object({ owner: identifier, study: identifier, revision: identifier, run: identifier, artifact: identifier, id: identifier }).strict()
const operations = new Set<string>()
const listeners = new Set<() => void>()
const changed = () => listeners.forEach(listener => listener())
const journalKey = (study: string) => `decision-hub.effect-tracking.v1:${JSON.stringify([import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin', productOwner, study])}`
const intentIdentity = (value: EffectTrackingIntent) => JSON.stringify([value.owner, value.study, value.revision, value.run, value.artifact, value.id])
const sameIntent = (left: EffectTrackingIntent, right: EffectTrackingIntent) => intentIdentity(left) === intentIdentity(right)
const requestKey = (intent: EffectTrackingIntent) => ['study-effect-request', intent.owner, intent.study, intent.revision, intent.run, intent.artifact, intent.id] as const

type JournalSlot = { storage: Storage; name: string; slot: string; raw: string }
function journalSlots(key: string): JournalSlot[] {
  return [[localStorage, key, 'local'], [localStorage, `${key}:cleanup`, 'local-cleanup'],
    [sessionStorage, key, 'session'], [sessionStorage, `${key}:cleanup`, 'session-cleanup']]
    .flatMap(([storage, name, slot]) => {
      const raw = (storage as Storage).getItem(name as string)
      return raw === null ? [] : [{ storage: storage as Storage, name: name as string, slot: slot as string, raw }]
    })
}
function decodeIntent(raw: string) {
  if (raw.length > 4096) throw new Error('效果跟踪凭据无法读取。')
  return intentSchema.parse(JSON.parse(raw))
}
function journalInventory(key: string, study: string) {
  const entries = journalSlots(key), groups = new Map<string, { intent: EffectTrackingIntent; slots: string[] }>(), damaged: string[] = []
  for (const entry of entries) {
    try {
      const intent = decodeIntent(entry.raw)
      if (intent.owner !== productOwner || intent.study !== study) throw new Error('原凭据不属于当前身份与主题。')
      const identity = intentIdentity(intent), existing = groups.get(identity)
      if (existing) existing.slots.push(entry.slot)
      else groups.set(identity, { intent, slots: [entry.slot] })
    } catch { damaged.push(entry.slot) }
  }
  return { entries, groups: [...groups.values()], damaged }
}
const slotIdentity = (entries: JournalSlot[]) => JSON.stringify(entries.map(entry => [entry.slot, entry.raw]))
function assertSlots(key: string, entries: JournalSlot[]) {
  if (slotIdentity(journalSlots(key)) !== slotIdentity(entries)) throw new Error('本地原凭据已变化，请重新逐笔核对。')
}
function savedRaw(key: string) {
  const entries = journalSlots(key)
  if (!entries.length) return null
  const original = decodeIntent(entries[0].raw)
  if (entries.some(entry => !sameIntent(decodeIntent(entry.raw), original))) throw new Error('效果跟踪凭据存在冲突。')
  return entries[0].raw
}
function savedIntent(key: string, study: string) {
  const raw = savedRaw(key)
  if (raw === null) return null
  const value = decodeIntent(raw)
  if (value.owner !== productOwner || value.study !== study) throw new Error('效果跟踪凭据与当前主题不一致。')
  return value
}
function journalState(key: string, study: string) {
  try {
    const inventory = journalInventory(key, study), recovery = inventory.damaged.length > 0 || inventory.groups.length > 1
    return { ...inventory, recovery, pending: !recovery ? inventory.groups[0]?.intent ?? null : null,
      busy: operations.has(key), error: recovery ? '浏览器保留了多笔或不完整的原操作，暂不能提交新的登记。请逐笔读取原请求。' : null as string | null }
  } catch {
    return { entries: [] as JournalSlot[], groups: [] as { intent: EffectTrackingIntent; slots: string[] }[], damaged: [] as string[], recovery: true,
      pending: null, busy: operations.has(key), error: '浏览器中的原操作凭据无法读取，暂时不能提交。原记录仍保留。' }
  }
}
function assertIntent(key: string, intent: EffectTrackingIntent) {
  const saved = savedIntent(key, intent.study)
  if (!saved || !sameIntent(saved, intent)) throw new Error('原操作凭据已变化，请重新核对。')
}
async function saveIntent(key: string, intent: EffectTrackingIntent) {
  if (!navigator.locks) throw new Error('浏览器暂不能安全锁定原操作凭据，尚未提交。请保留原记录并读取已有计划。')
  // Only the short durable write is locked across tabs; backend work is not.
  await navigator.locks.request(`effect-tracking-journal:${key}`, () => {
    const value = intentSchema.parse(intent), old = savedIntent(key, value.study)
    if (value.owner !== productOwner || (old && !sameIntent(old, value))) throw new Error('另一笔效果跟踪请求尚未确认。')
    const raw = savedRaw(key) ?? JSON.stringify(value)
    localStorage.setItem(key, raw)
    if (localStorage.getItem(key) !== raw) throw new Error('浏览器未保存原操作凭据。')
    assertIntent(key, value)
    changed()
  })
}
async function requestOperation<T>(key: string, operation: () => Promise<T>) {
  if (!navigator.locks) throw new Error('浏览器暂不能安全锁定原请求，尚未提交。')
  // Serialize POST and explicit abandonment across tabs as well as in this page.
  return navigator.locks.request(`effect-tracking-operation:${key}`, operation)
}
async function migrateLegacyIntent(key: string, study: string) {
  const inventory = journalInventory(key, study)
  if (inventory.damaged.length || inventory.groups.length > 1) return
  const value = savedIntent(key, study)
  if (value && localStorage.getItem(key) === null && localStorage.getItem(`${key}:cleanup`) === null) {
    // Copy only the original journal; migration never submits a request.
    await saveIntent(key, value)
  }
}
async function clearIntent(key: string, intent: EffectTrackingIntent, failureText: string) {
  if (!navigator.locks) throw new Error(failureText)
  await navigator.locks.request(`effect-tracking-journal:${key}`, () => {
    const current = savedIntent(key, intent.study)
    if (current === null) return
    if (!sameIntent(current, intent)) throw new Error('原操作凭据已变化，不能清除。')
    const entries = journalSlots(key)
    try {
      removeMatchedSlots(key, intent, entries)
    } catch {
      throw new Error(failureText)
    } finally { changed() }
  })
}
function removeMatchedSlots(key: string, intent: EffectTrackingIntent, entries: JournalSlot[]) {
  assertSlots(key, entries)
  const matching = entries.filter(entry => {
    try { return sameIntent(decodeIntent(entry.raw), intent) } catch { return false }
  })
  if (!matching.length) throw new Error('这笔原操作凭据已变化，不能整理。')
  let remaining = entries
  // Every removal uses the exact captured slot and string. Other intents and
  // damaged records remain; no shared cleanup slot is created or overwritten.
  for (const entry of matching) {
    assertSlots(key, remaining)
    entry.storage.removeItem(entry.name)
    remaining = remaining.filter(value => value.slot !== entry.slot)
    assertSlots(key, remaining)
  }
}
function acknowledge(key: string, intent: EffectTrackingIntent) {
  return clearIntent(key, intent, '服务器回执已核对，浏览器凭据清理尚未确认。请再次核对同一请求。')
}
function discardUnregisteredIntent(key: string, intent: EffectTrackingIntent) {
  return clearIntent(key, intent, '原请求暂无登记回执，浏览器凭据清理尚未确认。请保留记录并重新核对。')
}

function rejectionText(code: EffectTrackingRejectionCode) {
  return code === 'effect_tracking_plan_already_registered'
    ? '这个主题已有相同研究方法的跟踪计划。请查看主题已有的活动计划；先核对这笔原请求，确认未登记后可放弃本次操作。'
    : '这版报告所需的选样核验程序尚不可用，暂时不能登记。原报告和到期观察仍可查阅；先核对原请求，确认未登记后可放弃本次操作。'
}

function reasonText(code: string) {
  if (code.includes('cost_policy') || code.includes('cost_unavailable')) return '费用依据尚未配置，暂不能形成可信的净效果评估。'
  if (code.includes('minimum') || code.includes('sample_count') || code.includes('insufficient_samples') || code === 'calibration_samples_insufficient') return '独立有效样本还不足，继续积累后复核。'
  if (code.includes('interval') || code.includes('window_open')) return '观察窗口尚未结束。'
  if (code.includes('label') || code.includes('matur')) return '部分判断还没有到期结果。'
  if (code.includes('source') || code.includes('history') || code.includes('original')) return '原研究或结果来源尚未完成核对。'
  if (code.includes('regime')) return '市场环境依据尚未完成核对。'
  return '仍有评估条件待核对。'
}
const statusText = { pending: '已登记，等待处理', running: '正在复核', waiting: '等待评估条件', completed: '本次复核已保存', retry: '处理异常，等待重试' }
const requestStateText = { accepted: '已受理，等待核验', running: '正在核验原报告', retrying: '核验暂未完成，后台将重试', completed: '已登记', failed: '登记核验失败' }
function requestFailureText(code: string | null) {
  if (code === 'effect_tracking_plan_already_registered') return '这个主题已有相同研究方法的活动计划，请查阅下方已有计划。原请求和原报告仍保留。'
  if (code === 'context_selection_implementation_unavailable') return '这版报告所需的选样核验程序尚不可用，暂未登记计划。原报告和到期观察仍可查阅。'
  return '后台未能完成这笔登记核验，原请求和原报告仍保留。请核对失败原因；不会自动改用新的请求。'
}

export function StudyEffectTracking({ study, revision, run, artifact }: { study: string; revision: string; run: string; artifact: string }) {
  const key = journalKey(study), currentScope = JSON.stringify([key, revision, run, artifact]), client = useQueryClient(), scope = useRef(currentScope)
  scope.current = currentScope
  const [journal, setJournal] = useState(() => journalState(key, study))
  const [error, setError] = useState<string | null>(null)
  const [absent, setAbsent] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [rejection, setRejection] = useState<{ intent: EffectTrackingIntent; code: EffectTrackingRejectionCode } | null>(null)
  const [discarded, setDiscarded] = useState(false)
  const [closedFailure, setClosedFailure] = useState(false)
  const [recoveryNotice, setRecoveryNotice] = useState<string | null>(null)
  useEffect(() => {
    const update = () => setJournal(journalState(key, study))
    const storageChanged = (event: StorageEvent) => { if (event.key === key || event.key === `${key}:cleanup` || event.key === null) update() }
    update(); listeners.add(update); setError(null); setAbsent(null); setConfirmed(false); setRejection(null); setDiscarded(false); setClosedFailure(false); setRecoveryNotice(null)
    window.addEventListener('storage', storageChanged)
    void migrateLegacyIntent(key, study).catch(failure => {
      if (scope.current === currentScope) setError(failure instanceof Error ? failure.message : '原操作凭据尚未保存到浏览器，关闭标签页前请核对原请求。')
    })
    return () => { listeners.delete(update); window.removeEventListener('storage', storageChanged) }
  }, [key, study, revision, run, artifact])
  const progress = useQuery({ queryKey: ['study-effect-tracking', productOwner, study], queryFn: () => effectTracking.status(study), retry: false, refetchInterval: 30_000 })
  const groups = progress.data?.groups.filter(group => group.receipt.revision_id === revision) ?? []
  const otherActiveGroups = progress.data?.groups.filter(group => group.receipt.revision_id !== revision && Date.parse(group.receipt.end_at) > Date.now()) ?? []
  const active = groups.some(group => Date.parse(group.receipt.end_at) > Date.now())
  const rejectedIntent = journal.pending && rejection && sameIntent(journal.pending, rejection.intent) ? rejection : null
  const pendingIntent = journal.pending?.study === study && journal.pending.owner === productOwner ? journal.pending : null
  const requestQuery = useQuery({
    queryKey: pendingIntent ? requestKey(pendingIntent) : ['study-effect-request', productOwner, study, null],
    queryFn: () => {
      if (!pendingIntent) throw new Error('没有可读取的原请求。')
      return effectTracking.read(pendingIntent)
    },
    enabled: Boolean(pendingIntent) && !journal.busy && !journal.error,
    retry: false,
    refetchOnMount: 'always',
    refetchInterval: query => {
      const value = query.state.data
      return !query.state.error && value?.kind === 'request'
        && ['accepted', 'running', 'retrying'].includes(value.request.state) ? 5_000 : false
    },
  })
  const requestStatus = requestQuery.data?.kind === 'request' ? requestQuery.data.request : null
  const recoveryReads = useQueries({ queries: journal.recovery ? journal.groups.map(group => ({
    queryKey: [...requestKey(group.intent), 'journal-recovery'],
    queryFn: () => effectTracking.read(group.intent),
    enabled: !journal.busy,
    retry: false,
    refetchOnMount: 'always' as const,
    refetchInterval: (query: { state: { data?: EffectTrackingRequestRead; error?: unknown } }): number | false => {
      const value = query.state.data
      return !query.state.error && value?.kind === 'request'
        && ['accepted', 'running', 'retrying'].includes(value.request.state) ? 5_000 : false
    },
  })) : [] })

  async function reconcileOriginal(intent: EffectTrackingIntent, action: 'ack' | 'failed' | 'absent') {
    const current = journalState(key, study)
    if (scope.current !== currentScope || current.busy || !current.recovery
      || !current.groups.some(group => sameIntent(group.intent, intent))) return
    const entries = current.entries
    operations.add(key); changed(); setError(null); setRecoveryNotice(null)
    try {
      await requestOperation(key, async () => {
        assertSlots(key, entries)
        const value = await effectTracking.read(intent)
        assertSlots(key, entries)
        if (action === 'ack') {
          const receipt = value.kind === 'receipt' ? value.receipt : value.kind === 'request' ? value.request.final_receipt : null
          if (receipt === null) throw new Error('这笔原登记尚无完整回执，不能整理。')
          checkedEffectReceipt(receipt, intent)
        } else if (action === 'failed') {
          if (value.kind !== 'request' || value.request.state !== 'failed'
            || value.request.final_receipt !== null || value.request.can_retry) {
            throw new Error('这笔原请求未确认为不可重试的失败，不能关闭。')
          }
        } else if (value.kind !== 'absent') throw new Error('服务器已存在这笔原请求或回执，不能放弃。')
        if (!navigator.locks) throw new Error('浏览器暂不能安全整理原凭据。')
        await navigator.locks.request(`effect-tracking-journal:${key}`, () => {
          removeMatchedSlots(key, intent, entries)
          changed()
        })
        if (scope.current === currentScope) setRecoveryNotice(action === 'ack'
          ? '这笔登记回执已重新核对，本地对应凭据已整理；其余原记录仍保留。'
          : action === 'failed' ? '这笔本地失败凭据已关闭，服务器失败结果及其余原记录仍保留。'
            : '已放弃这笔未登记操作，其余原记录仍保留；没有提交新的请求。')
      })
      void client.invalidateQueries({ queryKey: ['study-effect-tracking', productOwner, intent.study] })
    } catch (failure) {
      if (scope.current === currentScope) setError(failure instanceof Error ? failure.message : '原凭据尚未核对，不能整理。')
    } finally { operations.delete(key); changed() }
  }

  async function resolveRead(intent: EffectTrackingIntent, value: EffectTrackingRequestRead) {
    assertIntent(key, intent)
    if (value.kind === 'absent') {
      if (scope.current === currentScope) { setAbsent(intent.id); setError(null) }
      return
    }
    if (scope.current === currentScope) { setAbsent(null); setRejection(null); setError(null) }
    const receipt = value.kind === 'receipt' ? value.receipt : value.request.final_receipt
    if (receipt === null) return
    checkedEffectReceipt(receipt, intent)
    await acknowledge(key, intent)
    if (scope.current === currentScope) setConfirmed(true)
    void client.invalidateQueries({ queryKey: ['study-effect-tracking', productOwner, intent.study] })
  }

  useEffect(() => {
    const intent = pendingIntent, value = requestQuery.data
    if (!intent || !value || !requestQuery.isSuccess || requestQuery.isError || requestQuery.isFetching || operations.has(key)) return
    try {
      const current = savedIntent(key, study)
      if (current && sameIntent(current, intent)) void resolveRead(intent, value).catch(failure => {
        if (scope.current === currentScope) setError(failure instanceof Error ? failure.message : '原请求状态尚未确认。')
      })
    } catch (failure) {
      if (scope.current === currentScope) setError(failure instanceof Error ? failure.message : '原请求状态尚未确认。')
    }
  }, [key, currentScope, pendingIntent?.id, requestQuery.dataUpdatedAt, requestQuery.isSuccess, requestQuery.isError, requestQuery.isFetching])

  async function execute(intent: EffectTrackingIntent, action: 'new' | 'check' | 'retry') {
    if (scope.current !== currentScope || operations.has(key)) return
    operations.add(key); changed(); setError(null); setAbsent(null); setConfirmed(false); setDiscarded(false); setClosedFailure(false)
    if (action !== 'check') setRejection(null)
    try {
      const perform = async () => {
        if (action !== 'check') await saveIntent(key, intent)
        else assertIntent(key, intent)
        const value: EffectTrackingRequestRead = action === 'check' ? await effectTracking.read(intent)
          : { kind: 'request', request: await effectTracking.start(intent) }
        assertIntent(key, intent)
        client.setQueryData(requestKey(intent), value)
        await resolveRead(intent, value)
      }
      if (action === 'check') await perform()
      else await requestOperation(key, perform)
    } catch (failure) {
      if (scope.current === currentScope && failure instanceof EffectTrackingAdmissionRejection && sameIntent(failure.intent, intent)) {
        try { assertIntent(key, intent); setRejection({ intent, code: failure.code }) } catch { /* Keep an unverified journal blocked. */ }
      }
      if (scope.current === currentScope) setError(failure instanceof Error ? failure.message : '操作尚未确认，请核对原请求。')
    } finally { operations.delete(key); changed() }
  }
  function start() {
    const current = journalState(key, study); setJournal(current)
    if (current.busy || current.pending || current.error || progress.isPending || progress.isError || active) return
    try { void execute({ owner: productOwner, study, revision, run, artifact, id: `effect-tracking:${crypto.randomUUID()}` }, 'new') }
    catch { setError('无法生成操作凭据，尚未提交。') }
  }
  function original(action: 'check' | 'retry') {
    const current = journalState(key, study); setJournal(current)
    if (!current.busy && !current.error && current.pending && (action === 'check' || (absent === current.pending.id
      && !requestQuery.isError && !requestQuery.isFetching && requestQuery.data?.kind === 'absent'))) void execute(current.pending, action)
  }

  async function discard() {
    const current = journalState(key, study)
    if (scope.current !== currentScope || current.busy || current.error || !current.pending
      || absent !== current.pending.id || requestQuery.isError || requestQuery.isFetching || requestQuery.data?.kind !== 'absent') return
    const intent = current.pending
    operations.add(key); changed(); setError(null); setAbsent(null); setConfirmed(false); setDiscarded(false); setClosedFailure(false)
    try {
      await requestOperation(key, async () => {
        assertIntent(key, intent)
        const value = await effectTracking.read(intent)
        assertIntent(key, intent)
        client.setQueryData(requestKey(intent), value)
        if (value.kind === 'absent') {
          await discardUnregisteredIntent(key, intent)
          if (scope.current === currentScope) { setDiscarded(true); setRejection(null) }
        } else {
          await resolveRead(intent, value)
          if (scope.current === currentScope && value.kind === 'request' && value.request.state !== 'completed') {
            setError('服务器已有这笔受理任务，不能把它当作未登记操作放弃。')
          }
        }
      })
      await client.invalidateQueries({ queryKey: ['study-effect-tracking', productOwner, intent.study] })
    } catch (failure) {
      if (scope.current === currentScope) setError(failure instanceof Error ? failure.message : '原请求状态尚未确认，不能放弃操作。')
    } finally { operations.delete(key); changed() }
  }

  async function closeFailedRecord() {
    const current = journalState(key, study)
    if (scope.current !== currentScope || current.busy || current.error || !current.pending
      || requestQuery.isError || requestQuery.isFetching || requestStatus?.state !== 'failed'
      || requestStatus.final_receipt !== null || requestStatus.can_retry) return
    const intent = current.pending
    operations.add(key); changed(); setError(null); setAbsent(null); setConfirmed(false); setDiscarded(false); setClosedFailure(false)
    try {
      await requestOperation(key, async () => {
        assertIntent(key, intent)
        const status = await effectTracking.requestStatus(intent)
        assertIntent(key, intent)
        if (status === null || status.state !== 'failed' || status.final_receipt !== null || status.can_retry) {
          throw new Error('原请求尚未确认为不可重试的失败记录，不能关闭本地凭据。请重新读取原请求。')
        }
        client.setQueryData(requestKey(intent), { kind: 'request', request: status } satisfies EffectTrackingRequestRead)
        await clearIntent(key, intent, '失败结果已核对，本地凭据清理尚未确认。请再次核对同一请求。')
        if (scope.current === currentScope) { setClosedFailure(true); setRejection(null) }
      })
    } catch (failure) {
      if (scope.current === currentScope) setError(failure instanceof Error ? failure.message : '原失败记录尚未确认，不能关闭本地凭据。')
    } finally { operations.delete(key); changed() }
  }

  function renderGroup(group: NonNullable<typeof progress.data>['groups'][number], otherVersion = false) {
    return <div key={group.receipt.request_id} className="product-notice">
      <p>{otherVersion ? '其他版本已登记' : '本版已登记'}：{assetTime(group.receipt.recorded_at)} · 观察至 {assetTime(group.receipt.end_at)}</p>
      {otherVersion && <p><a className="product-link" href={`#/report?run=${encodeURIComponent(group.receipt.source_run_id)}&back=${encodeURIComponent(window.location.hash)}`}>阅读原报告</a> · <a className="product-link" href={`#/studies?study=${encodeURIComponent(study)}&effect_revision=${encodeURIComponent(group.receipt.revision_id)}&back=${encodeURIComponent(window.location.hash)}`}>查看原版本跟踪</a></p>}
      <ul>{group.windows.map(window => <li key={window.plan.job_id}>
        <strong>{horizonName(window.plan.horizon)}：{statusText[window.status]}</strong>
        <p><a className="product-link" href={`#/results?method=ai_synthesis&market=${encodeURIComponent(window.plan.market)}&instrument=${encodeURIComponent(window.plan.instrument)}&horizon=${encodeURIComponent(window.plan.horizon)}&stable_recipe_hash=${encodeURIComponent(window.plan.stable_recipe_hash)}&back=${encodeURIComponent(globalThis.window.location.hash)}`}>查看同一方法的观察记录</a></p>
        <p className="product-muted">费用依据不足会阻止完整校准，不会抹去已保存的到期行情与方向核对；可通过观察记录继续查阅。</p>
        {window.captured_count === null ? <p>尚未形成采集记录</p> : <>
          <p>已核对原判断 {window.captured_count} 条；完整校准样本 {window.effective_count ?? '待核对'} 条。</p>
          <p>未纳入完整校准 {window.excluded_count ?? '待核对'} 条；这不等于没有到期结果。</p>
        </>}
        {[...new Set(window.reasons.map(reasonText))].map(reason => <p className="product-muted" key={reason}>{reason}</p>)}
        {window.error_code && <p role="alert">本次处理异常，系统会保留原计划并重试。</p>}
        <details><summary>核对记录</summary><p>下次复核：{assetTime(window.next_attempt_at)}</p><pre className="asset-json">{JSON.stringify(window, null, 2)}</pre></details>
      </li>)}</ul>
      {group.receipt.missing_windows.length > 0 && <p>登记报告未保存 {group.receipt.missing_windows.map(window => horizonName(window.horizon)).join('、')} 的 AI 判断，无法登记对应周期。</p>}
    </div>
  }
  return <section className="study-effect-tracking" aria-label="研究效果跟踪">
    <h4>跟踪这版研究效果</h4>
    <p><a className="product-link" href={`#/results?run=${encodeURIComponent(run)}&study_id=${encodeURIComponent(study)}&method=ai_synthesis&back=${encodeURIComponent(window.location.hash)}`}>查看这版判断的到期结果</a></p>
    <p className="product-muted">到期行情和方向核对可先查阅，不必等到完整校准样本达到 100 条；费用依据尚缺时不生成净效果或可信校准结论。</p>
    <p className="product-muted">从登记时起持续 90 天收集同一方法的后续判断和到期结果。每个研究主题按首份原始判断计样本，重复复查不会增加独立样本数。</p>
    {progress.isPending ? <p role="status">正在读取这版报告的跟踪记录…</p> : progress.isError ? <p role="alert">跟踪进度读取失败。<button className="product-link" onClick={() => void progress.refetch()}>重新读取</button></p> : <>
      {groups.map(group => renderGroup(group))}
      {groups.length === 0 && <p>这版报告尚未登记效果跟踪。</p>}
      {otherActiveGroups.length > 0 && <section aria-label="主题已有的活动计划"><h5>主题已有的其他活动计划</h5><p className="product-muted">以下来自本主题的其他报告版本，保留原计划继续观察；不据此认定与本版方法相同。新登记是否重复仍由服务器核对。</p>{otherActiveGroups.map(group => renderGroup(group, true))}</section>}
      {progress.data?.more_available && <p className="product-muted">当前展示最近 12 笔跟踪记录；更早记录未在本页展开。</p>}
    </>}
    <button className="button secondary" disabled={journal.busy || Boolean(journal.pending || journal.error) || progress.isPending || progress.isError || active} onClick={start}>{journal.busy ? '正在保存原请求…' : active ? '这版已登记跟踪' : '申请跟踪研究效果'}</button>
    <p className="product-muted">原请求先保存到本浏览器，再提交受理，后台核验原报告后才登记计划。离开此页或关闭浏览器后，回来会读取同一请求；不会重复启动研究。</p>
    <p className="product-muted">达到 100 个独立有效样本并补齐费用、结果和市场环境依据后，才能评估概率是否可信。登记跟踪本身不会启动新研究，也不会改变主题复查开关。</p>
    {journal.recovery && <section className="product-notice" aria-label="核对冲突凭据">
      <h5>逐笔核对原操作</h5><p>本浏览器保留了不同或不完整的原凭据。下面只读取已有请求，不会重新提交；尚未核对的记录继续保留。</p>
      {journal.groups.map((group, index) => {
        const read = recoveryReads[index], value = read?.data
        const final = value?.kind === 'receipt' || (value?.kind === 'request' && value.request.state === 'completed')
        const failed = value?.kind === 'request' && value.request.state === 'failed' && value.request.final_receipt === null && !value.request.can_retry
        const absent = value?.kind === 'absent'
        return <section key={intentIdentity(group.intent)} aria-label={`原操作 ${index + 1}`}>
          <p><strong>原操作 {index + 1}：{read?.isError ? '原状态尚未确认' : read?.isPending ? '正在读取原请求…' : value?.kind === 'receipt' ? '原登记回执已确认' : value?.kind === 'request' ? requestStateText[value.request.state] : absent ? '两处均未找到原请求或回执' : '等待核对'}</strong></p>
          <p><a className="product-link" href={`#/studies?study=${encodeURIComponent(group.intent.study)}&effect_revision=${encodeURIComponent(group.intent.revision)}&back=${encodeURIComponent(window.location.hash)}`}>查看这笔操作对应的报告版本</a></p>
          {read?.isError && <p>读取失败或原身份不匹配。这份本地凭据仍保留，不能认定为未登记。</p>}
          {value?.kind === 'request' && value.request.state === 'failed' && <p>{requestFailureText(value.request.error_code)}</p>}
          <button className="button secondary" disabled={journal.busy || read?.isFetching} onClick={() => void read?.refetch()}>重新核对这笔原请求</button>
          {final && <button className="button secondary" disabled={journal.busy || read?.isError || read?.isFetching} onClick={() => void reconcileOriginal(group.intent, 'ack')}>整理已确认凭据</button>}
          {failed && <button className="button secondary" disabled={journal.busy || read?.isError || read?.isFetching} onClick={() => void reconcileOriginal(group.intent, 'failed')}>关闭这笔失败记录</button>}
          {absent && <button className="button secondary" disabled={journal.busy || read?.isError || read?.isFetching} onClick={() => void reconcileOriginal(group.intent, 'absent')}>放弃这笔未登记操作</button>}
        </section>
      })}
      {journal.damaged.length > 0 && <p role="alert">有 {journal.damaged.length} 份本地凭据无法解析或不属于当前身份与主题。原文仍保留，无法据此读取请求或解除新登记限制。</p>}
    </section>}
    {recoveryNotice && <p role="status">{recoveryNotice}</p>}
    {pendingIntent && <div className="product-notice" role="status">
      <p><strong>{requestQuery.isError && requestStatus ? '上次读取：' : ''}{requestStatus ? requestStateText[requestStatus.state] : '原请求尚未确认'}</strong></p>
      {requestStatus?.state === 'failed' && <p>{requestFailureText(requestStatus.error_code)}</p>}
      {requestStatus?.state === 'failed' && requestStatus.final_receipt === null && !requestStatus.can_retry && <><p>关闭这笔本地失败记录后，可以发起新的登记；服务器上的原失败结果和审计仍保留，不会重试原请求。</p><button className="button secondary" disabled={journal.busy || requestQuery.isError || requestQuery.isFetching} onClick={() => void closeFailedRecord()}>关闭失败记录</button></>}
      {requestStatus?.state === 'retrying' && requestStatus.next_attempt_at && <p>后台下次核验：{assetTime(requestStatus.next_attempt_at)}。</p>}
      {(!requestStatus || ['accepted', 'running', 'retrying'].includes(requestStatus.state)) && <p>登记尚未完成，保留原请求等待核验；其他报告和已保存的到期观察仍可查阅。</p>}
      {pendingIntent.revision !== revision && <p><a className="product-link" href={`#/studies?study=${encodeURIComponent(pendingIntent.study)}&effect_revision=${encodeURIComponent(pendingIntent.revision)}&back=${encodeURIComponent(window.location.hash)}`}>查看原请求对应的报告版本</a></p>}
      {requestQuery.isError && <p role="alert">原请求状态读取失败，原操作凭据仍保留；未确认登记，也不会自动重新提交。</p>}
      {requestStatus?.error_code && <details><summary>核验记录</summary><p>{requestStatus.error_code}</p><p>已尝试 {requestStatus.attempts} 次</p></details>}
      <button className="button secondary" disabled={journal.busy || requestQuery.isFetching} onClick={() => original('check')}>刷新原请求状态</button>
      {absent === pendingIntent.id && !requestQuery.isError && requestQuery.data?.kind === 'absent' && <><p>服务器没有这笔受理任务，也没有旧登记回执。可显式重新提交同一请求，或放弃这笔未登记操作。</p><button className="button secondary" disabled={journal.busy || requestQuery.isFetching} onClick={() => original('retry')}>重新提交原请求</button><button className="button secondary" disabled={journal.busy || requestQuery.isFetching} onClick={() => void discard()}>放弃这笔未登记操作</button></>}
      {rejectedIntent && !error && <p>{rejectionText(rejectedIntent.code)}</p>}
    </div>}
    {(journal.error || error) && <div role="alert"><p>{journal.error ?? (rejectedIntent ? rejectionText(rejectedIntent.code) : '操作尚未确认，请核对原回执。')}</p>{error && <details><summary>查看错误记录</summary><pre className="asset-json">{error}</pre></details>}<button className="product-link" disabled={journal.busy} onClick={() => { setJournal(journalState(key, study)); setAbsent(null) }}>重新读取操作凭据</button></div>}
    {confirmed && !journal.pending && <p role="status">原跟踪回执已确认，进度以重新读取的原计划为准。</p>}
    {discarded && !journal.pending && <p role="status">已放弃这笔未登记操作。可继续查阅已有计划或重新选择报告；没有确认新的跟踪计划。</p>}
    {closedFailure && !journal.pending && <p role="status">本地失败记录已关闭，服务器原失败结果仍保留。可选择报告发起新的登记；尚未提交新的请求。</p>}
  </section>
}
