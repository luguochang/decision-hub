import { useQuery } from '@tanstack/react-query'
import { assets } from '../api/assets'
import { api } from '../api/client'
import { AssetError, assetTime } from './assetFormat'

const names: Record<string, string> = { api: '业务接口', research_worker: '研究执行', realtime_worker: '来源与事件调度', evolution_worker: '经验演进', data_worker: '历史数据更新', document_worker: '资料处理', hourly_market_worker: '小时行情更新' }
const serviceNames: Record<string, string> = { 'hub-api': '业务接口', 'hub-documents': '资料处理', 'hub-historical-worker': '小时行情更新', 'hub-research-priority-worker': '用户研究通道', 'hub-realtime-worker': '来源与事件调度', 'hub-research-worker': '自动研究通道', 'hub-evolution-worker': '经验演进' }
const modes: Record<string, string> = { provider: '在线供应商', replay: '离线回放', fake: '测试替身', dsh: 'DSH 在线研究', deterministic: '规则处理', model: '模型处理' }
export function RuntimePanel() {
  const config = useQuery({ queryKey: ['runtime-configurations'], queryFn: assets.runtimeConfigurations, retry: false, refetchInterval: 30_000 })
  const health = useQuery({ queryKey: ['operations'], queryFn: api.operations, retry: false, refetchInterval: 10_000 })
  return <section className="product-card"><div className="product-card-head"><h2>当前进程与实际配置</h2><button className="button secondary" onClick={() => { void config.refetch(); void health.refetch() }}>刷新运行配置</button></div><p>配置必须与心跳中的同一进程实例匹配。旧进程留下的配置不代表当前已经启动。</p>{config.isPending || health.isPending ? <p>匹配实际进程…</p> : config.isError || health.isError ? <AssetError retry={() => { void config.refetch(); void health.refetch() }} /> : <div className="study-grid">{health.data.services.map(service => {
    const active = config.data.find(c => c.service_id === service.service_id && c.instance_id === service.instance_id && c.started_at === service.started_at)
    const settings = active?.settings
    return <article className="study-card" key={service.service_id}><div className="study-card-meta"><strong>{serviceNames[service.service_id] ?? names[service.role] ?? '登记服务'}</strong><span>{service.status === 'online' ? '在线' : service.status === 'offline' ? '离线' : '状态需检查'}</span></div><p>最近心跳：{assetTime(service.heartbeat_at)}</p>{active ? <><p>本次启动：{assetTime(active.started_at)}</p><p>运行方式：{service.service_id === 'hub-realtime-worker' && settings?.legacy_analysis_enabled === false ? '来源采集与观察调度（旧兼容分析关闭）' : service.service_id === 'hub-historical-worker' ? '行情抓取与历史分区更新' : service.service_id === 'hub-documents' ? settings?.document_correction_mode === 'model' ? '模型整理资料' : '按规则整理资料' : typeof settings?.runtime_mode === 'string' ? modes[settings.runtime_mode] ?? '其他登记模式' : '未声明'}</p><p>来源采集：{settings?.sources_enabled === true ? '已启用' : settings?.sources_enabled === false ? '未启用' : '未声明'} · 文档处理：{typeof settings?.document_correction_mode === 'string' ? modes[settings.document_correction_mode] ?? '未确认' : '未声明'}</p><details><summary>配置与版本审计</summary><p>{service.service_id} · {service.instance_id}</p><p>配置登记进程：{active.process_id} · 登记时间：{assetTime(active.recorded_at)}</p><p>配置指纹：{active.fingerprint}</p><pre className="asset-json">{JSON.stringify(settings, null, 2)}</pre></details></> : <p>未找到匹配的启动配置，不能推断执行模式。</p>}</article>
  })}</div>}</section>
}
