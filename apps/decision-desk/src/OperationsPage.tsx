import { sourceFailure } from './product/sourceFailure'
import { PlanningDecisionPanel } from './product/PlanningDecisionPanel'
import { useState } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, Cpu, Database, Globe2, Radio, SearchCheck, Server, ShieldCheck, Workflow } from 'lucide-react'
import type { EvolutionJob, OperationsOverview } from './api/types'
import { AssetBackLink, type AssetNavigate } from './product/assetFormat'
import { modeNames, operationStatus, researchServiceHealth, serviceNames, sourceNames } from './product/operationLabels'
import { PlanningCallsPanel } from './product/PlanningCallsPanel'

type Loadable<T> = { data?: T; loading: boolean; failed: boolean }

const jobStatusNames: Record<EvolutionJob['status'], string> = {
  queued: '排队', running: '执行中', retry_wait: '等待重试', waiting_inputs: '等待评测材料', pending_owner_review: '待负责人审核', completed: '已完成', failed: '失败', cancelled: '已取消',
}

const triggerNames: Record<EvolutionJob['trigger_type'], string> = {
  scheduled: '定时扫描', feedback: '用户反馈', failure_pattern: '失败模式', evaluation_batch: '评测批次',
}

const stageNames: Record<EvolutionJob['stage'], string> = { discover: '发现问题', plan: '安排验证', candidate: '生成候选', replay: '历史回放', holdout: '独立样本', shadow: '前瞻观察', review: '负责人审核' }

const stages: EvolutionJob['stage'][] = ['discover', 'plan', 'candidate', 'replay', 'holdout', 'shadow', 'review']

function DataState({ loading, failed, empty }: { loading: boolean; failed: boolean; empty?: string }) {
  if (loading) return <div className="workspace-state"><Radio size={17} className="pulse-icon" />正在读取业务账本…</div>
  if (failed) return <div className="workspace-state danger" role="alert"><AlertTriangle size={17} />无法取得当前状态，请重新读取。</div>
  if (empty) return <div className="workspace-state"><Database size={17} />{empty}</div>
  return null
}

export function OperationsPage({ data, loading, failed, onNavigate, onRefresh, back }: Loadable<OperationsOverview> & { onNavigate?: AssetNavigate; onRefresh?: () => void; back?: string }) {
  const auditBack = window.location.hash.split('?')[0] === '#/Operations' ? window.location.hash : `#/Operations${back ? `?${new URLSearchParams({ back })}` : ''}`
  const actions = <div className="heading-actions"><AssetBackLink back={back ?? null} /><button className="button secondary" onClick={() => onNavigate?.('management')}>返回管理与审计</button><button className="button secondary" onClick={onRefresh}>刷新运行状态</button></div>
  if (!data) return <div className="content-wrap">{actions}<PageTitle title="运行审计" eyebrow="进程 · 采集 · 执行边界" copy="核对实际进程、来源检查与已登记能力。" /><DataState loading={loading} failed={failed} /></div>
  const research = researchServiceHealth(data.services)
  const enabledSources = data.sources.filter(item => item.enabled)
  const successfulSources = enabledSources.filter(item => item.status === 'healthy')
  const evolution = data.services.find(item => item.service_id === 'hub-evolution-worker')
  const current = !failed
  return <div className="content-wrap operations-page">
    {actions}
    <PageTitle title="运行审计" eyebrow="进程 · 采集 · 执行边界" copy="进程在线、来源采集成功和研究完成分别核对。配置存在不等于模型已成功执行。" trailing={`记录时间 ${formatDate(data.checked_at)}`} />
    {failed && <div className="product-notice danger" role="alert"><p>刷新失败，下方保留上次读取的审计记录，不能确认当前运行状态。</p></div>}
    <section className="ops-metrics" aria-label="运行状态概况">
      <OpsMetric label="API 与研究通道" value={current ? `${research.online}/${research.expected}` : '未确认'} detail="业务接口、自动研究和用户研究分别核对" tone={current && research.complete ? 'success' : 'warning'} />
      <OpsMetric label="已登记进程" value={current ? `${data.services.filter(item => item.status === 'online').length}/${data.services.length}` : '未确认'} detail="包含采集、资料、行情更新及经验演进" tone="neutral" />
      <OpsMetric label="来源最近请求成功" value={current ? `${successfulSources.length}/${enabledSources.length}` : '未确认'} detail={`已启用 ${enabledSources.length} 个来源；解析和覆盖另行核对`} tone={current && successfulSources.length === enabledSources.length && enabledSources.length > 0 ? 'success' : 'warning'} />
      <OpsMetric label="待负责人审核" value={current ? data.jobs.pending_owner_review : '未确认'} detail={current && evolution ? `演进进程：${operationStatus(evolution.status)}` : '演进进程状态未确认'} tone="neutral" />
    </section>
    <section className="panel ops-panel services-panel">
      <PanelHeading icon={Server} title="全部登记进程" detail="心跳反映进程状态；模型、工具、采样和交付是否完成，以对应任务回执为准。" />
      <div className="service-grid">{data.services.map(service => <article className="service-row" key={service.service_id}>
        <StatusMark status={current ? service.status : 'unknown'} />
        <div className="service-main"><strong>{serviceNames[service.service_id] ?? '其他登记服务'}</strong><span>{service.service_id === 'hub-evolution-worker' ? '候选生成、评估与审核安排' : service.service_id === 'hub-realtime-worker' ? '来源采集、事件窗口及到期观察' : service.service_id === 'hub-documents' ? '资料解析与复核处理' : service.service_id === 'hub-historical-worker' ? '行情抓取与历史分区更新' : service.service_id === 'hub-api' ? '查询与业务命令接口' : '领取任务并保存执行结果'}</span></div>
        <div className="service-time"><strong>{current ? operationStatus(service.status) : '当前未确认'}</strong><span>{service.heartbeat_at ? `最近心跳 ${formatRelative(service.heartbeat_at)}` : '尚无心跳记录'}</span></div>
        <details className="service-detail"><summary>进程标识与技术记录</summary><dl><Fact label="服务标识" value={service.service_id} /><Fact label="实例" value={service.instance_id ?? '未记录'} /><Fact label="版本" value={service.version ?? '未记录'} /><Fact label="心跳声明模式" value={service.mode ? modeNames[service.mode] ?? '其他模式' : '未声明'} /><Fact label="最近错误代码" value={service.last_error_code ?? '无错误记录'} /></dl><p>角色和有效配置需按同一进程实例核对，可在管理页查看实际配置。</p></details>
      </article>)}</div>
      <div className="panel-header"><button className="product-link" onClick={() => onNavigate?.('management')}>核对进程实际配置</button><button className="product-link" onClick={() => onNavigate?.('runs', { back: auditBack })}>查看研究执行记录</button><button className="product-link" onClick={() => onNavigate?.('Evolution')}>查看经验演进</button></div>
    </section>
    <section className="ops-layout lower">
      <div className="panel ops-panel">
        <PanelHeading icon={Globe2} title="自动采集来源" detail="已启用是配置状态。最近请求成功仍需检查内容解析、时间精度和覆盖。" />
        {!data.sources.length ? <p className="workspace-state">尚无来源登记记录。</p> : <div className="operation-list">{data.sources.map(source => <div className="operation-row" key={source.source_id}><StatusMark status={current ? source.enabled ? source.status : 'disabled' : 'unknown'} /><div><strong>{sourceNames[source.source_id] ?? '其他登记来源'}</strong><span>{current ? source.enabled ? operationStatus(source.status) : '未启用' : '当前状态未确认'} · 连续失败 {source.consecutive_failures} 次</span>{source.error_code && <span>{current ? '' : '上次记录：'}{sourceFailure(source.error_code).reason} · 下次计划 {source.next_poll_at ? formatDate(source.next_poll_at) : '暂无记录'}</span>}<details><summary>来源技术记录</summary><p>{source.source_id}</p><p>游标：{source.cursor ?? '未记录'}</p><p>异常代码：{source.error_code ?? '无'}</p></details></div><div><strong>{source.last_success_at ? `最近成功 ${formatRelative(source.last_success_at)}` : '尚无成功记录'}</strong><button className="product-link" onClick={() => onNavigate?.('sources', { source: source.source_id, back: auditBack })}>核对采集记录</button></div></div>)}</div>}
      </div>
      <div className="panel ops-panel">
        <PanelHeading icon={Cpu} title="事件文本兼容入口" detail="以下是配置及探测结果，不代表每次 DSH 研究会话已成功运行。" />
        <dl className="runtime-facts"><Fact label="配置模式" value={modeNames[data.runtime.mode] ?? '未确认'} /><Fact label="供应商配置" value={data.runtime.provider_configured ? '已配置' : '未配置'} /><Fact label="在线探测" value={operationStatus(data.runtime.live_canary_status)} /><Fact label="配置模型" value={data.runtime.model ?? '未配置'} /></dl>
        <details className="runtime-facts"><summary>执行器技术记录</summary><p>{data.runtime.runtime_id} · {data.runtime.runtime_version}</p><p>接口模式：{data.runtime.api_mode ?? '未声明'}</p></details>
        <PanelHeading icon={SearchCheck} title="已登记工作台能力" detail="此目录用于工作台，不覆盖全部 DSH 原生工具。实际调用在研究报告中核对。" />
        {!data.capabilities.length ? <p className="workspace-state">工作台能力目录暂无登记，不能据此判断研究工具不可用。</p> : <div className="operation-list">{data.capabilities.map(capability => <div className="operation-row" key={capability.capability_id}><StatusMark status={current ? capability.status : 'unknown'} /><div><strong>{capability.capability_id}</strong><span>{current ? operationStatus(capability.status) : '当前未确认'}</span><details><summary>权限、网络与预算</summary><p>能力类型：{capability.capability_type}</p><p>权限：{capability.permissions.join(' · ') || '未声明'}</p><p>登记域名：{capability.network_domains.join(' · ') || '未声明，不能据此推断无限网络权限'}</p><p>超时：{capability.timeout_seconds} 秒 · 单次预算：{capability.max_cost_usd == null ? '未记录' : `$${capability.max_cost_usd}`}</p></details></div></div>)}</div>}
      </div>
    </section>
  </div>
}

export function EvolutionExecutionStatus({ data, loading, failed }: Loadable<OperationsOverview>) {
  const worker = data?.services.find(item => item.service_id === 'hub-evolution-worker')
  const unknown = failed || !worker || worker.status === 'stale'
  return <section className="product-card" aria-label="演进运行条件"><h2>当前执行状态</h2><p role="status">{loading ? '正在核对演进进程…' : unknown ? '演进进程状态未确认，请刷新运行记录。' : worker.status === 'online' ? '演进进程在线；任务是否执行及评估是否通过，以各自记录为准。' : '演进进程当前离线，新的候选生成与评估不会自动推进。'}</p><p>反馈、候选和评估结果分别保存。候选需完成历史回放、独立样本验证和前瞻观察，再由负责人审核是否启用。</p><p>没有任务不代表没有反馈；离线评测通过也不代表前瞻验证通过。</p></section>
}

export function EvolutionJobQueue({ jobs, loading, failed }: { jobs?: EvolutionJob[]; loading: boolean; failed: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const priority: Record<EvolutionJob['status'], number> = { running: 0, retry_wait: 1, pending_owner_review: 2, waiting_inputs: 3, queued: 4, failed: 5, completed: 6, cancelled: 7 }
  const ordered = [...(jobs ?? [])].sort((a, b) => priority[a.status] - priority[b.status] || b.created_at.localeCompare(a.created_at))
  const visible = expanded ? ordered : ordered.slice(0, 5)
  return <section className="panel evolution-jobs-panel">
    <PanelHeading icon={Workflow} title="演进任务" detail="从已登记任务核对触发原因、执行进度与审核状态" />
    {!jobs && <DataState loading={loading} failed={failed} />}
    {jobs?.length === 0 && <DataState loading={false} failed={false} empty="暂无已登记的演进任务。反馈和评估记录是否已转成任务，需要结合演进进程状态核对。" />}
    {visible.map((job) => <article className="job-row" key={job.job_id}>
      <div className="job-heading"><div><StatusMark status={job.status} /><div><strong>{triggerNames[job.trigger_type]}</strong><span>登记于 {formatDate(job.created_at)}</span></div></div><span className={`job-status ${statusTone(job.status)}`}>{jobStatusNames[job.status]}</span></div>
      <div className="stage-track" aria-label={`当前阶段 ${stageNames[job.stage]}`}>{stages.map((stage) => { const current = stages.indexOf(stage); const reached = current <= stages.indexOf(job.stage); return <div className={`${reached ? 'reached' : ''} ${stage === job.stage ? 'current' : ''}`} key={stage}><span /> <small>{stageNames[stage]}</small></div> })}</div>
      {job.status === 'waiting_inputs' && <p role="status">{job.last_error_code === 'prospective_evaluation_required' ? `已保存 ${job.experiment_refs.length} 个历史评测阶段，正在等待真实前瞻观察；不会用历史样例代替，也不会重新执行已核验的阶段。` : job.result_refs.length ? '部分评测结果已保存，后续材料仍需核验；恢复时会核对原输入与结果，不会自动启用。' : '候选已保存，执行条件与评测材料仍需核验；两者通过后才能继续。当前没有评测成绩，也不会自动启用。'}</p>}
      {job.status === 'completed' && !job.candidate_id && <><p>本次核查未生成候选；执行结束不代表原问题已修复。</p><PlanningDecisionPanel jobId={job.job_id} /></>}
      <details><summary>查看任务技术记录</summary><p>{job.job_id} · {job.domain_pack_ref}</p><div className="job-facts"><span><Clock3 size={13} />尝试 {job.attempt}/{job.max_attempts}</span><span><ShieldCheck size={13} />{job.lease_owner ?? '暂无执行中的任务租约'}</span><span className={job.last_error_code ? 'danger-text' : ''}><AlertTriangle size={13} />{job.last_error_code ?? '无错误记录'}</span><span><Database size={13} />{job.candidate_id ?? '尚未生成候选'}</span></div><PlanningCallsPanel jobId={job.job_id} showDecision={job.status !== 'completed' || !!job.candidate_id} /></details>
    </article>)}
    {ordered.length > 5 && <button className="button secondary job-list-toggle" onClick={() => setExpanded(!expanded)}>{expanded ? '收起任务列表' : `展开已读取的全部 ${ordered.length} 项任务`}</button>}
  </section>
}

export function PageTitle({ title, eyebrow, copy, trailing }: { title: string; eyebrow: string; copy: string; trailing?: string }) {
  return <section className="page-heading"><div><div className="eyebrow"><span className="live-dot" />{eyebrow}</div><h1>{title}</h1><p>{copy}</p></div>{trailing && <span className="page-timestamp">{trailing}</span>}</section>
}

function OpsMetric({ label, value, detail, tone }: { label: string; value: string | number; detail: string; tone: string }) { return <div><span>{label}</span><strong className={tone}>{value}</strong><small>{detail}</small></div> }
function PanelHeading({ icon: Icon, title, detail }: { icon: typeof Server; title: string; detail: string }) { return <header className="panel-header"><div className="ops-heading"><span><Icon size={16} /></span><div><h2>{title}</h2><p>{detail}</p></div></div></header> }
function Fact({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: string }) { return <div><dt>{label}</dt><dd className={tone}>{value}</dd></div> }
function StatusMark({ status }: { status: string }) { const tone = status === 'online' || status === 'healthy' || status === 'enabled' || status === 'passed' ? 'success' : status === 'offline' || status === 'failed' || status === 'rejected' ? 'danger' : status === 'stale' || status === 'degraded' || status === 'retry_wait' || status === 'pending_owner_review' || status === 'waiting_inputs' ? 'warning' : 'neutral'; return <span className={`status-mark ${tone}`} title={operationStatus(status)}>{tone === 'success' ? <CheckCircle2 size={15} /> : tone === 'danger' || tone === 'warning' ? <AlertTriangle size={15} /> : <Radio size={15} />}</span> }
function statusTone(status: EvolutionJob['status']) { return status === 'failed' ? 'danger' : status === 'completed' ? 'success' : status === 'pending_owner_review' || status === 'retry_wait' || status === 'waiting_inputs' ? 'warning' : 'neutral' }
function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
function formatRelative(value: string) { const diff = Date.now() - new Date(value).getTime(); const future = diff < 0; const minutes = Math.round(Math.abs(diff) / 60_000); if (minutes < 1) return '刚刚'; const text = minutes < 60 ? `${minutes} 分钟` : `${Math.round(minutes / 60)} 小时`; return future ? `${text}后` : `${text}前` }
