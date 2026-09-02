import { AlertTriangle, CheckCircle2, Clock3, Cpu, Database, Globe2, Radio, SearchCheck, Server, ShieldCheck, Workflow } from 'lucide-react'
import type { EvolutionJob, OperationsOverview } from './api/types'

type Loadable<T> = { data?: T; loading: boolean; failed: boolean }

const serviceNames: Record<string, string> = {
  'hub-api': 'Hub API',
  'hub-realtime-worker': 'Realtime worker',
  'hub-evolution-worker': 'Evolution worker',
}

const jobStatusNames: Record<EvolutionJob['status'], string> = {
  queued: '排队', running: '执行中', retry_wait: '等待重试', pending_owner_review: '待 owner 审查', completed: '已完成', failed: '失败', cancelled: '已取消',
}

const triggerNames: Record<EvolutionJob['trigger_type'], string> = {
  scheduled: '定时扫描', feedback: 'Owner 反馈', failure_pattern: '失败模式', evaluation_batch: '评测批次',
}

const stages: EvolutionJob['stage'][] = ['discover', 'plan', 'candidate', 'replay', 'holdout', 'shadow', 'review']

function DataState({ loading, failed, empty }: { loading: boolean; failed: boolean; empty?: string }) {
  if (loading) return <div className="workspace-state"><Radio size={17} className="pulse-icon" />正在读取业务账本…</div>
  if (failed) return <div className="workspace-state danger" role="alert"><AlertTriangle size={17} />数据不可用，未使用缓存或演示数据替代。</div>
  if (empty) return <div className="workspace-state"><Database size={17} />{empty}</div>
  return null
}

export function OperationsPage({ data, loading, failed }: Loadable<OperationsOverview>) {
  if (!data) return <div className="content-wrap"><PageTitle title="Operations" eyebrow="LIVE OBSERVATION" copy="三个逻辑进程、运行模式、来源、能力和演进任务的真实运行状态。" /><DataState loading={loading} failed={failed} /></div>
  const online = data.services.filter((item) => item.status === 'online').length
  const enabledSources = data.sources.filter((item) => item.enabled).length
  const executableCapabilities = data.capabilities.filter((item) => item.status === 'enabled' || item.status === 'shadow').length
  return <div className="content-wrap operations-page">
    <PageTitle title="Operations" eyebrow="LIVE OBSERVATION" copy="三个逻辑进程、运行模式、来源、能力和演进任务的真实运行状态。" trailing={`检查于 ${formatDate(data.checked_at)}`} />
    <section className="ops-metrics" aria-label="Operations summary">
      <OpsMetric label="在线服务" value={`${online}/${data.services.length}`} detail={online === data.services.length ? '全部在线' : '存在未启动或失联服务'} tone={online === data.services.length ? 'success' : 'warning'} />
      <OpsMetric label="Runtime" value={data.runtime.mode} detail={data.runtime.runtime_version} tone={data.runtime.mode === 'provider' ? 'success' : 'neutral'} />
      <OpsMetric label="启用来源" value={`${enabledSources}/${data.sources.length}`} detail={data.sources.length ? 'durable cursor' : '尚无来源状态'} tone={enabledSources ? 'success' : 'neutral'} />
      <OpsMetric label="待人工审查" value={data.jobs.pending_owner_review} detail={`${data.jobs.running} 执行中 · ${data.jobs.failed} 失败`} tone={data.jobs.failed ? 'danger' : data.jobs.pending_owner_review ? 'warning' : 'neutral'} />
    </section>
    <section className="ops-layout">
      <div className="panel ops-panel services-panel">
        <PanelHeading icon={Server} title="逻辑进程" detail="heartbeat 只表示存活；Job lease 决定任务所有权" />
        <div className="service-grid">{data.services.map((service) => <article className="service-row" key={service.service_id}>
          <StatusMark status={service.status} />
          <div className="service-main"><strong>{serviceNames[service.service_id] ?? service.service_id}</strong><span>{service.role} · {service.mode ?? '从未报告模式'}</span></div>
          <div className="service-time"><strong>{service.status}</strong><span>{service.heartbeat_at ? formatRelative(service.heartbeat_at) : '无 heartbeat'}</span></div>
          <div className="service-detail"><span>实例</span><strong title={service.instance_id ?? undefined}>{service.instance_id ?? '未启动'}</strong><span>版本</span><strong>{service.version ?? '—'}</strong><span>最近错误</span><strong className={service.last_error_code ? 'danger-text' : ''}>{service.last_error_code ?? 'none'}</strong></div>
        </article>)}</div>
      </div>
      <div className="panel ops-panel runtime-panel">
        <PanelHeading icon={Cpu} title="模型运行边界" detail="执行模式、Provider 配置和 live canary 分开判断" />
        <dl className="runtime-facts">
          <Fact label="实际模式" value={data.runtime.mode} tone={data.runtime.mode === 'provider' ? 'success' : 'neutral'} />
          <Fact label="执行 Runtime" value={`${data.runtime.runtime_id} · ${data.runtime.runtime_version}`} />
          <Fact label="Provider 已配置" value={data.runtime.provider_configured ? '是' : '否'} tone={data.runtime.provider_configured ? 'success' : 'warning'} />
          <Fact label="Live canary" value={data.runtime.live_canary_status} tone={data.runtime.live_canary_status === 'passed' ? 'success' : data.runtime.live_canary_status === 'failed' ? 'danger' : 'warning'} />
          <Fact label="模型 / API" value={data.runtime.model ? `${data.runtime.model} · ${data.runtime.api_mode ?? 'unknown'}` : '当前未调用远端模型'} />
        </dl>
      </div>
    </section>
    <section className="ops-layout lower">
      <div className="panel ops-panel">
        <PanelHeading icon={Globe2} title="实时来源" detail={`${enabledSources} 个启用 · cursor 与 next poll 来自账本`} />
        {data.sources.length === 0 ? <DataState loading={false} failed={false} empty="尚无来源注册或轮询状态。" /> : <div className="operation-list">{data.sources.map((source) => <div className="operation-row" key={source.source_id}><StatusMark status={source.enabled ? source.status : 'disabled'} /><div><strong>{source.source_id}</strong><span>{source.enabled ? source.status : 'disabled'} · cursor {source.cursor ?? 'none'}</span></div><div><strong>{source.next_poll_at ? formatRelative(source.next_poll_at) : '未排期'}</strong><span>{source.error_code ?? `${source.consecutive_failures} 连续失败`}</span></div></div>)}</div>}
      </div>
      <div className="panel ops-panel">
        <PanelHeading icon={SearchCheck} title="审计能力" detail={`${executableCapabilities} 个 enabled / shadow`} />
        {data.capabilities.length === 0 ? <DataState loading={false} failed={false} empty="尚无 Capability Manifest。" /> : <div className="operation-list">{data.capabilities.map((capability) => <div className="operation-row capability-operation" key={capability.capability_id}><StatusMark status={capability.status} /><div><strong>{capability.capability_id}</strong><span>{capability.capability_type} · {capability.permissions.join(' · ') || 'no permissions'}</span></div><div><strong>{capability.status}</strong><span>{capability.network_domains.join(' · ') || 'broad / no domains'}</span></div></div>)}</div>}
      </div>
    </section>
  </div>
}

export function EvolutionJobQueue({ jobs, loading, failed }: { jobs?: EvolutionJob[]; loading: boolean; failed: boolean }) {
  return <section className="panel evolution-jobs-panel">
    <PanelHeading icon={Workflow} title="Evolution jobs" detail="触发、lease、重试与 owner review 均来自 durable job" />
    {!jobs && <DataState loading={loading} failed={failed} />}
    {jobs?.length === 0 && <DataState loading={false} failed={false} empty="尚无可执行演进输入；需要负面反馈、失败模式或新的 Evaluation。" />}
    {jobs?.map((job) => <article className="job-row" key={job.job_id}>
      <div className="job-heading"><div><StatusMark status={job.status} /><div><strong>{triggerNames[job.trigger_type]}</strong><span>{job.job_id} · {job.domain_pack_ref}</span></div></div><span className={`job-status ${statusTone(job.status)}`}>{jobStatusNames[job.status]}</span></div>
      <div className="stage-track" aria-label={`当前阶段 ${job.stage}`}>{stages.map((stage) => { const current = stages.indexOf(stage); const reached = current <= stages.indexOf(job.stage); return <div className={`${reached ? 'reached' : ''} ${stage === job.stage ? 'current' : ''}`} key={stage}><span /> <small>{stage}</small></div> })}</div>
      <div className="job-facts"><span><Clock3 size={13} />attempt {job.attempt}/{job.max_attempts}</span><span><ShieldCheck size={13} />{job.lease_owner ?? 'no active lease'}</span><span className={job.last_error_code ? 'danger-text' : ''}><AlertTriangle size={13} />{job.last_error_code ?? 'no error'}</span><span><Database size={13} />{job.candidate_id ?? 'candidate pending'}</span></div>
    </article>)}
  </section>
}

export function PageTitle({ title, eyebrow, copy, trailing }: { title: string; eyebrow: string; copy: string; trailing?: string }) {
  return <section className="page-heading"><div><div className="eyebrow"><span className="live-dot" />{eyebrow}</div><h1>{title}</h1><p>{copy}</p></div>{trailing && <span className="page-timestamp">{trailing}</span>}</section>
}

function OpsMetric({ label, value, detail, tone }: { label: string; value: string | number; detail: string; tone: string }) { return <div><span>{label}</span><strong className={tone}>{value}</strong><small>{detail}</small></div> }
function PanelHeading({ icon: Icon, title, detail }: { icon: typeof Server; title: string; detail: string }) { return <header className="panel-header"><div className="ops-heading"><span><Icon size={16} /></span><div><h2>{title}</h2><p>{detail}</p></div></div></header> }
function Fact({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: string }) { return <div><dt>{label}</dt><dd className={tone}>{value}</dd></div> }
function StatusMark({ status }: { status: string }) { const tone = status === 'online' || status === 'healthy' || status === 'enabled' || status === 'passed' ? 'success' : status === 'offline' || status === 'failed' || status === 'rejected' ? 'danger' : status === 'stale' || status === 'retry_wait' || status === 'pending_owner_review' ? 'warning' : 'neutral'; return <span className={`status-mark ${tone}`} title={status}>{tone === 'success' ? <CheckCircle2 size={15} /> : tone === 'danger' || tone === 'warning' ? <AlertTriangle size={15} /> : <Radio size={15} />}</span> }
function statusTone(status: EvolutionJob['status']) { return status === 'failed' ? 'danger' : status === 'completed' ? 'success' : status === 'pending_owner_review' || status === 'retry_wait' ? 'warning' : 'neutral' }
function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
function formatRelative(value: string) { const diff = Date.now() - new Date(value).getTime(); const future = diff < 0; const minutes = Math.round(Math.abs(diff) / 60_000); if (minutes < 1) return '刚刚'; const text = minutes < 60 ? `${minutes} 分钟` : `${Math.round(minutes / 60)} 小时`; return future ? `${text}后` : `${text}前` }
