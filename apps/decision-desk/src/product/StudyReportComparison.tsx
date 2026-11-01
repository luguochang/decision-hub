import { useQuery } from '@tanstack/react-query'
import type { ResearchRunDetailView, StudyReportComparisonView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { api } from '../api/client'
import { AssetError, assetTime, directionName, horizonName } from './assetFormat'
import { researchDisplayText } from './researchLanguage'
import { sourceLabel as savedSourceLabel } from './operationLabels'
import { useDetailFocus } from './useDetailFocus'

type ReportRevisionView = StudyReportComparisonView['current']
type Evidence = ResearchRunDetailView['evidence'][number]
type CoreReferences = { groups: Map<string, Evidence[]>; missing: string[]; hasReferences: boolean }
const horizons = ['30m', '24h', '72h'] as const

function checkedPair(value: StudyReportComparisonView | undefined, studyId: string, revisionId: string) {
  if (!value || value.study_id !== studyId || value.current.study_id !== studyId
    || value.current.revision_id !== revisionId || value.current.strategy_version !== 'research.v1') return null
  const previous = value.previous
  if (value.current.previous_revision_id === null) return previous === null ? value : null
  return previous && previous.revision_id === value.current.previous_revision_id
    && previous.study_id === studyId && previous.strategy_version === value.current.strategy_version
    && previous.source_run_id !== value.current.source_run_id && previous.artifact_id !== value.current.artifact_id ? value : null
}
function checkedDetail(value: ResearchRunDetailView | undefined, revision: ReportRevisionView | undefined | null) {
  return value && revision && value.run.run_id === revision.source_run_id
    && value.run.artifact_id === revision.artifact_id ? value : null
}
function coreReferences(detail: ResearchRunDetailView): CoreReferences {
  const causal = detail.causal_case
  const refs = [...new Set([
    ...(causal?.evidence_refs ?? []), ...(causal?.main_chain.flatMap(link => link.evidence_refs) ?? []),
    ...(causal?.opposite_chain.flatMap(link => link.evidence_refs) ?? []), ...detail.horizons.flatMap(value => value.evidence_refs),
  ])]
  const byId = new Map(detail.evidence.map(value => [value.evidence_id, value]))
  const groups = new Map<string, Evidence[]>(), missing: string[] = []
  for (const ref of refs) {
    const value = byId.get(ref)
    if (!value) { missing.push(ref); continue }
    const group = groups.get(value.content_hash) ?? []
    group.push(value); groups.set(value.content_hash, group)
  }
  return { groups, missing, hasReferences: refs.length > 0 }
}
function href(scene: 'report' | 'evidence', run: string, back: string, evidence?: string) {
  const params = new URLSearchParams({ run, back })
  if (evidence) params.set('evidence', evidence)
  return `#/${scene}?${params}`
}
function sourceLabel(value: Evidence) {
  let fallback = '已保存来源'
  try { if (value.source_url) fallback = new URL(value.source_url).hostname } catch { /* Keep the existing saved-source fallback. */ }
  return savedSourceLabel(value.source_id, fallback)
}
function EvidenceLinks({ values, runId, back, label }: { values: Evidence[]; runId: string; back: string; label: string }) {
  return <>{values.map((value, index) => <a key={value.evidence_id} className="product-link" href={href('evidence', runId, back, value.evidence_id)}>{label}{values.length > 1 ? ` ${index + 1}` : ''}</a>)}</>
}
function EvidenceGroup({ title, hashes, current, previous, currentRun, previousRun, back }: { title: string; hashes: string[]; current: CoreReferences; previous?: CoreReferences; currentRun: string; previousRun?: string; back: string }) {
  return <section className="study-comparison-material-group"><h4>{title} · {hashes.length} 份</h4>{hashes.length === 0 ? <p className="product-muted">没有已定位材料属于这一组。</p> : <ul>{hashes.map(hash => {
    const newValues = current.groups.get(hash) ?? [], oldValues = previous?.groups.get(hash) ?? [], value = newValues[0] ?? oldValues[0]
    return <li key={hash}><strong>{sourceLabel(value)}</strong><small>原材料收到于 {assetTime(value.received_at)}</small><div className="asset-ref-list">{oldValues.length > 0 && previousRun && <EvidenceLinks values={oldValues} runId={previousRun} back={back} label="上一版原文" />}{newValues.length > 0 && <EvidenceLinks values={newValues} runId={currentRun} back={back} label="本版原文" />}</div><details><summary>查看保存摘录</summary><p>{value.excerpt}</p></details></li>
  })}</ul>}</section>
}
function VersionSummary({ title, revision, detail, back }: { title: string; revision: ReportRevisionView; detail: ResearchRunDetailView; back: string }) {
  const aiDirections = horizons.map(horizon => {
    const saved = detail.horizons.find(value => value.horizon === horizon)
    return `${horizonName(horizon)} ${saved ? directionName(saved.action) : '未保存观点'}`
  }).join(' · ')
  return <article><h4>{title} · {assetTime(revision.created_at)}</h4><p><b>当时的 AI 方向：</b>{aiDirections}</p><details><summary>日线参考与原报告汇总</summary><p>{researchDisplayText(detail.action_summary.headline)}</p><details><summary>原始措辞</summary><p>{detail.action_summary.headline}</p></details></details><p className="product-muted">已保存报告；保留当时内容，授权与质量限制请查看原报告。</p><a className="product-link" href={href('report', revision.source_run_id, back)}>阅读{title}完整报告</a>{detail.causal_case && <details><summary>查看当时保存的完整解释</summary><p>{researchDisplayText(detail.causal_case.thesis)}</p><details><summary>原始措辞</summary><p>{detail.causal_case.thesis}</p></details></details>}</article>
}
function Opinion({ detail, horizon }: { detail: ResearchRunDetailView | null; horizon: typeof horizons[number] }) {
  const value = detail?.horizons.find(item => item.horizon === horizon)
  return value ? <><strong>{directionName(value.action)}</strong><details><summary>确认与失效条件</summary><p><b>确认：</b>{researchDisplayText(value.trigger)}</p><p><b>失效：</b>{researchDisplayText(value.invalidation)}</p><details><summary>原始措辞</summary><p>{value.trigger}</p><p>{value.invalidation}</p></details></details></> : <p className="product-muted">{detail ? '该版未保存这个周期的 AI 判断。' : '没有原前版。'}</p>
}
function ComparisonContent({ pair, current, previous, back }: { pair: StudyReportComparisonView; current: ResearchRunDetailView; previous: ResearchRunDetailView | null; back: string }) {
  const after = coreReferences(current), before = previous ? coreReferences(previous) : undefined
  const complete = Boolean(before && before.hasReferences && after.hasReferences && before.missing.length === 0 && after.missing.length === 0)
  const afterHashes = [...after.groups.keys()], beforeHashes = [...(before?.groups.keys() ?? [])]
  const newHashes = afterHashes.filter(hash => !before?.groups.has(hash)), reusedHashes = afterHashes.filter(hash => before?.groups.has(hash)), removedHashes = beforeHashes.filter(hash => !after.groups.has(hash))
  return <>
    {!previous && <p role="status">这是这个主题的首份已登记报告，尚无原前版可对照。</p>}
    <div className="study-comparison-summaries">{pair.previous && previous && <VersionSummary title="上一版" revision={pair.previous} detail={previous} back={back} />}<VersionSummary title="所选版" revision={pair.current} detail={current} back={back} /></div>
    <p className="product-muted">这里只比较历史保存的方向值；方法标识相同不表示配方已核验，也不代表预测效果已验证。</p>
    <div className="table-wrap"><table className="study-comparison-windows"><caption>三个周期的原判断、确认与失效条件</caption><thead><tr><th>周期</th><th>上一版</th><th>所选版</th></tr></thead><tbody>{horizons.map(horizon => {
      const changes = (pair.current.direction_changes ?? []).filter(value => value.method_family === 'ai_synthesis' && value.horizon === horizon)
      const change = changes.length === 1 ? changes[0] : undefined
      const oldOpinion = previous?.horizons.find(value => value.horizon === horizon), newOpinion = current.horizons.find(value => value.horizon === horizon)
      const matched = change?.comparison_status === 'compared' && oldOpinion && newOpinion && change.previous_action === oldOpinion.action && change.current_action === newOpinion.action
      const label = !previous ? '首份已登记报告' : matched ? change?.changed === true ? '保存方向改变' : '保存方向相同' : change?.comparison_status === 'method_changed' ? '方法不同，不直接比较方向' : '缺少可比观点，只展示原值'
      return <tr key={horizon}><th scope="row">{horizonName(horizon)}<small>{label}</small></th><td><Opinion detail={previous} horizon={horizon} /></td><td><Opinion detail={current} horizon={horizon} /></td></tr>
    })}</tbody></table></div>
    <section aria-label="核心引用材料变化"><h3>核心引用材料</h3><p className="product-muted">来自两份报告保存的论据和三个周期引用，按内容指纹去重；不同任务中的原文入口各自保留。材料引用变化不表示内容失效、独立来源数或预测效果。</p>
      {!after.hasReferences && <p role="status">本版没有保存综合论据或周期引用，不能用全部已采纳材料代替核心引用。</p>}
      {(after.missing.length > 0 || (before?.missing.length ?? 0) > 0) && <p role="status">部分原引用尚未定位，本次不提供完整新增或不再引用结论；可继续核对已定位材料与两份原报告。</p>}
      {before && !before.hasReferences && <p role="status">上一版没有保存可对照的核心引用，暂不能判断完整材料变化。</p>}
      <div className="study-comparison-materials">{complete ? <><EvidenceGroup title="本版新增引用" hashes={newHashes} current={after} previous={before} currentRun={current.run.run_id} previousRun={previous!.run.run_id} back={back} /><EvidenceGroup title="两版沿用" hashes={reusedHashes} current={after} previous={before} currentRun={current.run.run_id} previousRun={previous!.run.run_id} back={back} /><EvidenceGroup title="本版未再引用" hashes={removedHashes} current={after} previous={before} currentRun={current.run.run_id} previousRun={previous!.run.run_id} back={back} /></> : <><EvidenceGroup title="本版已定位引用" hashes={afterHashes} current={after} currentRun={current.run.run_id} back={back} />{before && previous && <EvidenceGroup title="上一版已定位引用" hashes={beforeHashes} current={{ groups: new Map(), missing: [], hasReferences: false }} previous={before} currentRun={current.run.run_id} previousRun={previous.run.run_id} back={back} />}</>}</div>
    </section>
  </>
}

export function StudyReportComparison({ studyId, revisionId, back, onClose }: { studyId: string; revisionId: string; back: string; onClose: () => void }) {
  const pair = useQuery({ queryKey: ['study-report-comparison', studyId, revisionId], queryFn: () => assets.studyReportComparison(studyId, revisionId), retry: false })
  const identity = pair.isError ? null : checkedPair(pair.data, studyId, revisionId)
  const current = useQuery({ queryKey: ['study-report-version', studyId, revisionId, identity?.current.source_run_id, identity?.current.artifact_id], queryFn: () => api.researchRun(identity!.current.source_run_id), enabled: Boolean(identity), retry: false })
  const previous = useQuery({ queryKey: ['study-report-version', studyId, revisionId, identity?.previous?.source_run_id, identity?.previous?.artifact_id], queryFn: () => api.researchRun(identity!.previous!.source_run_id), enabled: Boolean(identity?.previous), retry: false })
  const currentDetail = current.isError ? null : checkedDetail(current.data, identity?.current)
  const previousDetail = previous.isError ? null : checkedDetail(previous.data, identity?.previous)
  const loading = pair.isPending || Boolean(identity && (current.isPending || (identity.previous && previous.isPending)))
  const failed = pair.isError || current.isError || Boolean(identity?.previous && previous.isError)
  const invalid = Boolean(pair.data && !identity) || Boolean(current.data && !currentDetail) || Boolean(identity?.previous && previous.data && !previousDetail)
  const detailRef = useDetailFocus(`${studyId}:${revisionId}`, !loading)
  const refreshing = pair.isFetching || current.isFetching || Boolean(identity?.previous && previous.isFetching)
  async function refresh() { await pair.refetch(); if (identity) { await current.refetch(); if (identity.previous) await previous.refetch() } }
  return <section ref={detailRef} tabIndex={-1} className="product-card study-report-comparison" aria-label="同一主题报告前后对照"><div className="product-card-head"><h2>报告前后对照</h2><div className="asset-ref-list"><button className="button secondary" disabled={refreshing} onClick={() => void refresh()}>重新读取两版</button><button className="button secondary" onClick={onClose}>收起对照</button></div></div><p className="product-muted">所选报告与它原来登记的上一版。这里只读取保存内容，不发起新研究或更新原数据。</p>{loading ? <p role="status">正在读取两份原报告…</p> : failed ? <AssetError retry={() => void refresh()} /> : invalid ? <p role="alert">版本或报告身份与所选主题不一致，未显示对照。请重新读取或核对原记录。</p> : identity && currentDetail && (!identity.previous || previousDetail) ? <ComparisonContent pair={identity} current={currentDetail} previous={previousDetail} back={back} /> : <p role="status">两份保存的报告尚未读取完整，暂不显示对照。</p>}</section>
}
