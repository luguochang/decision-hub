import type { ResearchRunView } from '../api/types'

export function coverageUnavailableText(run: ResearchRunView): string {
  if (run.coverage_basis === 'round_request_only') return '完整任务要求未保存'
  if (run.coverage_basis === 'invalid') return '原始要求无法核验'
  if (['failed', 'cancelled', 'rejected', 'completed', 'degraded', 'research_only'].includes(run.status)) return '覆盖率无法核验'
  return '等待证据评估'
}

export function coverageGapLabel(run: ResearchRunView): string {
  if (!run.coverage) return '缺口未核验'
  return `${run.coverage.gaps.length} 个缺口`
}
