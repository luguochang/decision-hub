import type { GateStatus, RunView } from '../api/types'

export function ledgerRunStatus(runStatus?: RunView['status'], status?: GateStatus | null, errorCode?: string | null) {
  if (runStatus === 'failed') return { label: '执行失败', tone: 'danger' }
  if (runStatus === 'cancelled') return { label: '已取消', tone: 'neutral' }
  if (errorCode === 'duplicate_observation') return { label: '重复输入', tone: 'neutral' }
  if (runStatus === 'admitted') return { label: '等待执行', tone: 'neutral' }
  if (runStatus === 'running') return { label: '执行中', tone: 'neutral' }
  if (status) return {
    publish: { label: '已发布', tone: 'success' },
    degraded: { label: '降级发布', tone: 'warning' },
    research_only: { label: '仅研究', tone: 'neutral' },
    reject: { label: '发布未通过', tone: 'danger' },
  }[status]
  if (runStatus === 'degraded') return { label: '降级完成', tone: 'warning' }
  if (runStatus === 'completed') return { label: '执行完成', tone: 'neutral' }
  return { label: '状态未知', tone: 'neutral' }
}

export function LedgerRunStatus({ status, runStatus, errorCode }: { status: GateStatus | null; runStatus?: RunView['status']; errorCode?: string | null }) {
  const meta = ledgerRunStatus(runStatus, status, errorCode)
  return <span className={`status-pill ${meta.tone}`}><span className="status-dot" />{meta.label}</span>
}
