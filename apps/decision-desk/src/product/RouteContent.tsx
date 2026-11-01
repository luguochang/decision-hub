import { Component, Suspense, type ReactNode } from 'react'

class RouteLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    return this.state.failed ? <div className="content-wrap"><div className="product-notice danger" role="alert"><div><strong>页面未能载入</strong><p>请重新载入，或通过左侧导航打开其他页面。已保存的研究记录不受影响。</p><button className="button secondary" onClick={() => window.location.reload()}>重新载入页面</button></div></div></div> : this.props.children
  }
}

export function RouteContent({ scene, children }: { scene: string; children: ReactNode }) {
  return <RouteLoadBoundary key={['report', 'report-incomplete', 'technical', 'evidence', 'graph', 'graph-node'].includes(scene) ? 'research-report' : scene}><Suspense fallback={<div className="content-wrap" role="status" aria-live="polite"><p>正在载入页面…</p></div>}>{children}</Suspense></RouteLoadBoundary>
}
