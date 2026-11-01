// @vitest-environment jsdom
import { act, useEffect } from 'react'
import type { ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Edge, Node, ReactFlowInstance } from '@xyflow/react'
import type { ResearchRunDetailView } from '../api/types'
import DecisionGraphCanvas from './DecisionGraphCanvas'
import { layoutDecisionGraph } from './decisionGraphLayout'
import { graphReviewPresentations } from './graphPresentation'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true
const flow = { fitView: vi.fn(), setCenter: vi.fn() } as unknown as ReactFlowInstance
vi.mock('./decisionGraphLayout', () => ({ layoutDecisionGraph: vi.fn() }))
vi.mock('@xyflow/react', () => ({
  ReactFlow: ({ nodes, edges, children, onInit, onMove, onNodeClick }: { nodes: Node[]; edges: Edge[]; children: ReactNode; onInit: (value: ReactFlowInstance) => void; onMove: (_event: null, viewport: { zoom: number }) => void; onNodeClick: (event: React.MouseEvent, node: Node) => void }) => {
    useEffect(() => { onInit(flow); onMove(null, { zoom: .35 }) }, [onInit])
    return <div data-edge-count={edges.length}>{nodes.map(node => <div key={node.id} data-node-id={node.id} onClick={event => onNodeClick(event, node)}>{node.data.label as ReactNode}</div>)}{children}</div>
  },
  Background: () => null, Controls: () => null, MiniMap: () => null,
}))

let container: HTMLDivElement | null = null
let root: Root | null = null
afterEach(async () => {
  if (root) await act(async () => root!.unmount())
  root = null
  container?.remove(); container = null
  vi.clearAllMocks()
})
const graph: NonNullable<ResearchRunDetailView['decision_graph']> = {
  schema_version: 'decision-graph.v1', graph_status: 'causal', generation_mode: 'causal_case', stop_reason: null,
  nodes: [
    { node_id: 'trigger', node_type: 'trigger', label: '研究触发', status: 'confirmed', summary: 'BTC 最新研究', evidence_refs: [] },
    { node_id: 'thesis', node_type: 'action', label: '综合判断', status: 'derived', summary: '价格规则偏多，执行未授权。', evidence_refs: ['ev-real-1'] },
  ], edges: [{ edge_id: 'link', from_node: 'trigger', to_node: 'thesis', relation: 'derived_from', label: '形成判断' }],
}
const edges: Edge[] = [{ id: 'link', source: 'trigger', target: 'thesis' }]
async function renderCanvas(onNodeSelect = vi.fn()) {
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
  await act(async () => root!.render(<DecisionGraphCanvas graph={graph} nodesEdges={edges} onNodeSelect={onNodeSelect} />))
  return onNodeSelect
}

describe('graph readable canvas', () => {
  it.each([false, true])('uses the same typed review presentation with layout fallback %s and retains native selection', async fallback => {
    const review: ResearchRunDetailView['reviews'][number] = { review_id: 'raw-review-id', role: 'independent_reviewer', target_claim: 'Original claim', evidence_refs: ['ev-real-1'], result_hash: '3'.repeat(64), challenge: 'The original English review remains bounded.', resolution: 'bounded', limitations: ['market_narrative', 'unregistered-limit'], created_at: '2026-08-29T12:00:00Z' }
    const rawNode = { node_id: 'review-1', node_type: 'review' as const, label: '独立复核 1', status: 'unresolved' as const, summary: `结论=bounded；挑战=${review.challenge}；限制=${review.limitations.join('；')}`, evidence_refs: [...review.evidence_refs] }
    const reviewGraph = { ...graph, nodes: [rawNode], edges: [] }, map = graphReviewPresentations(reviewGraph.nodes, [review]), select = vi.fn()
    if (fallback) vi.mocked(layoutDecisionGraph).mockRejectedValue(new Error('controlled layout failure'))
    else vi.mocked(layoutDecisionGraph).mockResolvedValue({ id: 'graph', children: [{ id: 'review-1', x: 0, y: 10 }] })
    container = document.createElement('div'); document.body.append(container); root = createRoot(container)
    await act(async () => root!.render(<DecisionGraphCanvas graph={reviewGraph} nodesEdges={[]} onNodeSelect={select} reviewPresentations={map} />))
    const summary = container.querySelector('.decision-graph-node-content p')!
    expect(summary.textContent).toBe(map.get('review-1')!.summary)
    expect(summary.textContent).not.toContain(review.challenge)
    expect(summary.textContent).not.toContain('unregistered-limit')
    const button = container.querySelector<HTMLButtonElement>('button[aria-label="查看独立复核 1详情"]')!
    await act(async () => button.click())
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: 'review-1' }))
    expect(rawNode.evidence_refs).toEqual(['ev-real-1'])
    await act(async () => root!.render(<DecisionGraphCanvas graph={reviewGraph} nodesEdges={[]} onNodeSelect={select} reviewPresentations={new Map()} />))
    expect(container.querySelector('.decision-graph-node-content p')?.textContent).toContain('尚未匹配')
  })
  it('keeps an unmatched review conservative without changing its native selection', async () => {
    const challenge = 'The original English review must remain available for audit.'
    const reviewGraph = { ...graph, nodes: [{ node_id: 'review-1', node_type: 'review' as const, label: '独立复核 1', status: 'unresolved' as const, summary: `结论=bounded；挑战=${challenge}；限制=market_narrative`, evidence_refs: ['ev-real-1'] }], edges: [] }
    vi.mocked(layoutDecisionGraph).mockResolvedValue({ id: 'graph', children: [{ id: 'review-1', x: 0, y: 10 }] })
    container = document.createElement('div'); document.body.append(container); root = createRoot(container)
    const select = vi.fn()
    await act(async () => root!.render(<DecisionGraphCanvas graph={reviewGraph} nodesEdges={[]} onNodeSelect={select} />))
    expect(container.querySelector('.decision-graph-node-content p')?.textContent).not.toContain(challenge)
    expect(container.querySelector('.decision-graph-node-content p')?.textContent).not.toContain('market_narrative')
    await act(async () => container!.querySelector<HTMLButtonElement>('button[aria-label="查看独立复核 1详情"]')!.click())
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: 'review-1' }))
  })
  it('retains every node, Chinese status and evidence count while allowing reading zoom and full overview', async () => {
    vi.mocked(layoutDecisionGraph).mockResolvedValue({ id: 'graph', children: [{ id: 'trigger', x: 0, y: 10 }, { id: 'thesis', x: 400, y: 80 }] })
    const onNodeSelect = await renderCanvas()
    expect(container!.querySelectorAll('[data-node-id]')).toHaveLength(2)
    expect(container!.textContent).toContain('已记录')
    expect(container!.textContent).toContain('推导')
    expect(container!.textContent).toContain('1 条关联证据')
    expect(container!.querySelector('output')!.textContent).toBe('35%')
    const button = [...container!.querySelectorAll('button')].find(button => button.textContent === '清晰阅读')!
    await act(async () => button.click())
    expect(flow.setCenter).toHaveBeenCalledWith(540, 158, { zoom: 1, duration: 250 })
    const overview = [...container!.querySelectorAll('button')].find(button => button.textContent === '查看全貌')!
    await act(async () => overview.click())
    expect(flow.fitView).toHaveBeenCalledWith(expect.objectContaining({ minZoom: .08, maxZoom: 1 }))
    await act(async () => (container!.querySelector('button[aria-label="查看综合判断详情"]') as HTMLButtonElement).click())
    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'thesis' }))
    expect(container!.querySelector('[data-edge-count]')!.getAttribute('data-edge-count')).toBe('1')
  })
  it('retains the same content, references and working selection when automatic layout fails', async () => {
    vi.mocked(layoutDecisionGraph).mockRejectedValue(new Error('worker unavailable'))
    const onNodeSelect = await renderCanvas()
    expect(container!.querySelectorAll('[data-node-id]')).toHaveLength(2)
    expect(container!.textContent).toContain('已用网格保留全部节点')
    expect(container!.textContent).toContain('1 条关联证据')
    await act(async () => (container!.querySelector('button[aria-label="查看综合判断详情"]') as HTMLButtonElement).click())
    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'thesis' }))
  })
})
