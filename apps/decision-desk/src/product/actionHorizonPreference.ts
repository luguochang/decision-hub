import type { ActionHorizon } from '../api/actionPlans'

const key = (owner: string, scope: string) => `action-view-horizon:${owner}:${scope}`
export const isActionHorizon = (value: unknown): value is ActionHorizon =>
  value === '30m' || value === '24h' || value === '72h'

export function readActionHorizon(owner: string, scope: string): { horizon: ActionHorizon; notice: string | null } {
  try {
    const value = sessionStorage.getItem(key(owner, scope))
    if (value === null) return { horizon: '24h', notice: null }
    if (isActionHorizon(value)) return { horizon: value, notice: null }
  } catch { /* Keep original request receipts and the invalid preference intact. */ }
  return { horizon: '24h', notice: '未能恢复上次观察窗口，请重新选择。已保存的模拟记录仍可在对应窗口查看。' }
}

export function saveActionHorizon(owner: string, scope: string, horizon: ActionHorizon): string | null {
  try {
    sessionStorage.setItem(key(owner, scope), horizon)
    return null
  } catch {
    return '本页已切换观察窗口，但未能保存选择；刷新后请重新选择相同窗口查看原记录。'
  }
}
