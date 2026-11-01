import { ArrowLeft, ArrowRight, RefreshCw } from 'lucide-react'

export type AssetNavigate = (scene: string, params?: Record<string, string | undefined>) => void
export const methodName = (family: string) => family === 'daily_rule' ? '日线规则' : 'AI 综合判断'
export const horizonName = (horizon: string) => ({ '30m': '30 分钟', '24h': '24 小时', '72h': '72 小时', '7d': '7 天' }[horizon] ?? horizon)
export const directionName = (direction: string) => ({ long: '偏多', short: '偏空', neutral: '中性观察', no_trade: '未形成方向' }[direction] ?? '待确认')
export const assetTime = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录'
export function instrumentName(value: string, metricFamily?: string) {
  const normalized = value.trim().toUpperCase().replaceAll('_', '-').replaceAll('/', '-')
  if (normalized === 'UNKNOWN') return '品种待确认'
  if (normalized === 'BTC-USDT-SWAP') return 'BTC 永续'
  if (normalized === 'BTC-USDT' || normalized === 'BTCUSDT') {
    if (metricFamily === 'crypto.derivatives') return 'BTC 衍生品（USDT）'
    if (metricFamily === 'crypto.spot' || metricFamily === 'crypto.spot.history') return 'BTC 现货（USDT）'
    return 'BTC / USDT'
  }
  return ({ DFII10: '美国10年期实际收益率（FRED）', T10YIE: '美国10年期市场通胀补偿（FRED）', WRESBAL: '美国银行准备金余额（FRED）', DGS10: '美国十年期国债收益率（FRED）', DGS2: '美国两年期国债收益率（FRED）', DTWEXBGS: '广义贸易加权美元指数', VIX: '波动率指数', DXY: '美元指数', US10Y: '美国十年期国债收益率', SPX: '标普指数' }[normalized] ?? value)
}
export const coverageName = (value: string) => ({ sufficient: '必需材料已覆盖', insufficient: '材料不足', conflicted: '材料有冲突', partial: '部分材料', degraded: '研究受限' }[value] ?? '覆盖待确认')
export const observationName = (status: string) => ({ pending: '等待到期', running: '核对行情中', retry: '等待补采', completed: '已观察', excluded: '不纳入前瞻' }[status] ?? '待确认')
export const qualityName = (quality: string | null) => quality === 'estimated' ? '行情估算' : quality === 'observed' ? '实测行情' : '尚无标签'
export const percent = (value: number | null) => value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
export function routeChange(params: URLSearchParams, changes: Record<string, string | undefined>) {
  const values: Record<string, string | undefined> = Object.fromEntries(params.entries())
  return { ...values, ...changes }
}
export function AssetPagination({ total, offset, limit, onChange }: { total: number; offset: number; limit: number; onChange: (offset: number) => void }) {
  return <div className="asset-pagination"><span>{total ? `共 ${total.toLocaleString()} 项 · ${offset + 1}–${Math.min(total, offset + limit)}` : '本筛选暂无记录'}</span><div><button className="button secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}><ArrowLeft size={14} />上一页</button><button className="button secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>下一页<ArrowRight size={14} /></button></div></div>
}
export function AssetError({ retry }: { retry: () => void }) {
  return <div className="product-notice danger" role="alert"><div><strong>这部分记录暂时无法读取</strong><p>已保存的数据仍保留，请重新读取或查看系统状态。</p><button className="button secondary" onClick={retry}><RefreshCw size={14} />重新读取</button></div></div>
}
export function readOffset(params: URLSearchParams, key = 'offset') {
  const raw = Number(params.get(key) ?? '0')
  return Number.isSafeInteger(raw) && raw >= 0 ? raw : 0
}

const backLabels: Record<string, string> = { settings: '返回建议与模拟', home: '返回今日研究', market: '返回市场与事件', research: '返回研究', library: '返回资料', 'calendar-all': '返回全部事件', 'calendar-upcoming': '返回即将发生的事件', 'calendar-history': '返回历史发布', management: '返回管理与审计', Operations: '返回运行审计', Evolution: '返回演进与审核', Inbox: '返回业务账本', delivery: '返回报告收件箱', runs: '返回任务记录', results: '返回研究效果', studies: '返回研究主题', report: '返回研究报告', sources: '返回来源记录', facts: '返回指标与依据', dsh: '返回 DSH 技术轨迹', 'dsh-conversation': '返回 DSH 对话摘要', 'dsh-research': '返回 DSH 主动研究', 'dsh-report': '返回 DSH 原生报告' }
export function assetBackLabel(back: string | null) {
  return back?.startsWith('#/') ? backLabels[back.slice(2).split('?')[0]] ?? null : null
}
export function AssetBackLink({ back }: { back: string | null }) {
  if (!back?.startsWith('#/')) return null
  const label = assetBackLabel(back)
  return label ? <a className="product-link asset-back-link" href={back}><ArrowLeft size={14} />{label}</a> : null
}
