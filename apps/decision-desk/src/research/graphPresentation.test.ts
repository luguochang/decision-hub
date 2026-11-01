import { describe, expect, it } from 'vitest'
import { graphNodeMatches, graphNodeSummary, graphNodeTitle, graphReviewPresentations, graphReviewPresentation } from './graphPresentation'
import type { ResearchRunDetailView } from '../api/types'

const node: Parameters<typeof graphNodeMatches>[0] = { node_id: 'gate-1', node_type: 'gate', label: '执行质量门', summary: 'SMA20 is required', status: 'blocked', evidence_refs: [] }
const savedReview: ResearchRunDetailView['reviews'][number] = {
  review_id: 'original-review-id', role: 'independent_reviewer', target_claim: 'Original target', evidence_refs: ['ev-2', 'ev-1'],
  result_hash: '3'.repeat(64), challenge: 'The causal strength should remain limited.', resolution: 'bounded',
  limitations: ['market_narrative', 'unregistered-limitation'], created_at: '2026-08-29T12:00:00Z',
}
function reviewNode(review = savedReview, ordinal = 1): typeof node {
  return { node_id: `review-${ordinal}`, node_type: 'review', label: `独立复核 ${ordinal}`, status: review.resolution === 'accepted' ? 'confirmed' : 'unresolved',
    summary: `结论=${review.resolution}；挑战=${review.challenge}${review.limitations.length ? `；限制=${review.limitations.join('；')}` : ''}`, evidence_refs: [...new Set(review.evidence_refs.filter(Boolean))] }
}
describe('graph display search', () => {
  it('does not treat the Chinese limitation wrapper as a Chinese review challenge', () => {
    const challenge = 'The evidence remains bounded and does not authorize execution.'
    const review = { ...node, node_type: 'review' as const, label: '独立复核 1', summary: `结论=bounded；挑战=${challenge}；限制=market_narrative；market_regime_context` }
    const before = JSON.stringify(review)
    expect(graphNodeSummary(review)).not.toContain(challenge)
    expect(graphNodeSummary(review)).not.toContain('market_narrative')
    expect(graphNodeMatches(review, challenge, 'review')).toBe(true)
    expect(graphNodeMatches(review, 'market_regime_context', 'review')).toBe(true)
    expect(JSON.stringify(review)).toBe(before)
  })
  it('matches displayed Chinese status, translated text, raw identifiers and type with the same filter', () => {
    for (const query of ['受限', '20 日均价', 'blocked', 'GATE-1', '执行质量门']) expect(graphNodeMatches(node, query, 'gate')).toBe(true)
    expect(graphNodeMatches(node, '受限', 'evidence')).toBe(false)
    expect(graphNodeMatches(node, '未出现的字段', 'all')).toBe(false)
    expect(graphNodeMatches(node, ' ', 'all')).toBe(true)
  })
  it('shows saved market measurements and makes the readable source title searchable without changing the record', () => {
    const evidence = { ...node, node_type: 'evidence' as const, label: 'huobi-public', evidence_refs: ['ev-1'], summary: '来源=huobi-public；要求=crypto_spot_confirmation；类型=market；权威=exchange；{"symbol":"BTC/USDT","values":{"spot_price":"83464.0","spot_volume":"7069.508576715854"}}' }
    const before = JSON.stringify(evidence)
    expect(graphNodeTitle(evidence)).toBe('现货确认 · Huobi')
    expect(graphNodeSummary(evidence)).toContain('BTC/USDT：现货价格 83464')
    expect(graphNodeSummary(evidence)).toContain('完整精度、单位与时间见证据')
    for (const query of ['现货确认', 'Huobi', '83464', 'spot_volume', 'crypto_spot_confirmation']) expect(graphNodeMatches(evidence, query, 'all')).toBe(true)
    expect(JSON.stringify(evidence)).toBe(before)
  })
  it.each(['null', '{"values":{"spot_price":null}}', '{"values":{"spot_price":"not-a-number"}}', '{"values":'])('does not fabricate a measured value from %s', content => {
    const evidence = { ...node, node_type: 'evidence' as const, evidence_refs: ['ev-1'], summary: `来源=unknown；要求=new_requirement；类型=market；权威=unverified；${content}` }
    expect(graphNodeSummary(evidence)).toBe('已保存市场数据，可打开 1 条关联证据核对内容、来源与时间。关联不代表已采纳。')
    expect(graphNodeTitle(evidence)).toBe('unknown')
  })
  it('keeps a tiny nonzero measurement visible instead of rounding it to zero or asserting percent units', () => {
    const evidence = { ...node, node_type: 'evidence' as const, summary: '来源=htx-derivatives-public；要求=derivatives_crowding；类型=market；权威=exchange；{"instrument":"BTC-USDT","values":{"funding_rate":"0.000075189954042933","basis":"-0.0003794061990944938"}}' }
    expect(graphNodeSummary(evidence)).toContain('资金费率 0.000075189954')
    expect(graphNodeSummary(evidence)).toContain('基差 -0.0003794062')
    expect(graphNodeSummary(evidence)).not.toContain('%')
  })
  it.each([['0.00000000000123', '1.23E-12'], ['1e-400', '1e-400']])('retains a very small nonzero source value %s', (value, expected) => {
    const evidence = { ...node, node_type: 'evidence' as const, summary: `来源=source；要求=derivatives_crowding；类型=market；权威=exchange；{"values":{"funding_rate":"${value}"}}` }
    expect(graphNodeSummary(evidence)).toContain(`资金费率 ${expected}`)
  })
  it('quotes an excerpt for a numbered claim and preserves the complete original text in search', () => {
    const claim = { ...node, node_type: 'causal_claim' as const, label: '反方 1', summary: '宏观风险偏紧可能压低 BTC 估值，使均线偏多变成滞后信号。' }
    const title = graphNodeTitle(claim)
    expect(title).toBe('反方 1 · 宏观风险偏紧可能压低 BTC 估值')
    expect(graphNodeMatches(claim, '滞后信号', 'causal_claim')).toBe(true)
  })
  it('uses explicit saved coverage values without claiming every requirement is covered', () => {
    const coverage = { ...node, node_type: 'coverage' as const, summary: '状态=sufficient；硬覆盖=100%；软覆盖=75%' }
    expect(graphNodeSummary(coverage)).toBe('满足当前证据门槛；必须要求 100%，补充要求 75%。')
    expect(graphNodeSummary({ ...coverage, summary: '状态=future_status；硬覆盖=100%；软覆盖=75%' })).toContain('状态需核对')
  })
  it('shows saved review disposition while identifying untranslated free text', () => {
    const review = { ...savedReview, limitations: [] }, originalNode = reviewNode(review)
    expect(graphNodeSummary(originalNode, graphReviewPresentations([originalNode], [review]))).toBe('复核结论：保留限制。复核意见以英文保存，尚未提供这段意见的中文摘要。未记录限制项。')
    expect(graphNodeMatches(originalNode, 'causal strength', 'review')).toBe(true)
    const chinese = { ...review, challenge: '盘口证据只有单一时点。' }, chineseNode = reviewNode(chinese)
    expect(graphNodeSummary(chineseNode, graphReviewPresentations([chineseNode], [chinese]))).toBe('复核结论：保留限制。盘口证据只有单一时点。未记录限制项。')
  })
  it.each([['30m', 'neutral', '30 分钟', '中性观察'], ['24h', 'no_trade', '24 小时', '暂不参与'], ['72h', 'long', '72 小时', '偏多']])('shows %s horizon and %s direction as Chinese labels', (label, direction, expectedTitle, expectedDirection) => {
    const horizon = { ...node, node_type: 'horizon' as const, label, summary: `方向=${direction}；触发=继续核验。；失效=条件不再成立。` }
    expect(graphNodeTitle(horizon)).toBe(expectedTitle)
    expect(graphNodeSummary(horizon)).toBe(`方向：${expectedDirection}；触发条件：继续核验。；失效条件：条件不再成立。`)
  })
  it('removes presentation revision metadata and translates explicit price fields while preserving the saved record', () => {
    const summary = { ...node, node_type: 'action' as const, summary: '中文结论 revision 1: BTC close 高于 SMA20，但 mark 均低于 index，long 仅为候选。' }
    expect(graphNodeSummary(summary)).toBe('BTC 收盘价 高于 20 日均价，但 标记价格 均低于 指数价格，偏多 仅为候选。')
    expect(summary.summary).toContain('revision 1')
    expect(graphNodeMatches(summary, 'revision 1', 'all')).toBe(true)
  })
})

describe('graph review binding', () => {
  it('uses the complete ordinal record and original ref order while preserving raw search and audit fields', () => {
    const review = { ...savedReview, evidence_refs: ['ev-2', 'ev-1', 'ev-2', ''] }, originalNode = reviewNode(review)
    const original = JSON.stringify({ review, originalNode })
    const map = graphReviewPresentations([originalNode], [review]), binding = graphReviewPresentation(originalNode, map)!
    expect(binding.summary).toContain('复核意见以英文保存')
    expect(binding.summary).toContain('限制类别：最新公开信息')
    expect(binding.summary).toContain('另 1 项限制')
    expect(binding.summary).not.toContain('market_narrative')
    expect(binding.originalReview).toEqual(review)
    expect(binding.originalNode).toEqual(originalNode)
    expect(graphNodeMatches(originalNode, 'unregistered-limitation', 'review')).toBe(true)
    expect(JSON.stringify({ review, originalNode })).toBe(original)
    originalNode.summary += ' drift'
    expect(graphReviewPresentation(originalNode, map)).toBeUndefined()
    expect(graphNodeSummary(originalNode, map)).toContain('尚未匹配')
    expect(binding.originalNode.summary).not.toContain(' drift')
  })
  it.each(['type', 'ordinal', 'title', 'summary', 'refs-order', 'refs-count', 'status', 'duplicate-id'] as const)('does not bind mismatched %s', mismatch => {
    const originalNode = reviewNode(), changed = { ...originalNode, evidence_refs: [...originalNode.evidence_refs] }
    if (mismatch === 'type') changed.node_type = 'gate'
    if (mismatch === 'ordinal') changed.node_id = 'review-2'
    if (mismatch === 'title') changed.label = '独立复核 2'
    if (mismatch === 'summary') changed.summary += ' extra'
    if (mismatch === 'refs-order') changed.evidence_refs.reverse()
    if (mismatch === 'refs-count') changed.evidence_refs.pop()
    if (mismatch === 'status') changed.status = 'confirmed'
    const map = graphReviewPresentations(mismatch === 'duplicate-id' ? [changed, { ...changed }] : [changed], [savedReview])
    expect(map.size).toBe(0)
    expect(graphReviewPresentation(changed, map)).toBeUndefined()
    if (changed.node_type === 'review') expect(graphNodeSummary(changed, map)).toContain('尚未匹配')
  })
  it('counts producer Unicode codepoints and rejects a truncated prefix instead of trusting it', () => {
    const prefix = '结论=bounded；挑战=', challenge = '😀'.repeat(2000 - Array.from(prefix).length)
    const review = { ...savedReview, challenge, limitations: [] }, originalNode = reviewNode(review)
    expect(originalNode.summary.length).toBeGreaterThan(2000)
    expect(graphReviewPresentations([originalNode], [review]).size).toBe(1)
    const longer = { ...review, challenge: `${challenge}😀` }, tooLong = reviewNode(longer)
    expect(graphReviewPresentations([tooLong], [longer]).size).toBe(0)
    expect(graphReviewPresentations([{ ...tooLong, summary: Array.from(tooLong.summary).slice(0, 2000).join('') }], [longer]).size).toBe(0)
  })
  it('does not split delimiters from a challenge and checks language before known-term substitution', () => {
    const review = { ...savedReview, challenge: 'SMA20 funding basis ; original prose', limitations: ['market_regime_context'] }, originalNode = reviewNode(review)
    const summary = graphNodeSummary(originalNode, graphReviewPresentations([originalNode], [review]))
    expect(summary).toContain('复核意见以英文保存')
    expect(summary).not.toContain('20 日均价')
    expect(summary).toContain('日线趋势背景')
    const literal = { ...review, challenge: '核对原材料；限制=属于意见正文' }, literalNode = reviewNode(literal)
    expect(graphNodeSummary(literalNode, graphReviewPresentations([literalNode], [literal]))).toContain(literal.challenge)
  })
  it('bounds a saved Chinese challenge and preserves its complete typed record', () => {
    const review = { ...savedReview, resolution: 'requires_new_evidence' as const, challenge: '需要复验原证据。'.repeat(40), limitations: ['unknown-category', 'constructor', '__proto__'] }, originalNode = reviewNode(review)
    const binding = graphReviewPresentation(originalNode, graphReviewPresentations([originalNode], [review]))!
    expect(binding.summary).toContain('复核结论：需要补充新证据')
    expect(binding.summary).toContain('…（完整意见见原始复核记录）')
    expect(binding.summary).not.toContain('unknown-category')
    expect(binding.summary).not.toContain('constructor')
    expect(binding.summary).not.toContain('[object Object]')
    expect(binding.summary).toContain('另 3 项限制')
    expect(binding.originalReview.challenge).toBe(review.challenge)
  })
})
