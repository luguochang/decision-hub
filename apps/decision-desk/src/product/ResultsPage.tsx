import { useEffect } from 'react'
import { useDetailFocus } from './useDetailFocus'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, RefreshCw } from 'lucide-react'
import type { AnalyticalCohortView, AnalyticalOutlookDetailView } from '@decision-hub/contracts-ts'
import { assets } from '../api/assets'
import { api } from '../api/client'
import { researchDisplayText } from './researchLanguage'
import { CaptureReadback } from './SourceHistoryPanel'
import { AssetBackLink, AssetError, AssetPagination, assetTime, instrumentName, coverageName, directionName, horizonName, methodName, observationName, percent, qualityName, readOffset, routeChange, type AssetNavigate } from './assetFormat'

const reasons: Record<string, string> = {
  retrospective_projection: '历史回填，不用于前瞻效果统计', no_directional_hypothesis: '当时没有形成方向判断',
  accepted_versioned_inputs_missing: '未留存足够的已采纳版本引用', future_as_of_claim: '正文引用了晚于报告的截至时间',
  input_after_freeze: '输入时间晚于判断冻结时间', model_hypothesis_expired: '发布时原判断窗口已经过期',
  unresolved_evidence_reference: '部分依据未能定位', scope_unknown: '历史研究范围不明确',
  observation_market_not_enabled: '这个品种尚未开通后验观察', model_finished_after_freeze: '模型完成时间与冻结时间不一致',
  daily_bar_not_closed: '日线在冻结时尚未收盘', daily_as_of_invalid: '日线日期无法确认',
}

function directionOutcome(item: AnalyticalOutlookDetailView['outlook']) {
  if (item.cohort === 'retrospective' || item.eligibility === 'excluded') return '不纳入前瞻命中'
  if (item.action === 'neutral' || item.action === 'no_trade') return '不计算方向命中'
  if (item.observation.status !== 'completed' || item.observation.direction_correct == null) return '尚未核定'
  return item.observation.direction_correct ? '账本记为命中，需核对原件' : '账本记为未命中，需核对原件'
}

// These reasons identify an unavailable matching verifier, not changed originals.
// Keep exact codes: hash, payload and replay mismatches still use the invalid state.
const sourceRecoveryNotices = new Map<string, string>([
  ['context_selection_implementation_unavailable', '需恢复与保存记录匹配的核验程序后重新核对，当前不计入认证样本。'],
  ['assessment_loaded_verifier_source_changed', '需加载匹配的核验程序后重新核对，当前不计入认证样本。'],
  ['context_selection_loaded_source_changed', '需加载匹配的核验程序后重新核对，当前不计入认证样本。'],
])

function AssessmentSummary({ data }: { data: AnalyticalOutlookDetailView }) {
  const assessment = data.assessment
  if (!assessment) return <p role="status">这条记录尚未完成原件核对，暂不作为认证效果样本。</p>
  const backgroundPending = assessment.source_status === 'unverifiable' && assessment.method_status === 'unverifiable' && assessment.selection_status === 'unchecked' && assessment.directional_usable === false && (assessment.source_reason === 'assessment_background_pending' || assessment.source_reason === 'assessment_recheck_pending')
  const recoveryNotice = assessment.source_status !== 'verified' && typeof assessment.source_reason === 'string' ? sourceRecoveryNotices.get(assessment.source_reason) : undefined
  const source = backgroundPending ? '等待后台核验' : recoveryNotice ? '核验程序待恢复' : assessment.source_status === 'verified' ? '已核对' : assessment.source_status === 'invalid' ? '原件不一致' : '缺少可核对的原件'
  const label = backgroundPending ? '等待后台核验' : assessment.label_status === 'verified' ? '已核对' : assessment.label_status === 'not_due' ? '还未到观察时间' : assessment.label_status === 'pending' ? '等待采集' : assessment.label_status === 'retry' ? '等待补采' : assessment.label_status === 'invalid' ? '行情或计算记录不一致' : '缺少行情原件'
  const selection = Array.isArray(assessment.limitations) && assessment.limitations.includes('method_identity_ambiguous') ? '首次记录或研究配方待确认，暂不计入样本' : assessment.selection_status === 'selected' ? '同课题、同一研究配方的首次记录' : assessment.selection_status === 'repeat' ? '后续更新，不替换首次样本' : '选样身份尚未核定'
  return <section aria-label="效果样本核对"><h3>报告与行情核对</h3><dl className="asset-facts"><div><dt>当时的报告与依据</dt><dd>{source}</dd></div><div><dt>事后行情与计算</dt><dd>{label}</dd></div><div><dt>样本位置</dt><dd>{selection}</dd></div><div><dt>方向结果</dt><dd>{assessment.directional_usable === true && assessment.selection_status === 'selected' ? assessment.verified_direction_correct === true ? '核对后命中' : '核对后未命中' : '暂不计入认证方向样本'}</dd></div></dl>{backgroundPending && <p role="status" className="product-muted">原始判断和观察记录已保留，等待后台核验；完成核验前不计入认证效果样本。可点“刷新观察结果”查看核验进度。</p>}{recoveryNotice && <p className="product-muted">{recoveryNotice}</p>}<p className="product-muted">保留首次记录，后续更新可继续阅读；更早记录或配方身份未确认时，后续判断不替代它。</p><details><summary>查看核对记录</summary><pre className="asset-json">{JSON.stringify(assessment, null, 2)}</pre></details></section>
}

function CohortOutcome({ group }: { group: AnalyticalCohortView }) {
  const recipeKnown = group.recipe_status === 'verified' && typeof group.stable_recipe_hash === 'string' && /^[a-f0-9]{64}$/.test(group.stable_recipe_hash)
  const recipeMatches = recipeKnown && group.method_recipe_hashes?.length === 1 && group.method_recipe_hashes[0] === group.stable_recipe_hash
  const legacy = group.assessment_version === 'fixed-study-originals.v1'
  const assessed = (legacy || (group.assessment_version === 'authenticated-recipe-originals.v2' && recipeMatches)) &&
    [group.fixed_sample_count, group.repeat_count, group.source_verified_count, group.label_verified_count].every(value => typeof value === 'number' && Number.isInteger(value) && value >= 0) && Array.isArray(group.method_recipe_hashes)
  const rateReady = assessed && (!legacy || !group.assessment_reasons?.method_identity_ambiguous) && group.method_recipe_hashes?.length === 1 && group.minimum_rate_samples >= 30 && group.directional_count >= group.minimum_rate_samples &&
    typeof group.directional_hit_rate === 'number' && Number.isFinite(group.directional_hit_rate) && group.directional_hit_rate >= 0 && group.directional_hit_rate <= 1 &&
    group.correct_count <= group.directional_count && (group.fixed_sample_count ?? -1) >= group.directional_count && group.distinct_studies >= group.directional_count && group.directional_count <= group.total &&
    (group.source_verified_count ?? -1) >= group.directional_count && (group.source_verified_count ?? -1) <= group.total && (group.label_verified_count ?? -1) >= group.directional_count && (group.label_verified_count ?? -1) <= group.total &&
    Math.abs(group.directional_hit_rate - group.correct_count / group.directional_count) < 1e-12
  const outcome = group.cohort === 'retrospective' ? '不纳入前瞻命中率' : group.action === 'neutral' || group.action === 'no_trade' ? '不计算方向命中' : !assessed ? (group.recipe_status ? '认证统计尚未形成' : '尚未核验') : (group.method_recipe_hashes?.length ?? 0) > 1 ? '研究配方不同，暂不合并' : rateReady ? `${(group.directional_hit_rate! * 100).toFixed(1)}%` : group.directional_count < 30 ? `认证样本不足（${group.directional_count}）` : '核验统计待确认'
  const checked = group.assessment_checked_count
  const pending = group.assessment_pending_count
  const progressKnown = typeof checked === 'number' && typeof pending === 'number' && Number.isInteger(checked) && Number.isInteger(pending) && checked >= 0 && pending >= 0 && checked + pending === group.total
  const countedReason = (name: string) => typeof group.assessment_reasons?.[name] === 'number' && group.assessment_reasons[name] > 0
  const recoveryNeeded = [...sourceRecoveryNotices.keys()].some(countedReason)
  return <>{outcome}{group.cohort === 'retrospective' ? <small>历史档案未纳入后台认证</small> : progressKnown && <small>本轮原件检查 {checked}/{group.total} 条{pending! > 0 ? `；还有 ${pending} 条等待后台核验` : '；已覆盖全部记录'}</small>}<small>{assessed ? `固定首次记录 ${group.fixed_sample_count} · 后续重复 ${group.repeat_count}` : '原始记录保留，未认证记录不计入方向样本'}</small>{assessed && <small>报告核对 {group.source_verified_count} · 行情核对 {group.label_verified_count} · 方向样本 {group.directional_count}</small>}{rateReady && <small>仅统计本配方已认证的首次方向判断</small>}{recoveryNeeded && <small>部分记录需恢复匹配的核验程序后重新核对，未核验记录不计入方向样本</small>}{countedReason('daily_rule_original_assessment_not_implemented') ? <small>日线规则的原件核对尚未支持，暂不生成认证统计</small> : group.cohort === 'prospective' && group.recipe_status === 'unchecked' && !recoveryNeeded && <small>{progressKnown && pending! > 0 ? '等待后台核验研究配方，未核验记录暂不计入认证样本' : '缺少可核对的方法原件，暂无法归入同一研究配方'}</small>}{countedReason('method_identity_ambiguous') && <small>部分记录的首次身份尚未确认，已排除于方向样本</small>}{countedReason('not_due') && <small>部分判断还未到观察时间</small>}{(countedReason('pending') || countedReason('retry')) && <small>部分判断的行情等待采集或补采</small>}<details><summary>查看原账本统计</summary><p>方向记录 {group.record_directional_count ?? group.directional_count ?? '未记录'} 条，账本记为命中 {group.record_correct_count ?? group.correct_count ?? '未记录'} 条；重复和未核验记录不作为认证效果分母。</p>{group.assessment_reasons && <pre className="asset-json">{JSON.stringify(group.assessment_reasons, null, 2)}</pre>}</details></>
}

function CohortMethod({ group }: { group: AnalyticalCohortView }) {
  const versions = group.saved_method_versions ?? (group.method_version ? [group.method_version] : [])
  return <><strong>{methodName(group.method_family)}</strong>{group.cohort === 'prospective' && group.recipe_status && <small>{group.recipe_status === 'verified' ? '研究配方已核对' : '研究配方待确认'}</small>}<details><summary>方法记录</summary>{group.stable_recipe_hash && <><p>本组原件核对的方法标识：</p><code>{group.stable_recipe_hash}</code></>}<p>{versions.length} 个保存版本 · {group.distinct_studies} 个研究课题</p>{versions.map(version => <p key={version}><code>{version}</code></p>)}</details></>
}

function resultConditionText(text: string) {
  // Translate observed direction prose and the saved daily relative-volume field.
  // Original enum and condition strings remain in the closed audit below.
  const prose = text.split(/(https?:\/\/[^\s<>"']+|\b(?:[a-zA-Z][a-zA-Z0-9_]*_(?:id|ref)|id)"?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s;,，；]+))/g)
    .map((part, index) => index % 2 ? part : part
      .replace(/((?:30m|24h|72h|30\s*分钟|(?:24|72)\s*小时)\s+)(long|short)(?=\s+(?:降为|转为|调整为))/g,
        (_match, window: string, direction: string) => `${window}${direction === 'long' ? '偏多' : '偏空'}判断`)
      .replace(/((?:30m|24h|72h|30\s*分钟|(?:24|72)\s*小时)\s*的\s*)(long|short)(?=\s*是)/g,
        (_match, window: string, direction: string) => `${window}${direction === 'long' ? '偏多' : '偏空'}判断`)
      // Backend: latest completed daily volume / the ordered 20-day volume midpoint.
      .replace(/\bvolume_ratio_20\b/g, '20日相对成交量'))
    .join('')
  return researchDisplayText(prose)
}

function OutlookDetail({ data, onNavigate, back }: { data: AnalyticalOutlookDetailView; onNavigate: AssetNavigate; back: string }) {
  const item = data.outlook
  return <><div className="asset-detail-title"><strong>{methodName(item.method_family)} · {horizonName(item.horizon)} · {directionName(item.action)}</strong><span>{instrumentName(item.instrument)}</span></div>
    {typeof data.raw_payload.thesis === 'string' && <details><summary>当时保存的完整解释</summary><p>{data.raw_payload.thesis}</p></details>}
    <dl className="asset-facts"><div><dt>本观点引用材料最晚收到</dt><dd>{assetTime(item.data_as_of)}</dd></div><div><dt>判断冻结</dt><dd>{assetTime(item.window_start_at)}</dd></div><div><dt>观察到期</dt><dd>{assetTime(item.window_end_at)}</dd></div><div><dt>实际市场变化</dt><dd>{percent(item.observation.market_return_pct)} · {qualityName(item.observation.quality_status)}</dd></div><div><dt>方向核对</dt><dd>{directionOutcome(item)}</dd></div></dl>
    <p className="product-muted">观察窗口从当时的判断冻结起计算，到观察到期结束；条件保留当时的措辞。</p>
    <AssessmentSummary data={data} />
    <p><strong>后续确认：</strong>{resultConditionText(item.trigger)}</p><p><strong>失效条件：</strong>{resultConditionText(item.invalidation)}</p>
    {item.exclusion_reasons.length > 0 && <ul>{item.exclusion_reasons.map(reason => <li key={reason}>{reasons[reason] ?? '存在待核对的数据条件，详见审计记录'}</li>)}</ul>}
    <p>{item.action === 'neutral' ? '中性观察只记录市场变化，不计算方向命中。' : '方向变化采用统一价格基准，未扣交易成本，不代表交易盈亏。'}</p>
    <button className="product-link" onClick={() => onNavigate('report', { run: item.run_id, back })}>回到对应报告<ArrowRight size={14} /></button>
    <details><summary>查看依据版本与采集审计</summary><details><summary>查看原始判断条件</summary><pre className="asset-json" aria-label="原始判断条件">{JSON.stringify({ horizon: item.horizon, action: item.action, trigger: item.trigger, invalidation: item.invalidation }, null, 2)}</pre></details><p>{item.fact_version_refs.length} 个已冻结事实版本</p><div className="asset-ref-list">{item.fact_version_refs.map((id, index) => <button key={id} className="product-link" onClick={() => onNavigate('facts', { version: id, as_of: item.window_start_at, back })}>依据 {index + 1}<ArrowRight size={13} /></button>)}</div><p>模型自评概率：{item.subjective_probability === null ? '未提供' : `${Math.round(item.subjective_probability * 100)}%`} · {item.probability_status === 'calibrated' ? '有匹配校准凭据' : '未证实为校准概率'}</p><code>{item.method_version}</code><p>后验采集共尝试 {data.attempts_total} 次；最近记录：</p>{data.attempts.map(attempt => <section key={attempt.attempt} aria-label={`后验采集第 ${attempt.attempt} 次`}><p>第 {attempt.attempt} 次 · {assetTime(attempt.started_at)} · {attempt.error_code ? '当次采集未完成' : attempt.finished_at ? '已结束' : '进行中'}{attempt.error_code && <> · <code>{attempt.error_code}</code></>}</p>{attempt.raw_capture_ids?.length > 0 && <div className="asset-ref-list">{attempt.raw_capture_ids.map((id, index) => <CaptureReadback key={id} captureId={id} index={index + 1} />)}</div>}</section>)}{data.attempts_total > data.attempts_limit && <section aria-label="后验采集记录分页"><AssetPagination total={data.attempts_total} offset={data.attempts_offset} limit={data.attempts_limit} onChange={value => onNavigate('results', { ...Object.fromEntries(new URLSearchParams(back.split('?')[1])), attempts: String(value) })} /></section>}{data.window && <details><summary>已保存的行情窗口与计算依据</summary><pre className="asset-json">{JSON.stringify(data.window, null, 2)}</pre></details>}</details>
  </>
}

export function ResultsPage({ params = new URLSearchParams(), onNavigate }: { params?: URLSearchParams; onNavigate: AssetNavigate }) {
  const queryClient = useQueryClient()
  const reportReturnParams = new URLSearchParams(params)
  reportReturnParams.delete('back')
  const cohort = params.get('cohort') || 'prospective'
  const family = params.get('method') || ''
  const offset = readOffset(params)
  const groupOffset = readOffset(params, 'groups')
  const selected = params.get('outlook')
  const attemptsOffset = readOffset(params, 'attempts')
  const horizon = params.get('horizon') || '', status = params.get('status') || ''
  const filters = { cohort, method_family: family || undefined, run_id: params.get('run') || undefined, horizon: horizon || undefined, status: status || undefined, ...Object.fromEntries(['market', 'instrument', 'method_version', 'stable_recipe_hash', 'recipe_status', 'action', 'research_quality', 'quality_status', 'study_id', 'since', 'until'].map(key => [key, params.get(key) || undefined])) }
  const list = useQuery({ queryKey: ['asset-outlooks', filters, offset], queryFn: () => assets.outlooks({ ...filters, limit: 12, offset }), retry: false, refetchInterval: 30_000 })
  const groups = useQuery({ queryKey: ['asset-cohorts', filters, groupOffset], queryFn: () => assets.cohorts({ ...filters, limit: 12, offset: groupOffset }), retry: false, refetchInterval: 30_000 })
  const detail = useQuery({
    queryKey: ['asset-outlook', selected, attemptsOffset],
    queryFn: () => assets.outlook(selected!, { limit: 10, offset: attemptsOffset }),
    enabled: Boolean(selected), retry: false,
    refetchInterval: query => {
      const value = query.state.data
      return value && (['pending', 'running', 'retry'].includes(value.outlook.observation.status)
        || ['not_due', 'pending', 'retry'].includes(value.assessment?.label_status ?? '')) ? 30_000 : false
    },
  })
  const calibrations = useQuery({ queryKey: ['results-calibrations'], queryFn: api.strategyCalibrations, retry: false })
  const detailRef = useDetailFocus(selected, !detail.isPending)
  const change = (values: Record<string, string | undefined>) => onNavigate('results', routeChange(params, { ...(Object.hasOwn(values, 'outlook') ? { attempts: undefined } : {}), ...values }))
  const groupKeys = ['market', 'instrument', 'method_version', 'stable_recipe_hash', 'recipe_status', 'action', 'research_quality', 'quality_status']
  const resetGroup = Object.fromEntries(groupKeys.map(key => [key, undefined]))
  const groupSelected = groupKeys.some(key => params.has(key))
  useEffect(() => { if (list.data && list.data.offset !== offset) change({ offset: String(list.data.offset) }) }, [list.data?.offset, offset])
  useEffect(() => { if (groups.data && groups.data.offset !== groupOffset) change({ groups: String(groups.data.offset) }) }, [groups.data?.offset, groupOffset])
  useEffect(() => { if (detail.data && detail.data.attempts_offset !== attemptsOffset) change({ attempts: String(detail.data.attempts_offset) }) }, [detail.data?.attempts_offset, attemptsOffset])
  return <div className="content-wrap product-page results-page"><AssetBackLink back={params.get('back')} />
    <div className="product-heading"><div><span className="product-kicker">研究之后，继续核对</span><h1>研究效果</h1><p>当时怎样判断，后来市场怎样变化。AI 综合判断与日线规则分别观察。</p></div><button className="button secondary" disabled={list.isFetching || groups.isFetching || (Boolean(selected) && detail.isFetching)} onClick={() => { void queryClient.invalidateQueries({ queryKey: ['asset-outlooks'] }); void queryClient.invalidateQueries({ queryKey: ['asset-cohorts'] }); void queryClient.invalidateQueries({ queryKey: ['asset-outlook'] }); void calibrations.refetch() }}><RefreshCw size={15} />刷新记录</button></div>
    <div className="asset-filters">{params.get('run') && <button className="button secondary" onClick={() => change({ run: undefined, offset: undefined, groups: undefined, outlook: undefined })}>正在查看单次研究 · 查看全部</button>}<label>记录范围<select value={cohort} onChange={e => change({ ...resetGroup, cohort: e.target.value, offset: undefined, groups: undefined, outlook: undefined })}><option value="prospective">新建前瞻记录</option><option value="retrospective">历史回填档案</option></select></label><label>判断方法<select value={family} onChange={e => change({ ...resetGroup, method: e.target.value || undefined, offset: undefined, groups: undefined, outlook: undefined })}><option value="">全部方法（分组显示）</option><option value="ai_synthesis">AI 综合判断</option><option value="daily_rule">日线规则</option></select></label><label>观察窗口<select value={horizon} onChange={e => change({ ...resetGroup, horizon: e.target.value || undefined, offset: undefined, groups: undefined, outlook: undefined })}><option value="">全部窗口</option>{['30m', '24h', '72h', '7d'].map(key => <option key={key} value={key}>{horizonName(key)}</option>)}</select></label><label>观察进度<select value={status} onChange={e => change({ status: e.target.value || undefined, offset: undefined, groups: undefined, outlook: undefined })}><option value="">全部进度</option>{['pending', 'running', 'retry', 'completed', 'excluded'].map(key => <option key={key} value={key}>{observationName(key)}</option>)}</select></label>{groupSelected && <button className="button secondary" onClick={() => change({ ...resetGroup, offset: undefined, groups: undefined, outlook: undefined })}>清除分组限定</button>}<p>{cohort === 'retrospective' ? '这些判断在事后补建档案，全部排除于前瞻评估。' : '窗口从报告冻结时起计算；原始内容及引用版本保留。'}</p></div>
    {groupSelected && <p className="product-muted" role="status">{params.has('stable_recipe_hash') ? '已按核对的研究配方' : params.get('recipe_status') === 'unchecked' ? '已按待确认的方法档案' : '已按保存的方法版本'}、{instrumentName(params.get('instrument') ?? '')}、{horizonName(horizon)}、{directionName(params.get('action') ?? '')}及材料/行情质量限定明细。修改方法或窗口将清除此分组限定。</p>}
    {selected && <section ref={detailRef} tabIndex={-1} className="product-card asset-detail"><div className="product-card-head"><h2>当时的判断与实际观察</h2><button className="button secondary" disabled={detail.isFetching} onClick={() => void detail.refetch()}>{detail.isFetching ? '正在刷新观察…' : '刷新观察结果'}</button><button className="button secondary" onClick={() => change({ outlook: undefined })}>收起详情</button></div>{detail.isPending ? <p>读取判断详情…</p> : detail.isError ? <AssetError retry={() => void detail.refetch()} /> : <OutlookDetail data={detail.data} onNavigate={onNavigate} back={`#/results?${reportReturnParams.toString()}`} />}</section>}
    <section className="product-card"><div className="product-card-head"><h2>逐条判断</h2><span>{list.isError ? '读取失败' : list.data ? `${list.data.total.toLocaleString()} 条已保存记录` : '正在读取'}</span></div>{list.isPending ? <p>读取已保存的观点…</p> : list.isError ? <AssetError retry={() => void list.refetch()} /> : list.data.items.length ? <><div className="table-wrap"><table className="asset-table"><thead><tr><th>方法与品种</th><th>窗口 / 判断</th><th>冻结时间</th><th>观察进度</th><th>市场变化</th><th>详情</th></tr></thead><tbody>{list.data.items.map(item => <tr key={item.outlook_id}><td><strong>{methodName(item.method_family)}</strong><small>{instrumentName(item.instrument)}</small></td><td>{horizonName(item.horizon)}<small>{directionName(item.action)}</small></td><td>{assetTime(item.window_start_at)}</td><td>{observationName(item.observation.status)}</td><td>{percent(item.observation.market_return_pct)}<small>{qualityName(item.observation.quality_status)}</small></td><td><button className="product-link" onClick={() => change({ outlook: item.outlook_id })}>查看判断</button></td></tr>)}</tbody></table></div><AssetPagination total={list.data.total} offset={list.data.offset} limit={12} onChange={value => change({ offset: String(value), outlook: undefined })} /></> : <div className="product-empty"><h3>这个范围还没有观点记录</h3><p>当前筛选下没有匹配记录。可调整窗口、进度或清除分组限定；没有记录不代表方向判断错误。</p></div>}</section>
    <section className="product-card"><h2>按同一种方法复盘</h2><p className="product-muted">按课题、研究配方和观察窗口保留首次记录（失败或无方向也保留）。更早记录或配方身份未确认时，后续更新不替代它。报告、方法和行情都核对通过的首次方向样本满 30 个才显示命中率。必需材料已覆盖仅说明达到本次研究的最低必需覆盖要求；补充材料仍可能缺失，也不表示预测已校准。点击分组查看全部原始记录。</p>{groups.isPending ? <p>读取分组结果…</p> : groups.isError ? <AssetError retry={() => void groups.refetch()} /> : <>{groups.data.items.length === 0 && <p role="status">当前筛选下没有可复盘的分组。</p>}<div className="table-wrap"><table className="asset-table"><thead><tr><th>方法</th><th>品种 / 窗口</th><th>方向 / 质量</th><th>已观察 / 记录</th><th>待到期 / 补采 / 排除</th><th>方向命中</th><th>样本</th></tr></thead><tbody>{groups.data.items.map((group, index) => <tr key={index}><td style={{ minWidth: 124 }}><CohortMethod group={group} /></td><td>{instrumentName(group.instrument)}<small>{horizonName(group.horizon)}</small></td><td>{directionName(group.action)}<small>{qualityName(group.quality_status)} · {coverageName(group.research_quality)}</small></td><td>{group.completed} / {group.total}</td><td>{group.pending} / {group.retry} / {group.excluded}</td><td><CohortOutcome group={group} /></td><td><button className="product-link" onClick={() => change({ market: group.market, instrument: group.instrument, method: group.method_family, method_version: group.recipe_status === 'verified' ? params.get('method_version') || undefined : group.method_version ?? undefined, stable_recipe_hash: group.recipe_status === 'verified' ? group.stable_recipe_hash ?? undefined : undefined, recipe_status: group.recipe_status === 'unchecked' ? 'unchecked' : undefined, horizon: group.horizon, action: group.action, research_quality: group.research_quality, quality_status: group.quality_status ?? 'unlabelled', offset: undefined, groups: undefined, outlook: undefined })}>查看这组记录</button></td></tr>)}</tbody></table></div><AssetPagination total={groups.data.total} offset={groups.data.offset} limit={12} onChange={value => change({ groups: String(value) })} /></>}</section>
    <details className="product-card"><summary>校准与旧预测账本</summary>{calibrations.isPending ? <p>读取校准记录…</p> : calibrations.isError ? <AssetError retry={() => void calibrations.refetch()} /> : <p>{calibrations.data.length ? `有 ${calibrations.data.length} 条校准评估，请结合固定版本与样本核对。` : '尚无已保存的校准结论。当前分组观察不等于稳定预测能力。'}</p>}<button className="product-link" onClick={() => onNavigate('Inbox')}>查看原有预测与结果账本<ArrowRight size={14} /></button></details>
  </div>
}
