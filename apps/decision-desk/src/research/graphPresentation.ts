import type { ResearchRunDetailView } from '../api/types'
import { researchDisplayText } from '../product/researchLanguage'
import { sourceNames, marketSourceNames } from '../product/operationLabels'
import { requirementNames as reviewRequirementNames } from './technicalPresentation'

type GraphNode = NonNullable<ResearchRunDetailView['decision_graph']>['nodes'][number]
export const graphNodeKinds: Record<string, string> = {
  trigger: '研究触发', evidence: '来源证据', causal_claim: '因果解释', action: '综合判断',
  coverage: '证据覆盖', review: '审查记录', horizon: '时间窗口', gate: '执行质量门', stop: '研究状态与限制',
}
export const graphNodeStatuses: Record<string, string> = {
  confirmed: '已记录', supporting: '支持', opposing: '反向约束', derived: '推导',
  unresolved: '待核实', blocked: '受限', scheduled: '已计划',
}
const requirementNames: Record<string, string> = {
  event_identity: '事件与公告', policy_or_data_delta: '政策或数据变化', expectation_pricing: '预期与定价',
  macro_transmission: '宏观传导', market_transmission: '宏观传导', cross_asset_confirmation: '跨资产确认',
  crypto_spot_confirmation: '现货确认', derivatives_crowding: '衍生品与拥挤度',
  market_regime_context: '日线趋势背景', market_narrative: '公开信息与市场叙事',
}
const directions: Record<string, string> = { long: '偏多', short: '偏空', bullish: '偏多', bearish: '偏空', neutral: '中性观察', no_trade: '暂不参与' }
const reviewResults: Record<string, string> = { accepted: '接受', bounded: '保留限制', requires_new_evidence: '需要补充新证据', rejected: '未接受' }
type GraphReview = ResearchRunDetailView['reviews'][number]
export type GraphReviewPresentation = { readonly summary: string; readonly originalReview: GraphReview; readonly originalNode: GraphNode }
export type GraphReviewPresentations = ReadonlyMap<string, GraphReviewPresentation>

function boundedChineseReview(challenge: string) {
  const text = researchDisplayText(challenge.replace(/\s+/g, ' ').trim())
  let excerpt = '', length = 0
  for (const character of text) {
    if (length === 160) return `${excerpt}…（完整意见见原始复核记录）`
    excerpt += character; length++
  }
  return excerpt
}
function sameNodeRecord(left: GraphNode, right: GraphNode) {
  return left.node_id === right.node_id && left.node_type === right.node_type && left.label === right.label
    && left.summary === right.summary && left.status === right.status
    && left.evidence_refs.length === right.evidence_refs.length
    && left.evidence_refs.every((ref, index) => ref === right.evidence_refs[index])
}
export function graphReviewPresentation(node: GraphNode, reviews?: GraphReviewPresentations): GraphReviewPresentation | undefined {
  const matched = reviews?.get(node.node_id)
  return matched && sameNodeRecord(node, matched.originalNode) ? matched : undefined
}
// A display association only: no policy, source or execution authorization is inferred.
export function graphReviewPresentations(nodes: readonly GraphNode[], reviews: readonly GraphReview[]): GraphReviewPresentations {
  const presentations = new Map<string, GraphReviewPresentation>()
  reviews.forEach((review, index) => {
    const nodeId = `review-${index + 1}`
    const candidates = nodes.filter(node => node.node_id === nodeId)
    if (candidates.length !== 1) return
    const node = candidates[0]
    const fullSummary = `结论=${review.resolution}；挑战=${review.challenge}${review.limitations.length ? `；限制=${review.limitations.join('；')}` : ''}`
    let length = 0
    for (const _character of fullSummary) { if (++length > 2000) return }
    const refs = [...new Set(review.evidence_refs.filter(ref => Boolean(ref)))]
    if (node.node_type !== 'review' || node.label !== `独立复核 ${index + 1}` || node.summary !== fullSummary
      || node.status !== (review.resolution === 'accepted' ? 'confirmed' : 'unresolved')
      || !Object.hasOwn(reviewResults, review.resolution) || node.evidence_refs.length !== refs.length
      || !node.evidence_refs.every((ref, refIndex) => ref === refs[refIndex])) return
    const challenge = /[\u3400-\u9fff]/.test(review.challenge)
      ? boundedChineseReview(review.challenge)
      : /[a-zA-Z]{3}/.test(review.challenge) ? '复核意见以英文保存，尚未提供这段意见的中文摘要。'
        : '复核意见原文已保存，尚无这段意见的中文摘要。'
    const known = review.limitations.flatMap(value => Object.hasOwn(reviewRequirementNames, value) ? [reviewRequirementNames[value]] : [])
    const unknownCount = review.limitations.length - known.length
    const limits = `${known.length ? `限制类别：${known.join('、')}。` : ''}${unknownCount ? `另 ${unknownCount} 项限制的分类待核对，内容见原始复核记录。` : ''}${review.limitations.length ? '' : '未记录限制项。'}`
    presentations.set(nodeId, {
      summary: `复核结论：${reviewResults[review.resolution]}。${challenge}${limits}`,
      originalReview: { ...review, evidence_refs: [...review.evidence_refs], limitations: [...review.limitations] },
      originalNode: { ...node, evidence_refs: [...node.evidence_refs] },
    })
  })
  return presentations
}

function evidenceFields(summary: string) {
  const match = summary.match(/^来源=([^；]+)；要求=([^；]+)；类型=([^；]+)；权威=([^；]+)；([\s\S]*)$/)
  return match ? { source: match[1], requirement: match[2], kind: match[3], content: match[5] } : null
}

// Short titles quote the saved statement; they do not generate a new conclusion.
export function graphNodeTitle(node: GraphNode): string {
  if (node.node_type === 'stop') return '研究状态与限制'
  if (node.node_type === 'review') {
    const sequence = /^review-([1-9]\d*)$/.exec(node.node_id)?.[1]
    return sequence ? `审查记录 ${sequence}` : '审查记录'
  }
  if (node.node_type === 'evidence') {
    const fields = evidenceFields(node.summary)
    const source = fields?.source ?? node.label
    const sourceName = sourceNames[source] ?? marketSourceNames[source] ?? source
    return fields && requirementNames[fields.requirement] ? `${requirementNames[fields.requirement]} · ${sourceName}` : sourceName
  }
  if (node.node_type === 'horizon') return researchDisplayText(node.label)
  if (node.node_type === 'causal_claim' && /[\u3400-\u9fff]/.test(node.summary)) {
    const firstClause = researchDisplayText(node.summary).split(/[：:，,。；;\n]/)[0].trim()
    const words = Array.from(firstClause)
    const excerpt = words.length > 20 ? `${words.slice(0, 20).join('')}…` : firstClause
    return excerpt ? `${node.label} · ${excerpt}` : node.label
  }
  return researchDisplayText(node.label)
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function numberText(value: unknown): string | null {
  if (typeof value !== 'number' && (typeof value !== 'string' || !/^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(value))) return null
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  if (number === 0 && typeof value === 'string' && /[1-9]/.test(value.split(/e/i)[0])) return value
  const notation = number !== 0 && (Math.abs(number) < 1e-6 || Math.abs(number) >= 1e10) ? 'scientific' : 'standard'
  return new Intl.NumberFormat('zh-CN', { maximumSignificantDigits: 8, useGrouping: false, notation }).format(number)
}
function savedMarketSummary(content: string): string | null {
  let payload: Record<string, unknown> | null
  try { payload = record(JSON.parse(content)) } catch { return null }
  if (!payload) return null
  const values = record(payload.values) ?? record(payload.summary) ?? payload
  const fields = [['spot_price', '现货价格'], ['level', '指标值'], ['period_close', '期间收盘'], ['funding_rate', '资金费率'], ['basis', '基差'], ['book_imbalance', '买卖盘失衡度']] as const
  const metrics = fields.flatMap(([key, name]) => {
    const value = numberText(values[key])
    return value === null ? [] : [`${name} ${value}`]
  }).slice(0, 2)
  if (metrics.length === 0) return null
  const instrument = typeof payload.symbol === 'string' ? payload.symbol : typeof payload.instrument === 'string' ? payload.instrument : ''
  return `${instrument ? `${researchDisplayText(instrument)}：` : ''}${metrics.join('；')}。数值已简化显示，完整精度、单位与时间见证据。`
}

export function graphNodeSummary(node: GraphNode, reviews?: GraphReviewPresentations): string {
  if (node.node_type === 'evidence') {
    const fields = evidenceFields(node.summary)
    const measured = fields?.kind === 'market' ? savedMarketSummary(fields.content) : null
    if (measured) return measured
    const category = fields?.kind === 'market' ? '市场数据' : fields?.kind === 'official' ? '官方材料' : fields?.kind === 'web' ? '网页材料' : '来源材料'
    return `已保存${category}，可打开 ${node.evidence_refs.length} 条关联证据核对内容、来源与时间。关联不代表已采纳。`
  }
  if (node.node_type === 'coverage') {
    const match = node.summary.match(/^状态=(\w+)；硬覆盖=([^；]+)；软覆盖=([^；]+)$/)
    const states: Record<string, string> = { sufficient: '满足当前证据门槛', partial: '部分覆盖', insufficient: '覆盖不足' }
    if (match) return states[match[1]] ? `${states[match[1]]}；必须要求 ${match[2]}，补充要求 ${match[3]}。` : '已保存覆盖评估，状态需核对；完整数值见原始节点记录。'
  }
  if (node.node_type === 'review') {
    const matched = graphReviewPresentation(node, reviews)
    return matched ? matched.summary
      : '已保存复核记录，原始意见与限制尚未匹配；请展开原始节点记录核对。'
  }
  if (!/[\u3400-\u9fff]/.test(node.summary)) return '已保存原始说明，尚无中文摘要；请打开节点详情核对。'
  const summary = node.summary
    .replace(/^中文结论\s+revision\s+\d+:\s*/i, '')
    .replace(/^方向=(\w+)/, (_, direction: string) => `方向：${directions[direction] ?? '原始方向待核对'}`)
    .replace(/；触发=/g, '；触发条件：').replace(/；失效=/g, '；失效条件：')
  const marketTerms: Record<string, string> = { mark: '标记价格', index: '指数价格', close: '收盘价' }
  return researchDisplayText(summary).replace(/\b(?:long|short|bullish|bearish)\b/g, term => directions[term]).replace(/\b(?:mark|index|close)\b/g, term => marketTerms[term])
}

export function graphNodeMatches(node: GraphNode, query: string, kind: string): boolean {
  const text = query.trim().toLocaleLowerCase()
  return (kind === 'all' || node.node_type === kind) && (!text || [
    node.label, node.summary, node.status, node.node_type, node.node_id,
    researchDisplayText(node.summary), graphNodeTitle(node), graphNodeSummary(node), graphNodeKinds[node.node_type], graphNodeStatuses[node.status],
  ].join(' ').toLocaleLowerCase().includes(text))
}
