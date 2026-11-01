import type { OperationsOverview } from '../api/types'

export const serviceNames: Record<string, string> = {
  'hub-api': '业务接口', 'hub-research-worker': '自动研究通道',
  'hub-research-priority-worker': '用户研究通道', 'hub-realtime-worker': '来源与事件调度',
  'hub-documents': '资料处理', 'hub-historical-worker': '行情更新', 'hub-evolution-worker': '经验演进',
}
export const sourceNames: Record<string, string> = {
  'bea-news': '美国经济分析局公告', 'bls-calendar': '美国劳工统计局日历',
  'bls-releases': '美国劳工统计局数据发布', 'boj-calendar': '日本央行日历',
  'ecb-calendar': '欧洲央行日历', 'fed-calendar': '美联储日历',
  'fed-press': '美联储新闻', 'fed-speeches': '美联储官员讲话',
  'fred.current_macro': 'FRED 宏观数据库（圣路易斯联储）',
}
export const marketSourceNames: Record<string, string> = {
  'okx-public': 'OKX 现货', 'okx-orderbook-public': 'OKX 盘口',
  'binance-public': '币安行情', 'api.binance.com': '币安现货接口',
  'huobi-public': 'Huobi', 'htx-derivatives-public': 'HTX 衍生品', 'htx-orderbook-public': 'HTX 盘口',
  'gateio-public': 'Gate.io', 'gateio-derivatives-public': 'Gate.io 衍生品', 'gateio-orderbook-public': 'Gate.io 盘口',
  'yahoo-finance-public': '雅虎财经', 'api.alternative.me': '恐惧与贪婪指数', 'federalreserve.gov': '美联储官网',
}
export function sourceLabel(value: string, fallback = '来源名称待核对') {
  return sourceNames[value] ?? marketSourceNames[value] ?? (/[\u3400-\u9fff]/.test(value) ? value : fallback)
}
export const modeNames: Record<string, string> = { provider: '供应商配置', replay: '离线回放', fake: '测试替身', local: '本地处理', dsh: 'DSH 研究', deterministic: '规则处理', model: '模型处理' }
export function operationStatus(status: string) {
  return ({ online: '在线', offline: '离线', stale: '心跳过期', healthy: '最近请求成功', degraded: '最近采集异常', enabled: '已启用', disabled: '未启用', shadow: '观察验证', audited: '已审核', registered: '已登记', passed: '通过', failed: '失败', rejected: '已拒绝', not_run: '尚未验证', unknown: '未确认', queued: '等待执行', running: '执行中', retry_wait: '等待重试', waiting_inputs: '等待评测材料', pending_owner_review: '待负责人审核', completed: '已完成', cancelled: '已取消' } as Record<string, string>)[status] ?? '状态待核对'
}

const researchServiceIds = ['hub-api', 'hub-research-worker', 'hub-research-priority-worker']
export function researchServiceHealth(services: OperationsOverview['services'] = []) {
  const online = researchServiceIds.filter(id => services.some(service => service.service_id === id && service.status === 'online')).length
  return { online, expected: researchServiceIds.length, complete: online === researchServiceIds.length }
}
