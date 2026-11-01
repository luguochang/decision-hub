export const executionScopeName = (scope?: string | null) => scope === 'directional_synthesis'
  ? '单次方向综合'
  : scope === 'research_harness' ? '完整研究流程' : '未标注适用范围'

export const executionScopeLimit = (scope?: string | null) => scope === 'research_harness'
  ? '需走完整研究的部署与确认流程，不能通过方向综合账本启用。'
  : scope !== 'directional_synthesis' ? '原记录保持不变；需登记范围明确的新候选，才能重新审核或回滚。' : null
