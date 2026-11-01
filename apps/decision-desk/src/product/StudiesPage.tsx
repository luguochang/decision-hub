import type { StudyTimelineView, StudyView } from '@decision-hub/contracts-ts'
import { useDetailFocus } from './useDetailFocus'
import { useEffect, useRef, useState } from 'react'
import { StudyEffectTracking } from './StudyEffectTracking'
import { StudyEffectTarget } from './StudyEffectTarget'
import { StudyActivity } from './StudyActivity'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, RefreshCw, Search } from 'lucide-react'
import { assets } from '../api/assets'
import { StudyTracking } from './StudyTracking'
import { StudyReportComparison } from './StudyReportComparison'
import { researchDisplayTitle } from './researchLanguage'
import { AssetBackLink, AssetError, AssetPagination, assetTime, instrumentName, methodName, directionName, horizonName, readOffset, routeChange, type AssetNavigate } from './assetFormat'

export function studyTitle(title: string, displayTitle?: string | null, maxLength = 90) {
  let label = researchDisplayTitle(displayTitle?.trim() ? displayTitle : title)
  if (label.trim() === 'Jefferson, The U.S. Economy and Monetary Policy') label = '杰斐逊：美国经济与货币政策'
  // A broad market label cannot replace the original research question.
  if (/^BTC (?:市场|永续)(?:与宏观市场)?研究$/.test(displayTitle?.trim() ?? '') && title.trim() !== displayTitle?.trim()) {
    const original = title.trim().replace(/^独立公开宏观研究(?:\s+[A-Za-z0-9][A-Za-z0-9_-]{0,31})?\s*[（(][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[）)]\s*[:：]\s*/i, '')
    // Only shorten an affirmative, explicit sole-BTC instrument sentence.
    const question = /^以\s*(BTC(?:-USDT(?:-SWAP)?)?)\s*为\s*唯一(?:研究)?标的\s*[，,:：]\s*(?:研究|核查|复核|观察)?\s*([^。！？!?；;\n]+)/i.exec(original)
    label = researchDisplayTitle(question ? `${question[1]}：${question[2].trim()}` : original || title.trim())
  }
  // A missing or entirely damaged title is not enough evidence to reconstruct a question.
  if (!label.trim() || /^[\s?？\uFFFD]+$/.test(label)) return '标题待核对'
  return maxLength > 0 && label.length > maxLength ? `${label.slice(0, maxLength - 3)}…` : label
}
function collectedSourceOnly(study: StudyView) {
  return study.entry_projection?.state === 'collected_not_researched' && study.entry_projection.research_runs === 0
}
export function studyEntryLabel(study: StudyView) {
  if (study.latest_report_at) return '已保存报告'
  switch (study.entry_projection?.state) {
    case 'has_research': return '已登记研究任务'
    case 'collected_not_researched': return '已收集，尚未研究'
    default: return '研究准入待确认'
  }
}
export function studyEntryAction(study: StudyView) {
  if (study.latest_report_at) return '阅读报告与变化'
  if (collectedSourceOnly(study)) return study.entry_projection?.latest_admission_decision === 'blocked' ? '查看准入限制' : '查看资料与准入'
  return study.entry_projection?.state === 'has_research' ? '查看研究任务' : '查看主题登记'
}
function studyEntryExplanation(study: StudyView) {
  const entry = study.entry_projection
  if (entry?.state === 'has_research') return `已登记 ${entry.research_runs} 次 AI 研究任务${study.latest_report_at ? '；可打开已保存报告。' : '；尚无已保存报告。'}`
  if (entry?.state === 'collected_not_researched') return entry.latest_admission_reason === 'no_high_impact_title_match'
    ? '本次没有发起 AI 研究：未匹配当前重要事件。'
    : entry.latest_admission_decision === 'blocked' ? '本次研究准入受限；尚无 AI 研究任务。' : '资料已登记；本次没有发起 AI 研究。'
  return study.latest_report_at ? '已有历史报告；研究准入仍待核对。' : '准入记录待核对；登记不代表研究已启动。'
}
function StudyTitle({ study, detail = false }: { study: StudyView; detail?: boolean }) {
  const title = studyTitle(study.title, study.entry_projection?.display_title, 0)
  const originalLanguage = /[A-Za-z]/.test(title) && !/[\u3400-\u9fff]/.test(title)
  return <><h3 className={detail ? 'study-detail-question' : 'study-card-question'}>{title}</h3>{originalLanguage && <small className="study-language-note">保留来源原文标题</small>}</>
}
function StudyAudit({ study }: { study: StudyView }) {
  return <details className="study-registration-audit"><summary>完整原题与登记依据</summary><p>{study.title || '原登记标题为空'}</p><p>原登记范围：{instrumentName(study.instrument)}；范围登记不代表来源事件已通过研究准入。</p><p>主题登记：{assetTime(study.created_at)}</p><details><summary>技术审计</summary><p>主题编号：<code>{study.study_id}</code></p>{study.entry_projection && <pre className="asset-json">{JSON.stringify(study.entry_projection, null, 2)}</pre>}</details></details>
}
type Revision = StudyTimelineView['revisions'][number]
export function revisionDirectionSummary(revision: Revision) {
  if (revision.change.comparison_status === 'first_recorded_revision') return '首份已登记报告'
  const changes = revision.direction_changes ?? []
  const knownActions = new Set(['long', 'short', 'neutral', 'no_trade'])
  const compared = (row: Revision['direction_changes'][number]) => row.comparison_status === 'compared'
    && row.previous_action !== null && row.current_action !== null
    && knownActions.has(row.previous_action) && knownActions.has(row.current_action)
    && typeof row.changed === 'boolean' && row.changed === (row.previous_action !== row.current_action)
  const sameInstrument = (rows: Revision['direction_changes']) => new Set(rows.map(row => row.instrument)).size === 1
  const ai = changes.filter(row => row.method_family === 'ai_synthesis')
  const daily = changes.filter(row => row.method_family === 'daily_rule')
  const parts: string[] = []
  if (ai.length) {
    const complete = ai.length === 3 && new Set(ai.map(row => row.horizon)).size === 3
      && ai.every(row => ['30m', '24h', '72h'].includes(row.horizon)) && sameInstrument(ai)
    parts.push(ai.some(row => row.comparison_status === 'method_changed') ? 'AI 方法不同，需逐周期核对'
      : !complete || !ai.every(compared) ? 'AI 判断部分不可比，需逐周期核对'
      : ai.some(row => row.changed === true) ? '可比 AI 周期方向有变化' : '三个可比 AI 周期方向相同')
  }
  if (daily.length) {
    parts.push(daily.length === 1 && daily[0].horizon === '7d' && compared(daily[0])
      ? daily[0].changed ? '7 天日线参考方向有变化' : '7 天日线参考方向相同'
      : '日线参考不可比，需核对原值')
  }
  return parts.length ? parts.join('；') : '尚无可比的方向档案'
}
const studyViews = {
  research: { label: '研究主题', kicker: '研究工作台', description: '查看报告、跟进研究任务与后续变化。', empty: '没有匹配的研究主题。可修改关键词，或查看来源资料。', unit: '个研究主题' },
  sources: { label: '来源资料', kicker: '研究工作台', description: '查看已收集资料及本次研究准入。', empty: '没有匹配的来源资料登记。可修改关键词，或查看全部登记。', unit: '个来源主题' },
  all: { label: '全部登记', kicker: '研究工作台', description: '查找研究主题、来源资料与待确认登记。', empty: '没有匹配的登记记录。可修改关键词后重新查找。', unit: '条主题与来源登记' },
} as const

export function StudiesPage({ params, onNavigate }: { params: URLSearchParams; onNavigate: AssetNavigate }) {
  const listBack = `#/studies${params.size ? `?${params}` : ''}`
  const studyId = params.get('study'), q = params.get('q') || '', offset = readOffset(params), revisionOffset = readOffset(params, 'revisions')
  const requestedView = params.get('view')
  const view = requestedView === 'research' || requestedView === 'sources' ? requestedView : 'all'
  const workspace = studyViews[view]
  const list = useQuery({ queryKey: ['asset-studies', view, q, offset], queryFn: () => assets.studies({ view, q: q || undefined, limit: 12, offset }), retry: false, refetchInterval: 30_000 })
  const detail = useQuery({ queryKey: ['asset-study', studyId, revisionOffset], queryFn: () => assets.study(studyId!, { limit: 8, offset: revisionOffset }), enabled: Boolean(studyId), retry: false, refetchInterval: 30_000 })
  // History pages must never relabel their first revision as the Study's latest report.
  const latestPage = useQuery({ queryKey: ['asset-study-latest', studyId], queryFn: () => assets.study(studyId!, { limit: 1, offset: 0 }), enabled: Boolean(studyId) && revisionOffset > 0, retry: false, refetchInterval: 30_000 })
  const latestRevision = revisionOffset === 0 ? detail.data?.revisions[0] : latestPage.data?.revisions[0]
  const effectRevision = params.get('effect_revision'), compareRevision = params.get('compare'), revisionParam = params.get('revisions')
  const [historyOpen, setHistoryOpen] = useState(Boolean(effectRevision || compareRevision || revisionParam !== null))
  const previousStudy = useRef(studyId)
  const detailRef = useDetailFocus(studyId ? `${studyId}:${revisionOffset}` : null, !detail.isPending)
  const change = (values: Record<string, string | undefined>) => onNavigate('studies', routeChange(params, { ...(Object.hasOwn(values, 'study') && values.study !== studyId ? { compare: undefined, effect_revision: undefined } : {}), ...values }))
  useEffect(() => { if (list.data && list.data.offset !== offset) change({ offset: String(list.data.offset) }) }, [list.data?.offset, offset, view])
  useEffect(() => { if (detail.data && detail.data.offset !== revisionOffset) change({ revisions: String(detail.data.offset) }) }, [detail.data?.offset, revisionOffset])
  useEffect(() => {
    const linkedHistory = Boolean(effectRevision || compareRevision || revisionParam !== null)
    if (previousStudy.current !== studyId) { previousStudy.current = studyId; setHistoryOpen(linkedHistory) }
    else if (linkedHistory) setHistoryOpen(true)
  }, [studyId, effectRevision, compareRevision, revisionParam])
  const openEffect = (revision: string) => { setHistoryOpen(true); change({ effect_revision: effectRevision === revision ? undefined : revision }) }
  return <div className="content-wrap product-page studies-page">
    <AssetBackLink back={params.get('back')} />
    <div className="product-heading"><div><span className="product-kicker">{workspace.kicker}</span><h1>{workspace.label}</h1><p>{workspace.description}</p></div><button className="button secondary" disabled={list.isFetching || (Boolean(studyId) && (detail.isFetching || latestPage.isFetching))} onClick={() => { void list.refetch(); if (studyId) { void detail.refetch(); if (revisionOffset > 0) void latestPage.refetch() } }}><RefreshCw size={15} />刷新主题</button></div>
    <div className="study-workspace-toolbar">
      <nav className="study-workspace-views" aria-label="研究工作台分类">{(['research', 'sources', 'all'] as const).map(value => <button key={value} type="button" className={`button ${view === value ? 'primary' : 'secondary'}`} aria-pressed={view === value} onClick={() => change({ view: value, offset: undefined, study: undefined, revisions: undefined, compare: undefined, effect_revision: undefined })}>{value === 'all' ? '全部' : studyViews[value].label}</button>)}</nav>
      <form key={q} className="asset-filters study-workspace-search" onSubmit={event => { event.preventDefault(); const query = String(new FormData(event.currentTarget).get('q') ?? '').trim(); change({ q: query || undefined, offset: undefined, study: undefined, revisions: undefined }) }}><label>查找{workspace.label}<input name="q" defaultValue={q} placeholder="例如：美联储、BTC" /></label><button className="button secondary" type="submit"><Search size={14} />查找</button>{q && <button className="product-link" type="button" onClick={() => change({ q: undefined, offset: undefined, study: undefined, revisions: undefined })}>清除关键词</button>}</form>
    </div>
    {studyId && <section ref={detailRef} tabIndex={-1} className="product-card asset-detail study-workspace-detail">
      <div className="product-card-head"><h2>{detail.data && collectedSourceOnly(detail.data.study) ? '来源资料与准入' : '主题工作区'}</h2><button className="button secondary" onClick={() => change({ study: undefined, revisions: undefined })}>收起主题</button></div>
      {detail.isPending ? <p>读取主题与历次版本…</p> : detail.isError ? <AssetError retry={() => void detail.refetch()} /> : <>
        <StudyTitle study={detail.data.study} detail />
        <p className="study-workspace-meta">{detail.data.total} 份已保存报告 · {detail.data.study.status === 'active' ? '允许后续自动复查' : '自动复查已暂停'}{!collectedSourceOnly(detail.data.study) && ` · ${instrumentName(detail.data.study.instrument)}`}</p>
        {latestRevision ? <section className="study-latest-report" aria-label="最新已保存报告">
          <div className="study-revision-head"><strong>最新已保存报告</strong><time>{assetTime(latestRevision.created_at)}</time></div>
          <p>{revisionDirectionSummary(latestRevision)}</p>
          <div className="delivery-actions"><button className="button primary" onClick={() => onNavigate('report', { run: latestRevision.source_run_id, back: listBack })}>阅读最新已保存报告<ArrowRight size={14} /></button>{latestRevision.strategy_version === 'research.v1' && <><button className="button secondary" onClick={() => { setHistoryOpen(true); change({ compare: latestRevision.revision_id }) }}>{latestRevision.previous_revision_id ? '对照原前版' : '查看这版内容'}</button><button className="button secondary" onClick={() => openEffect(latestRevision.revision_id)} aria-expanded={effectRevision === latestRevision.revision_id}>{effectRevision === latestRevision.revision_id ? '收起效果跟踪' : '登记或查看效果跟踪'}</button></>}</div>
        </section> : detail.data.total === 0 ? <p className="product-notice" role="status">这个主题尚无已保存报告。下方可查看任务、准入和后续复查状态。</p> : revisionOffset > 0 && latestPage.isError ? <div className="study-latest-report"><p>最新报告暂时无法读取；当前历史页仍可阅读。</p><AssetError retry={() => void latestPage.refetch()} /></div> : <p role="status">正在读取最新已保存报告…</p>}
        <StudyActivity key={detail.data.study.study_id} activity={detail.data.activity} paused={detail.data.study.status !== 'active'} back={listBack} onNavigate={onNavigate} />
        <details className="study-history" open={historyOpen} onToggle={event => setHistoryOpen(event.currentTarget.open)}>
          <summary>报告版本与变化 · {detail.data.total} 份</summary>
          {detail.data.total === 0 && <p className="product-muted">尚无已保存的报告版本。</p>}
          <ol className="study-revisions">{detail.data.revisions.map(revision => <li key={revision.revision_id}>
            <div className="study-revision-head"><time>{assetTime(revision.created_at)}</time><strong>{revisionDirectionSummary(revision)}</strong><span>已保存报告</span></div>
            <div className="delivery-actions"><button className="product-link" onClick={() => onNavigate('report', { run: revision.source_run_id, back: listBack })}>阅读这一版报告<ArrowRight size={14} /></button>{revision.strategy_version === 'research.v1' && <><button className="product-link" onClick={() => change({ compare: revision.revision_id })}>{revision.previous_revision_id ? '并排对照原前版' : '展开这版内容'}<ArrowRight size={14} /></button><button className="product-link" onClick={() => openEffect(revision.revision_id)} aria-expanded={effectRevision === revision.revision_id}>{effectRevision === revision.revision_id ? '收起效果跟踪' : '登记或查看效果跟踪'}</button></>}</div>
            {revision.strategy_version === 'research.v1' && effectRevision === revision.revision_id && <StudyEffectTracking key={revision.revision_id} study={detail.data.study.study_id} revision={revision.revision_id} run={revision.source_run_id} artifact={revision.artifact_id} />}
            <details className="study-revision-changes"><summary>查看各周期判断与采纳材料变化</summary>{revision.direction_changes?.length ? <ul className="revision-directions">{revision.direction_changes.map(change => <li key={`${change.method_family}:${change.instrument}:${change.horizon}`}><strong>{methodName(change.method_family)} · {horizonName(change.horizon)}</strong><span>{change.previous_action === null ? '前版未记录' : directionName(change.previous_action)} → {change.current_action === null ? '本版未记录' : directionName(change.current_action)}</span><small>{change.comparison_status === 'compared' ? change.changed ? '方向改变' : '方向延续' : change.comparison_status === 'method_changed' ? '方法版本不同，不直接比较' : '缺少可比版本'}</small></li>)}</ul> : <p>该版本尚无独立观点档案，请打开原报告核对。</p>}<div className="study-delta"><span>新增采纳材料 {revision.change.added_evidence_count}</span><span>上版采纳材料本版未采纳 {revision.change.removed_evidence_count}</span><small>按内容哈希去重；不表示报告引用变化或独立来源数。</small></div></details>
            <details><summary>发布限制摘要</summary><p>{revision.change.current_summary === 'The remaining causal or reviewer inquiry has no executable declared route.' ? '仍有未解决的因果或复核问题，当前已声明的工具无法继续补证。' : revision.change.current_summary}</p>{revision.change.previous_summary && <><h4>上一版发布摘要</h4><p>{revision.change.previous_summary}</p></>}</details>
            <details><summary>版本审计</summary><p>版本登记：{assetTime(revision.recorded_at)}</p><code>{revision.revision_id}</code><p>方法：{revision.strategy_version}</p></details>
          </li>)}</ol>
          {effectRevision && !detail.data.revisions.some(revision => revision.revision_id === effectRevision && revision.strategy_version === 'research.v1') && <StudyEffectTarget key={effectRevision} study={detail.data.study.study_id} revision={effectRevision} />}
          <AssetPagination total={detail.data.total} offset={detail.data.offset} limit={8} onChange={value => { setHistoryOpen(true); change({ revisions: String(value) }) }} />
        </details>
        <section className="study-workspace-review" aria-label="后续自动复查"><h3>后续自动复查</h3><StudyTracking key={detail.data.study.study_id} study={detail.data.study} /></section>
        <StudyAudit study={detail.data.study} />
      </>}
    </section>}
    {studyId && compareRevision && <StudyReportComparison key={JSON.stringify([studyId, compareRevision])} studyId={studyId} revisionId={compareRevision} back={listBack} onClose={() => change({ compare: undefined })} />}
    <section className="product-card"><div className="product-card-head"><h2>{workspace.label}</h2><span>{list.isError ? '读取失败' : list.data ? `${list.data.total} ${workspace.unit}` : '读取中'}</span></div>{list.isPending ? <p>读取主题列表…</p> : list.isError ? <AssetError retry={() => void list.refetch()} /> : <>
      {list.data.items.length === 0 && <p role="status">{workspace.empty}</p>}
      <div className="study-grid">{list.data.items.map(study => <article className="study-card" key={study.study_id}>
        <div className="study-card-meta"><span>{studyEntryLabel(study)}</span><span>{study.status === 'active' ? '自动复查已开启' : '自动复查已暂停'}</span></div>
        <StudyTitle study={study} /><p className="study-card-progress">{studyEntryExplanation(study)}</p>
        <small>{study.latest_report_at ? '最近报告' : '主题登记'}：{assetTime(study.latest_report_at ?? study.created_at)}</small>
        <button className="product-link" onClick={() => change({ study: study.study_id, revisions: undefined })}>{studyEntryAction(study)}<ArrowRight size={14} /></button>
        <StudyAudit study={study} />
      </article>)}</div>
      <AssetPagination total={list.data.total} offset={list.data.offset} limit={12} onChange={value => change({ offset: String(value), study: undefined, revisions: undefined })} />
    </>}</section>
  </div>
}
