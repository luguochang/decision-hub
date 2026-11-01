import { graphNodeKinds, graphNodeStatuses, graphNodeSummary, graphNodeTitle } from './graphPresentation'
import type { GraphReviewPresentations } from './graphPresentation'
import { useEffect, useState } from 'react'
import type { MouseEvent as ReactMouseEvent } from 'react'
import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node, type ReactFlowInstance } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './DecisionGraphCanvas.css'
import { layoutDecisionGraph } from './decisionGraphLayout'
import type { ResearchRunDetailView } from '../api/types'

const graphNodeTone: Record<string, string> = {
  confirmed: 'confirmed', supporting: 'supporting', opposing: 'opposing', derived: 'derived',
  unresolved: 'unresolved', blocked: 'blocked', scheduled: 'scheduled',
}
type GraphNode = NonNullable<ResearchRunDetailView['decision_graph']>['nodes'][number]
function GraphNodeContent({ node, reviewPresentations }: { node: GraphNode; reviewPresentations?: GraphReviewPresentations }) {
  return <button type="button" className="decision-graph-node-content" aria-label={`查看${graphNodeTitle(node)}详情`}>
    <div className="decision-graph-node-meta"><span>{graphNodeKinds[node.node_type] ?? '其他节点'}</span><span>{graphNodeStatuses[node.status] ?? '状态待核对'}</span></div>
    <strong title={node.label}>{graphNodeTitle(node)}</strong>
    <p>{graphNodeSummary(node, reviewPresentations)}</p>
    <small>{node.evidence_refs.length ? `${node.evidence_refs.length} 条关联证据` : '无直接关联证据'} · 点击查看详情</small>
  </button>
}

export default function DecisionGraphCanvas({ graph, nodesEdges, expanded = false, onNodeSelect, reviewPresentations }: { graph: NonNullable<ResearchRunDetailView['decision_graph']>; nodesEdges: Edge[]; expanded?: boolean; onNodeSelect?: (node: Node) => void; reviewPresentations?: GraphReviewPresentations }) {
  const [nodes, setNodes] = useState<Node[]>([])
  const [layouting, setLayouting] = useState(true)
  const [layoutFailed, setLayoutFailed] = useState(false)
  const [flow, setFlow] = useState<ReactFlowInstance | null>(null)
  const [zoom, setZoom] = useState(1)
  const nodeWidth = expanded ? 320 : 280
  const nodeHeight = expanded ? 164 : 156
  const fitAll = () => { void flow?.fitView({ padding: 0.12, minZoom: 0.08, maxZoom: 1, duration: 250 }) }
  const readNodes = () => {
    const focus = nodes.find(node => node.id === 'thesis') ?? nodes.find(node => graph.nodes.some(item => item.node_id === node.id && item.node_type === 'action')) ?? nodes[0]
    if (focus) void flow?.setCenter(focus.position.x + nodeWidth / 2, focus.position.y + nodeHeight / 2, { zoom: 1, duration: 250 })
  }
  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    setLayouting(true)
    setLayoutFailed(false)
    const layout = async () => {
      try {
        const result = await layoutDecisionGraph({
          id: 'decision-graph',
          layoutOptions: {
            'elk.algorithm': 'layered',
            'elk.direction': 'RIGHT',
            'elk.edgeRouting': 'ORTHOGONAL',
            'elk.layered.spacing.nodeNodeBetweenLayers': expanded ? '100' : '70',
            'elk.spacing.nodeNode': expanded ? '48' : '28',
            'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
          },
          children: graph.nodes.map((item) => ({ id: item.node_id, width: nodeWidth, height: nodeHeight })),
          edges: nodesEdges.map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
        }, controller.signal)
        if (cancelled) return
        const positions = new Map((result.children ?? []).map((child) => [child.id, { x: child.x ?? 0, y: child.y ?? 0 }]))
        setNodes(graph.nodes.map((item) => ({
          id: item.node_id,
          position: positions.get(item.node_id) ?? { x: 0, y: 0 },
          data: { label: <GraphNodeContent node={item} reviewPresentations={reviewPresentations} /> },
          ariaLabel: `${graphNodeTitle(item)}，${graphNodeStatuses[item.status] ?? '状态待核对'}。可通过查看详情按钮打开原始记录。`,
          className: `decision-graph-node ${graphNodeTone[item.status] ?? 'derived'}`,
          draggable: false,
          style: { width: nodeWidth, height: nodeHeight, padding: 0, overflow: 'hidden' },
        })))
      } catch {
        if (cancelled) return
        setLayoutFailed(true)
        // A renderer failure must not hide the graph; use a simple stable
        // fallback while keeping the same JSON graph contract.
        setNodes(graph.nodes.map((item, index) => ({
          id: item.node_id,
          position: { x: (index % 4) * (nodeWidth + 70), y: Math.floor(index / 4) * (nodeHeight + 45) },
          data: { label: <GraphNodeContent node={item} reviewPresentations={reviewPresentations} /> },
          ariaLabel: `${graphNodeTitle(item)}，${graphNodeStatuses[item.status] ?? '状态待核对'}。可通过查看详情按钮打开原始记录。`,
          className: `decision-graph-node ${graphNodeTone[item.status] ?? 'derived'}`,
          draggable: false,
          style: { width: nodeWidth, height: nodeHeight, padding: 0, overflow: 'hidden' },
        })))
      } finally {
        if (!cancelled) setLayouting(false)
      }
    }
    void layout()
    return () => { cancelled = true; controller.abort() }
  }, [graph, nodesEdgeKey(nodesEdges), expanded, nodeHeight, nodeWidth, reviewPresentations])

  const scrollToEvidence = (_event: ReactMouseEvent, node: Node) => {
    onNodeSelect?.(node)

  }
  return <div className="decision-graph-flow-wrap" aria-busy={layouting}><div className="decision-graph-reading-controls" aria-label="图谱阅读方式"><button type="button" onClick={fitAll} disabled={layouting || !flow}>查看全貌</button><button type="button" onClick={readNodes} disabled={layouting || !flow || !nodes.length}>清晰阅读</button><output aria-label="图谱缩放比例">{Math.round(zoom * 100)}%</output><span>全貌看关系，清晰阅读看文字</span></div><ReactFlow ariaLabelConfig={{ 'controls.ariaLabel': '图谱视图操作', 'controls.zoomIn.ariaLabel': '放大图谱', 'controls.zoomOut.ariaLabel': '缩小图谱', 'controls.fitView.ariaLabel': '适应全部节点', 'node.a11yDescription.default': '按 Tab 定位查看详情按钮，按回车打开；点击节点也可打开详情。' }} nodes={nodes} edges={nodesEdges} onInit={setFlow} onMove={(_event, viewport) => setZoom(viewport.zoom)} onNodeClick={scrollToEvidence} fitView fitViewOptions={{ padding: expanded ? 0.18 : 0.1, maxZoom: expanded ? 1.15 : 1 }} minZoom={0.08} maxZoom={2.5} panOnScroll zoomOnScroll zoomOnPinch nodesConnectable={false} nodesDraggable={false} elementsSelectable proOptions={{ hideAttribution: true }}>
    {nodes.length > 8 && <MiniMap pannable zoomable nodeColor={(node) => {
      const className = String(node.className ?? '')
      if (className.includes('opposing')) return '#fb7185'
      if (className.includes('supporting')) return '#4ade80'
      if (className.includes('blocked') || className.includes('unresolved')) return '#f59e0b'
      if (className.includes('confirmed')) return '#38bdf8'
      return '#94a3b8'
    }} />}
    <Controls showInteractive={false} />
    <Background gap={24} size={1} />
  </ReactFlow>{layouting && <span className="decision-graph-layout-status" role="status">正在按分支自动排版…</span>}{layoutFailed && <span className="decision-graph-layout-status" role="status">自动排版暂不可用，已用网格保留全部节点。</span>}</div>
}

function nodesEdgeKey(edges: Edge[]): string {
  return edges.map((edge) => `${edge.id}:${edge.source}>${edge.target}`).join('|')
}
