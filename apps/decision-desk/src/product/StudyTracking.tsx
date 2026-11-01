import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { z } from 'zod'
import { checkedStudyTrackingReceipt, delivery, productOwner, type StudyTrackingRequest } from '../api/delivery'
import { assets } from '../api/assets'
import type { StudyTimelineView, StudyView } from '@decision-hub/contracts-ts'

const identifier = z.string().min(1).max(128).refine(value => value === value.trim())
const intentSchema = z.object({ owner: identifier, study: identifier, id: identifier, expected: z.enum(['active', 'paused']), status: z.enum(['active', 'paused']) }).strict().refine(value => value.expected !== value.status)
const activeOperations = new Set<string>()
const listeners = new Set<() => void>()
const changed = () => { for (const listener of listeners) listener() }
const trackingKey = (study: string) => `decision-hub.study-tracking-command.v1:${JSON.stringify([import.meta.env.VITE_API_BASE_URL?.trim() || 'same-origin', productOwner, study])}`
const sameIntent = (left: StudyTrackingRequest, right: StudyTrackingRequest) => left.owner === right.owner && left.study === right.study && left.id === right.id && left.expected === right.expected && left.status === right.status
function storedRaw(key: string) {
  const primary = sessionStorage.getItem(key), cleanup = sessionStorage.getItem(`${key}:cleanup`)
  if (primary !== null && cleanup !== null && primary !== cleanup) throw new Error('study_tracking_journal_ambiguous')
  return primary ?? cleanup
}
function readIntent(key: string, study: string): StudyTrackingRequest | null {
  const raw = storedRaw(key)
  if (raw === null) return null
  if (raw.length > 4096) throw new Error('study_tracking_journal_invalid')
  const value = intentSchema.parse(JSON.parse(raw))
  if (value.owner !== productOwner || value.study !== study) throw new Error('study_tracking_journal_scope_changed')
  return value
}
function trackingState(key: string, study: string) {
  try { return { pending: readIntent(key, study), busy: activeOperations.has(key), error: null as string | null } }
  catch { return { pending: null, busy: activeOperations.has(key), error: '无法核对浏览器中的自动复查凭据。请保留原记录并重新读取；尚未发送新请求。' } }
}
function assertIntent(key: string, value: StudyTrackingRequest) {
  const saved = readIntent(key, value.study)
  if (!saved || !sameIntent(saved, value)) throw new Error('原自动复查凭据已变化，尚未发送新请求。')
}
function persistIntent(key: string, value: StudyTrackingRequest) {
  try {
    const checked = intentSchema.parse(value)
    if (checked.owner !== productOwner) throw new Error('study_tracking_owner_changed')
    const existing = readIntent(key, value.study)
    if (existing && !sameIntent(existing, value)) throw new Error('study_tracking_unconfirmed_conflict')
    if (!existing) sessionStorage.setItem(key, JSON.stringify(checked))
    assertIntent(key, value)
  } catch { throw new Error('无法保存本次自动复查操作凭据，尚未发送请求。请重新读取浏览器凭据。') }
  finally { changed() }
}
function acknowledgeIntent(key: string, value: StudyTrackingRequest) {
  assertIntent(key, value)
  const original = storedRaw(key)
  if (original === null) throw new Error('原自动复查凭据无法核对，尚未清除请求。')
  try {
    // Keep a recovery copy until the primary deletion is confirmed.
    sessionStorage.setItem(`${key}:cleanup`, original)
    if (sessionStorage.getItem(`${key}:cleanup`) !== original) throw new Error('study_tracking_cleanup_unconfirmed')
    sessionStorage.removeItem(key)
    if (sessionStorage.getItem(key) !== null) throw new Error('study_tracking_cleanup_unconfirmed')
    sessionStorage.removeItem(`${key}:cleanup`)
  } catch {
    try { if (sessionStorage.getItem(key) === null && sessionStorage.getItem(`${key}:cleanup`) === null) sessionStorage.setItem(`${key}:cleanup`, original) } catch { /* Inaccessible storage remains blocked. */ }
    throw new Error('原回执已核对，但浏览器凭据清理未确认。请再次核对同一原请求。')
  }
  finally { changed() }
}

export function StudyTracking({ study }: { study: StudyView }) {
  const client = useQueryClient(), key = trackingKey(study.study_id)
  const currentScope = useRef(key)
  currentScope.current = key
  const [journal, setJournal] = useState(() => trackingState(key, study.study_id))
  const [operationError, setOperationError] = useState<string | null>(null)
  const [absentRequest, setAbsentRequest] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState<ReturnType<typeof checkedStudyTrackingReceipt> | null>(null)
  useEffect(() => {
    setOperationError(null); setAbsentRequest(null); setConfirmed(null)
    const update = () => setJournal(trackingState(key, study.study_id))
    update(); listeners.add(update)
    return () => { listeners.delete(update) }
  }, [key, study.study_id])
  const catalog = useQuery({ queryKey: ['research-market-catalog'], queryFn: assets.researchMarkets, retry: false, staleTime: 60_000 })
  const enabled = study.identity_basis !== 'legacy_unknown' && Boolean(catalog.data?.items.some(scope => scope.market === study.market && scope.instrument === study.instrument && scope.research_enabled))

  async function run(value: StudyTrackingRequest, kind: 'new' | 'check' | 'retry') {
    const sameScope = () => currentScope.current === key
    if (!sameScope() || activeOperations.has(key)) return
    activeOperations.add(key); changed(); setOperationError(null); setAbsentRequest(null); setConfirmed(null)
    try {
      if (kind === 'new') persistIntent(key, value)
      else assertIntent(key, value)
      const raw = kind === 'check' ? await delivery.trackingCommandReceipt(value) : await delivery.tracking(value.study, value.status, value.expected, value.id)
      if (raw === null) {
        // Only the client accepts the exact server 404+code as absence. No automatic POST follows.
        assertIntent(key, value); if (sameScope()) setAbsentRequest(value.id)
        return
      }
      // Check the frozen intent again at the component boundary, including mocked transports.
      const receipt = checkedStudyTrackingReceipt(raw, value)
      acknowledgeIntent(key, value); if (sameScope()) setConfirmed(receipt)
      if (kind !== 'check') {
        // A fresh POST receipt may update an older projection. A historical GET
        // never overwrites a later opposite command or treats current state as ACK.
        const update = (current: StudyTimelineView['study']): StudyTimelineView['study'] => current.study_id === receipt.study_id && current.status === value.expected && Date.parse(current.updated_at) <= Date.parse(receipt.recorded_at)
          ? { ...current, status: value.status, updated_at: receipt.recorded_at } : current
        client.setQueriesData<StudyTimelineView>({ queryKey: ['asset-study', value.study] }, current => current ? { ...current, study: update(current.study) } : current)
        client.setQueriesData<Awaited<ReturnType<typeof assets.studies>>>({ queryKey: ['asset-studies'] }, current => current ? { ...current, items: current.items.map(update) } : current)
      }
      await Promise.all([client.invalidateQueries({ queryKey: ['asset-study'] }), client.invalidateQueries({ queryKey: ['asset-studies'] })])
    } catch (failure) { if (sameScope()) setOperationError(failure instanceof Error ? failure.message : '原自动复查请求尚未确认，请核对原回执。') }
    finally { activeOperations.delete(key); changed() }
  }
  function toggle() {
    const current = trackingState(key, study.study_id)
    setJournal(current)
    if (current.busy || current.pending || current.error || (!enabled && study.status !== 'active') || !['active', 'paused'].includes(study.status)) return
    const expected = study.status as 'active' | 'paused'
    try { void run({ owner: productOwner, study: study.study_id, id: `study-tracking:${crypto.randomUUID()}`, expected, status: expected === 'active' ? 'paused' : 'active' }, 'new') }
    catch { setOperationError('无法生成自动复查请求凭据，尚未发送操作。') }
  }
  function checkOriginal() {
    const current = trackingState(key, study.study_id); setJournal(current)
    if (!current.busy && !current.error && current.pending) void run(current.pending, 'check')
  }
  function retryOriginal() {
    const current = trackingState(key, study.study_id); setJournal(current)
    if (!current.busy && !current.error && current.pending && absentRequest === current.pending.id) void run(current.pending, 'retry')
  }
  const blocked = journal.busy || Boolean(journal.pending || journal.error)
  return <div className="study-tracking">
    <button className="button secondary" disabled={blocked || (!enabled && study.status !== 'active')} onClick={toggle}>{journal.busy ? '正在保存并核对…' : study.status === 'active' ? '暂停自动复查' : '开启后续自动复查'}</button>
    <p className="product-muted">暂停会停止自动排队任务与后续自动研究，手动任务及正在执行的任务会保留；恢复不会重放已跳过的旧计划，也不会立即发起新研究。</p>
    {catalog.isPending ? <p className="product-muted">正在核对研究范围，确认后可开启自动复查。</p> : catalog.isError ? <p role="alert">研究范围读取失败，暂时无法确认能否开启自动复查。<button className="product-link" disabled={catalog.isFetching} onClick={() => void catalog.refetch()}>重新核对范围</button></p> : !enabled && <p className="product-muted">原始范围未明确或市场未开通，请先完成范围准入。</p>}
    {journal.pending && <div className="product-notice" role="status">
      <p>原{journal.pending.status === 'paused' ? '暂停' : '开启'}自动复查操作尚未确认。刷新后的开关状态不能代替原请求回执；当前不会提交相反操作。</p>
      <button className="button secondary" disabled={journal.busy} onClick={checkOriginal}>核对原自动复查回执</button>
      {absentRequest === journal.pending.id && <><p>服务器尚无这笔原操作回执。可明确重试同一原请求，不会生成新请求或提交相反操作。</p><button className="button secondary" disabled={journal.busy} onClick={retryOriginal}>重试原{journal.pending.status === 'paused' ? '暂停' : '开启'}请求</button></>}
      <details><summary>查看原操作凭据</summary><pre className="asset-json">{JSON.stringify(journal.pending, null, 2)}</pre></details>
    </div>}
    {(journal.error || operationError) && <div role="alert"><p>{journal.error ?? '操作未确认，请核对原请求；刷新当前状态不能代替原回执。'}</p>{operationError && <details><summary>查看原始错误</summary><pre className="asset-json">{operationError}</pre></details>}<button className="product-link" disabled={journal.busy} onClick={() => { setJournal(trackingState(key, study.study_id)); setAbsentRequest(null) }}>重新读取操作凭据</button></div>}
    {confirmed && !journal.pending && <p role="status">原{confirmed.status === 'paused' ? '暂停' : '开启'}回执已核对。{confirmed.status === 'paused' ? `当时停止 ${confirmed.cancelled_runs} 个自动排队任务、跳过 ${confirmed.retired_plans} 个复查计划。` : ''}当前开关以主题重新读取的状态为准。</p>}
  </div>
}
