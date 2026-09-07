import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  Ban,
  BookOpenCheck,
  CheckCircle2,
  CircleGauge,
  Clock3,
  ExternalLink,
  FileSearch,
  GitBranch,
  MessageSquareText,
  Network,
  Play,
  RefreshCw,
  RotateCcw,
  SearchCheck,
  Send,
  ShieldAlert,
  TimerReset,
  Wrench,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, apiUrl } from '../api/client'
import type {
  OperationsOverview,
  ResearchRunCommand,
  ResearchRunDetailView,
  ResearchRunView,
  ResearchInboxItem,
  ResearchObservabilityView,
  ResearchValueEvaluation,
} from '../api/types'
import { PageTitle } from '../OperationsPage'

const OWNER_ID = import.meta.env.VITE_OWNER_ID?.trim() || 'owner'
const TERMINAL = new Set<ResearchRunView['status']>([
  'completed', 'degraded', 'research_only', 'rejected', 'failed', 'cancelled',
])
const TRACE_EVENTS = [
  'session_started', 'plan_created', 'task_started', 'model_step_started',
  'model_step_completed', 'tool_started', 'tool_completed', 'tool_failed',
  'subagent_started', 'subagent_completed', 'evidence_accepted', 'evidence_rejected',
  'coverage_assessed', 'replan', 'round_completed', 'synthesis_started', 'session_stopped',
]

const statusLabel: Record<ResearchRunView['status'], string> = {
  admitted: '已接收', queued: '排队中', researching: '研究中', retry_wait: '等待重试',
  completed: '已完成', degraded: '有界降级', research_only: '仅研究', rejected: '已拒绝',
  failed: '失败', cancelled: '已取消',
}

const originLabel: Record<ResearchRunView['admission_origin'], string> = {
  manual: '用户提交',
  automatic: '自动发现',
  scheduled_recheck: '定时复查',
  legacy: '历史兼容',
}

const priorityLabel: Record<ResearchRunView['priority'], string> = {
  critical: '紧急', high: '高优先级', normal: '普通优先级', low: '低优先级',
}

type ResearchPageProps = {
  operations?: OperationsOverview
  operationsLoading: boolean
  operationsFailed: boolean
}

export function ResearchPage({ operations, operationsLoading, operationsFailed }: ResearchPageProps) {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [composerOpen, setComposerOpen] = useState(false)
  const [input, setInput] = useState('')
  const [commandReason, setCommandReason] = useState('')
  const runsQuery = useQuery({
    queryKey: ['research-runs'],
    queryFn: api.researchRuns,
    refetchInterval: 3_000,
    retry: false,
  })
  const inboxQuery = useQuery({
    queryKey: ['research-inbox'],
    queryFn: api.researchInbox,
    refetchInterval: 5_000,
    retry: false,
  })
  const runs = runsQuery.data ?? []
  const displayRuns = useMemo(
    () => [...runs].sort((left, right) => Number(Boolean(left.parent_run_id)) - Number(Boolean(right.parent_run_id))),
    [runs],
  )
  useEffect(() => {
    if (!selectedId && displayRuns[0]) setSelectedId(displayRuns[0].run_id)
    if (selectedId && displayRuns.length && !displayRuns.some((item) => item.run_id === selectedId)) {
      setSelectedId(displayRuns[0].run_id)
    }
  }, [displayRuns, selectedId])
  const detailQuery = useQuery({
    queryKey: ['research-run', selectedId],
    queryFn: () => api.researchRun(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: 3_000,
    retry: false,
  })
  const detail = detailQuery.data
  const observabilityQuery = useQuery({
    queryKey: ['research-observability', selectedId],
    queryFn: () => api.researchObservability(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  })
  const valueEvaluationQuery = useQuery({
    queryKey: ['research-value-evaluation', selectedId],
    queryFn: () => api.researchValueEvaluation(selectedId!),
    enabled: Boolean(selectedId),
    retry: false,
  })
  useResearchTrace(selectedId, detail?.run.latest_sequence_no ?? 0, queryClient)

  const submit = useMutation({
    mutationFn: api.submitResearch,
    onSuccess: (result) => {
      setInput('')
      setComposerOpen(false)
      setSelectedId(result.run_id)
      void queryClient.invalidateQueries({ queryKey: ['research-runs'] })
    },
  })
  const command = useMutation({
    mutationFn: ({ runId, payload }: { runId: string; payload: ResearchRunCommand }) => (
      api.researchCommand(runId, payload, OWNER_ID)
    ),
    onSuccess: () => {
      setCommandReason('')
      void queryClient.invalidateQueries({ queryKey: ['research-runs'] })
      void queryClient.invalidateQueries({ queryKey: ['research-run', selectedId] })
    },
  })
  const sendCommand = (action: ResearchRunCommand['command']) => {
    if (!selectedId || !commandReason.trim()) return
    command.mutate({
      runId: selectedId,
      payload: {
        schema_version: 'research-run-command.v1',
        request_id: crypto.randomUUID(),
        command: action,
        reason: commandReason.trim(),
      },
    })
  }

  const worker = operations?.services.find((item) => item.service_id === 'hub-research-worker')
  const stats = useMemo(() => ({
    active: runs.filter((item) => item.status === 'researching' || item.status === 'queued').length,
    gaps: runs.filter((item) => item.coverage?.status === 'insufficient').length,
    completed: runs.filter((item) => TERMINAL.has(item.status)).length,
    // The list endpoint intentionally does not carry cost. Do not present an
    // incomplete aggregate as $0; only the selected detail can provide a
    // known estimate and null remains visibly unknown.
    cost: detail?.estimated_cost_usd ?? null,
  }), [runs, detail])

  return <div className="content-wrap research-page">
    <PageTitle
      title="Research Command Center"
      eyebrow="DURABLE AGENTIC RESEARCH"
      copy="主动发现、补齐证据、形成根因链，并在确定性 Gate 后输出分周期结论。"
      trailing={worker ? `Research worker · ${worker.status}` : operationsLoading ? '读取 worker 状态…' : 'Research worker · unavailable'}
    />
    {operationsFailed && <div className="research-notice danger"><AlertTriangle size={15} />运行健康数据不可用，研究结论不会被伪装为在线。</div>}
    <section className="research-metrics" aria-label="Research overview">
      <ResearchMetric icon={Activity} label="正在处理" value={stats.active} detail="Durable runs" tone="blue" />
      <ResearchMetric icon={ShieldAlert} label="证据不足" value={stats.gaps} detail="Open sufficiency gaps" tone="amber" />
      <ResearchMetric icon={CheckCircle2} label="已有终态" value={stats.completed} detail="Completed or bounded" tone="green" />
      <ResearchMetric icon={CircleGauge} label="选中任务成本" value={stats.cost === null ? '未知' : `$${stats.cost.toFixed(4)}`} detail="仅显示已知模型/工具估算" tone="neutral" />
    </section>
    <ResearchInboxPanel inbox={inboxQuery.data?.items ?? []} loading={inboxQuery.isLoading} failed={inboxQuery.isError} onSelectRun={setSelectedId} />
    <div className="research-toolbar">
      <div><span className={`live-dot ${worker?.status !== 'online' ? 'warning' : ''}`} /><strong>{worker?.status === 'online' ? '后台研究循环在线' : '后台研究循环未确认在线'}</strong><small>{worker?.mode ?? 'mode unknown'} · {worker?.heartbeat_at ? formatDate(worker.heartbeat_at) : 'no heartbeat'}</small></div>
      <button className="button primary" onClick={() => setComposerOpen((value) => !value)}><FileSearch size={15} />提交研究材料</button>
    </div>
    {composerOpen && <section className="research-composer" aria-label="Submit research material">
      <textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder="粘贴讲话、新闻、数据或需要主动核验的研究问题…" autoFocus />
      <div><span>{input.trim().length} 字符</span><button className="button primary" disabled={!input.trim() || submit.isPending} onClick={() => submit.mutate(input.trim())}>{submit.isPending ? <RotateCcw className="spin" size={14} /> : <Send size={14} />}{submit.isPending ? '正在入队' : '建立研究任务'}</button></div>
      {submit.isError && <p role="alert">提交失败：{submit.error.message}</p>}
    </section>}
    <div className="research-workspace">
      <ResearchRunList runs={displayRuns} selectedId={selectedId} loading={runsQuery.isLoading} failed={runsQuery.isError} onSelect={setSelectedId} />
      <section className="panel research-detail-panel">
        {detailQuery.isLoading && <ResearchState icon={Activity} text="正在读取规范化研究轨迹…" />}
        {detailQuery.isError && <ResearchState icon={AlertTriangle} text="研究详情不可用，未显示 raw JSON 或演示数据。" danger />}
        {!selectedId && !detailQuery.isLoading && <ResearchState icon={SearchCheck} text="选择一个研究任务查看计划、证据和结论。" />}
        {detail && <><ResearchDiagnostics observability={observabilityQuery.data} evaluation={valueEvaluationQuery.data} loading={observabilityQuery.isLoading || valueEvaluationQuery.isLoading} /><ResearchRunDetail detail={detail} commandReason={commandReason} setCommandReason={setCommandReason} commandPending={command.isPending} commandError={command.isError ? command.error.message : null} onCommand={sendCommand} /></>}
      </section>
    </div>
  </div>
}

const inboxStatusLabel: Record<ResearchInboxItem['status'], string> = {
  watching: '观察中', queued: '排队中', researching: '研究中', report_ready: '报告就绪',
  research_only: '仅研究', rejected: '已拒绝', failed: '失败', cancelled: '已取消',
}
const inboxBaselineLabel: Record<ResearchInboxItem['baseline_status'], string> = {
  not_applicable: '无需基线', pending: '基线采集中', ready: '基线就绪', unavailable: '基线不可用',
}
const inboxNotificationLabel: Record<ResearchInboxItem['notification_status'], string> = {
  not_applicable: '无需通知', pending: '通知待发送', retry_wait: '通知待重试',
  delivered: '通知已送达', failed: '通知失败',
}

function ResearchInboxPanel({ inbox, loading, failed, onSelectRun }: { inbox: ResearchInboxItem[]; loading: boolean; failed: boolean; onSelectRun: (id: string) => void }) {
  return <section className="panel research-inbox-panel" aria-label="Active research inbox">
    <div className="panel-header">
      <div><h2>主动研究 Inbox</h2><p>后台观察、事件触发、报告与通知状态</p></div>
      <span>{loading ? '同步中' : String(inbox.length) + ' 项'}</span>
    </div>
    {failed && <ResearchState icon={AlertTriangle} text="主动研究 Inbox 不可用" danger />}
    {!loading && !failed && inbox.length === 0 && <ResearchState icon={BookOpenCheck} text="暂无主动研究任务或观察事件" />}
    {!failed && inbox.length > 0 && <div className="research-inbox-items">{inbox.slice(0, 8).map((item) => {
      const canOpen = typeof item.run_id === 'string' && item.run_id.length > 0
      return <button key={item.event_id + ':' + (item.run_id ?? 'watch')} className={'research-inbox-item ' + (canOpen ? 'interactive' : '')} disabled={!canOpen} onClick={() => canOpen && onSelectRun(item.run_id!)}>
        <div className="research-inbox-heading"><span className={'research-status ' + inboxTone(item.status)}>{inboxStatusLabel[item.status]}</span><time>{formatRelative(item.updated_at)}</time></div>
        <strong>{item.event_title}</strong>
        <small>{item.event_family ?? '未分类事件'} · {inboxBaselineLabel[item.baseline_status]} · {inboxNotificationLabel[item.notification_status]}</small>
        <div className="research-inbox-meta"><span>{item.gate_status ?? '待裁决'}</span>{item.headline && <span>{item.headline}</span>}{item.next_recheck_at && <span>复查 {formatDate(item.next_recheck_at)}</span>}</div>
        {!canOpen && <em>观察中，尚未触发研究</em>}
      </button>
    })}</div>}
  </section>
}

function inboxTone(status: ResearchInboxItem['status']) {
  return status === 'report_ready' ? 'success' : status === 'failed' || status === 'rejected' || status === 'cancelled' ? 'danger' : status === 'research_only' ? 'warning' : 'running'
}

function ResearchDiagnostics({ observability, evaluation, loading }: { observability?: ResearchObservabilityView; evaluation?: ResearchValueEvaluation | null; loading: boolean }) {
  if (loading && !observability && !evaluation) return <div className="research-diagnostics loading">正在读取版本、来源和成本投影…</div>
  if (!observability && !evaluation) return <div className="research-diagnostics unavailable">当前任务暂无可用的可观测或价值评估记录</div>
  return <section className="research-diagnostics" aria-label="Research observability and evaluation">
    <div className="diagnostic-grid">
      {observability && <><div><span>运行版本</span><strong>{observability.versions.runtime_id} · {observability.versions.runtime_version}</strong><small>{observability.versions.domain_pack_ref} · {observability.versions.domain_pack_version}</small></div><div><span>来源尝试</span><strong>{observability.source_attempts.length} 次</strong><small>{observability.readiness.filter((item) => item.status !== 'ready').length} 个 requirement 未就绪</small></div><div><span>成本</span><strong>{observability.cost.status === 'known' && observability.cost.total_usd !== null ? '$' + observability.cost.total_usd.toFixed(4) : '未知/部分'}</strong><small>{observability.cost.unknown_components.length ? observability.cost.unknown_components.join(' · ') : '全部组件已归集'}</small></div></>}
      {evaluation && <div><span>研究价值</span><strong>{evaluation.usefulness === 'unlabeled' ? '尚未标注' : evaluation.usefulness}</strong><small>{evaluation.mode} · {Math.round(evaluation.citation_traceability * 100)}% 引用可追溯</small></div>}
    </div>
  </section>
}

function useResearchTrace(selectedId: string | null, after: number, queryClient: ReturnType<typeof useQueryClient>) {
  useEffect(() => {
    if (!selectedId || typeof EventSource === 'undefined') return
    const source = new EventSource(apiUrl(`/v1/research/runs/${encodeURIComponent(selectedId)}/events?after=${after}`))
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ['research-run', selectedId] })
      void queryClient.invalidateQueries({ queryKey: ['research-runs'] })
    }
    TRACE_EVENTS.forEach((event) => source.addEventListener(event, refresh))
    return () => source.close()
  }, [selectedId, after, queryClient])
}

export function ResearchRunList({ runs, selectedId, loading, failed, onSelect }: { runs: ResearchRunView[]; selectedId: string | null; loading: boolean; failed: boolean; onSelect: (id: string) => void }) {
  return <section className="panel research-run-list">
    <div className="panel-header"><div><h2>Research runs</h2><p>自动触发、人工提交与 scheduled recheck</p></div><span>{runs.length}</span></div>
    {loading && <ResearchState icon={Activity} text="正在读取研究队列…" />}
    {failed && <ResearchState icon={AlertTriangle} text="研究队列不可用" danger />}
    {!loading && !failed && runs.length === 0 && <ResearchState icon={BookOpenCheck} text="暂无研究任务" />}
    <div className="research-run-items">{runs.map((run) => <button key={run.run_id} className={`research-run-item ${selectedId === run.run_id ? 'selected' : ''}`} onClick={() => onSelect(run.run_id)}>
      <div className="research-run-heading"><span className={`research-status ${statusTone(run.status)}`}>{statusLabel[run.status]}</span><time>{formatRelative(run.updated_at)}</time></div>
      <strong>{run.event_title}</strong>
      <div className="research-run-meta"><span>{run.runtime_id}</span><span>R{run.current_round}/{run.budget.max_evidence_rounds}</span><span>{Math.round((run.coverage?.hard_coverage_ratio ?? 0) * 100)}% hard</span></div>
      {run.parent_run_id && <small><TimerReset size={12} />Recheck · parent {shortId(run.parent_run_id)}</small>}
    </button>)}</div>
  </section>
}

export function ResearchRunDetail({ detail, commandReason, setCommandReason, commandPending, commandError, onCommand }: { detail: ResearchRunDetailView; commandReason: string; setCommandReason: (value: string) => void; commandPending: boolean; commandError: string | null; onCommand: (action: ResearchRunCommand['command']) => void }) {
  const { run } = detail
  const canCancel = ['admitted', 'queued', 'researching', 'retry_wait'].includes(run.status)
  const canRetry = ['failed', 'cancelled', 'degraded'].includes(run.status)
  const canRecheck = ['completed', 'degraded'].includes(run.status)
  return <>
    <header className="research-detail-header">
      <div><div className="research-detail-status"><span className={`research-status ${statusTone(run.status)}`}>{statusLabel[run.status]}</span><span>{originLabel[run.admission_origin]}</span><span>{priorityLabel[run.priority]}</span><span>{run.runtime_id}</span></div><h2>{run.event_title}</h2><p className="mono">{run.run_id}</p></div>
      <div className="research-budget"><strong>Round {run.current_round}/{run.budget.max_evidence_rounds}</strong><span>{detail.total_tool_calls}/{run.budget.max_tool_calls} tools · {detail.total_subagents}/{run.budget.max_subagents} subagents</span></div>
    </header>
    <div className="research-progress"><span style={{ width: `${Math.min(100, (run.current_round / run.budget.max_evidence_rounds) * 100)}%` }} /></div>
    <div className="research-detail-body">
      {run.failure && <section className="research-section research-failure" aria-label="Failure provenance"><SectionHeading icon={AlertTriangle} title="Failure provenance" meta={run.failure.retryable ? 'bounded retry allowed' : 'fail closed'} />{failureExplanation(run.failure.error_code, run.failure.cause_code) && <p>{failureExplanation(run.failure.error_code, run.failure.cause_code)}</p>}<p>{detail.evidence.length} 条 Evidence 已保留</p><div className="failure-grid"><strong>{run.failure.error_code}</strong><span>origin · {run.failure.origin}</span><span>cause · {run.failure.cause_code ?? 'not provided'}</span><span>capability · {run.failure.capability_id ?? 'not identified'}</span><span>tool call · {run.failure.tool_call_id ?? 'not identified'}</span><span>deadline · {run.failure.deadline_ms === null ? 'not specified' : `${run.failure.deadline_ms}ms`}</span></div></section>}
      <section className="research-section sufficiency-section"><SectionHeading icon={ShieldAlert} title="Evidence sufficiency" meta={run.coverage?.status ?? 'not assessed'} />
        <div className="sufficiency-grid"><Coverage label="Hard coverage" value={run.coverage?.hard_coverage_ratio ?? 0} /><Coverage label="Soft coverage" value={run.coverage?.soft_coverage_ratio ?? 0} /><div className="stop-reason"><span>Stop reason</span><strong>{run.stop_reason?.code ?? '运行中'}</strong><p>{run.stop_reason?.detail ?? run.current_action ?? '等待下一条规范化轨迹'}</p></div></div>
        {run.coverage?.gaps.length ? <div className="gap-list">{run.coverage.gaps.map((gap) => <div key={gap.requirement_id}><AlertTriangle size={14} /><div><strong>{gap.requirement_id}</strong><span>{gap.reason_code} · {gap.importance} · {gap.query_hint}</span></div></div>)}</div> : <div className="research-empty-line"><CheckCircle2 size={14} />没有未关闭的 evidence gap</div>}
      </section>
      <section className="research-section"><SectionHeading icon={GitBranch} title="Research plan" meta={`${detail.rounds.length} rounds`} />
        {detail.rounds.length === 0 ? <ResearchEmpty text="尚未形成计划" /> : detail.rounds.map((round) => <div className="research-round" key={round.round}><div className="round-heading"><strong>Round {round.round}</strong><span>{round.plan.objective}</span><time>{formatDate(round.finished_at)}</time></div><div className="task-list">{round.plan.tasks.map((task) => <div key={task.task_id}><span>{task.priority}</span><div><strong>{task.objective}</strong><p>{task.question}</p><small>{task.capability_id} · {task.success_condition}</small></div></div>)}</div><ToolActivity invocations={round.tool_invocations} /></div>)}
      </section>
      <section className="research-section"><SectionHeading icon={Network} title="Evidence" meta={`${detail.evidence.length} accepted / reviewed`} />
        {detail.evidence.length === 0 ? <ResearchEmpty text="尚无新增研究证据" /> : <div className="research-evidence-list">{detail.evidence.map((evidence) => <article key={evidence.evidence_id}><div><span className={`evidence-kind ${evidence.kind}`}>{evidence.kind}</span><span>{evidence.authority}</span><span className={evidence.quality === 'accepted' ? 'success-text' : 'warning-text'}>{evidence.quality}</span></div><h3>{evidence.source_id}</h3><p>{evidenceSummary(evidence.excerpt)}</p><footer><span>{evidence.requirement_id} · round {evidence.round}</span>{evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer" aria-label={`打开来源 ${evidence.source_id}`}><ExternalLink size={13} /></a>}</footer></article>)}</div>}
      </section>
      <section className="research-section"><SectionHeading icon={GitBranch} title="Causal case" meta={detail.causal_case ? 'main + counter chain' : 'pending'} />
        {!detail.causal_case ? <ResearchEmpty text="证据尚不足以形成因果案例" /> : <><h3 className="causal-thesis">{detail.causal_case.thesis}</h3><div className="causal-columns"><CausalChain title="Main chain" links={detail.causal_case.main_chain} /><CausalChain title="Counter chain" links={detail.causal_case.opposite_chain} counter /></div></>}
      </section>
      <section className="research-section"><SectionHeading icon={Clock3} title="Horizon decisions" meta="30m / 24h / 72h independent" />
        {detail.horizons.length === 0 ? <ResearchEmpty text="Gate 未允许生成分周期方向" /> : <div className="horizon-grid">{detail.horizons.map((horizon) => <article key={horizon.horizon}><header><span>{horizon.horizon}</span><strong className={`direction ${horizon.action}`}>{horizon.action}</strong><b>{Math.round(horizon.subjective_probability * 100)}%</b></header><dl><div><dt>Trigger</dt><dd>{horizon.trigger}</dd></div><div><dt>Invalidation</dt><dd>{horizon.invalidation}</dd></div><div><dt>Next review</dt><dd>{formatDate(horizon.next_review_at)}</dd></div></dl>{horizon.confidence_cap_reason && <p><ShieldAlert size={13} />{horizon.confidence_cap_reason}</p>}</article>)}</div>}
      </section>
      <section className="research-section"><SectionHeading icon={Activity} title="Research trace" meta={`${detail.trace.length} normalized events`} />
        {detail.trace.length === 0 ? <ResearchEmpty text="等待第一条轨迹" /> : <div className="research-trace">{detail.trace.map((event) => <div key={event.sequence_no}><span className={`trace-mark ${event.status}`} /> <time>{formatTime(event.occurred_at)}</time><div><strong>{traceLabel(event.event_type)}</strong><p>{event.summary}</p><small>{event.stage}{event.reference_id ? ` · ${event.reference_type}: ${shortId(event.reference_id)}` : ''}{event.error_code ? ` · ${event.error_code}` : ''}</small></div></div>)}</div>}
      </section>
      <section className="research-section owner-command"><SectionHeading icon={MessageSquareText} title="Owner command" meta="audited and idempotent" />
        <textarea value={commandReason} onChange={(event) => setCommandReason(event.target.value)} placeholder="记录取消、重试、复查或反馈的理由…" />
        <div className="owner-command-actions"><button className="button secondary" disabled={!commandReason.trim() || commandPending || !canCancel} onClick={() => onCommand('cancel')}><Ban size={14} />取消</button><button className="button secondary" disabled={!commandReason.trim() || commandPending || !canRetry} onClick={() => onCommand('retry')}><RefreshCw size={14} />重试</button><button className="button secondary" disabled={!commandReason.trim() || commandPending || !canRecheck} onClick={() => onCommand('recheck')}><TimerReset size={14} />立即复查</button><button className="button primary" disabled={!commandReason.trim() || commandPending} onClick={() => onCommand('feedback')}><MessageSquareText size={14} />记录反馈</button></div>
        {commandError && <p className="command-error" role="alert">命令失败：{commandError}</p>}
      </section>
    </div>
  </>
}

function ToolActivity({ invocations }: { invocations: ResearchRunDetailView['rounds'][number]['tool_invocations'] }) {
  if (!invocations.length) return <div className="research-empty-line"><Wrench size={13} />本轮未记录工具调用</div>
  return <div className="tool-activity">{invocations.map((tool) => <div key={tool.tool_call_id}><span className={`tool-mark ${tool.status}`}>{tool.status === 'succeeded' ? <CheckCircle2 size={13} /> : tool.status === 'running' ? <Play size={13} /> : <AlertTriangle size={13} />}</span><div><strong>{tool.tool_name}</strong><p>{tool.query_summary}</p><small>{tool.capability_id} · attempt {tool.attempt} · {tool.latency_ms === null ? 'pending' : `${tool.latency_ms}ms`} · {tool.cost_usd === null ? 'cost unknown' : `$${tool.cost_usd.toFixed(4)}`}</small>{tool.error && <small className="tool-error">{tool.error.error_code} · {tool.error.origin} · {tool.error.retryable ? 'retryable' : 'fail closed'}{tool.error.cause_code ? ` · ${tool.error.cause_code}` : ''}</small>}</div></div>)}</div>
}

function CausalChain({ title, links, counter = false }: { title: string; links: NonNullable<ResearchRunDetailView['causal_case']>['main_chain']; counter?: boolean }) {
  return <div className={`causal-chain ${counter ? 'counter' : ''}`}><h4>{title}</h4>{links.map((link, index) => <div key={link.link_id}><span>{index + 1}</span><div><strong>{link.statement}</strong><p><ArrowRight size={12} />确认：{link.confirmation}</p><p><ArrowDownRight size={12} />失效：{link.invalidation}</p><small>{link.claim_type} · {link.affected_horizons.join(' / ')}</small></div></div>)}</div>
}

function ResearchMetric({ icon: Icon, label, value, detail, tone }: { icon: typeof Activity; label: string; value: string | number; detail: string; tone: string }) { return <div><span className={`research-metric-icon ${tone}`}><Icon size={15} /></span><div><small>{label}</small><strong>{value}</strong><span>{detail}</span></div></div> }
function SectionHeading({ icon: Icon, title, meta }: { icon: typeof Activity; title: string; meta: string }) { return <header className="research-section-heading"><div><Icon size={15} /><h3>{title}</h3></div><span>{meta}</span></header> }
function Coverage({ label, value }: { label: string; value: number }) { const percent = Math.round(value * 100); return <div className="coverage-meter"><div><span>{label}</span><strong>{percent}%</strong></div><div><span style={{ width: `${percent}%` }} /></div></div> }
function ResearchState({ icon: Icon, text, danger = false }: { icon: typeof Activity; text: string; danger?: boolean }) { return <div className={`research-state ${danger ? 'danger' : ''}`}><Icon size={16} />{text}</div> }
function ResearchEmpty({ text }: { text: string }) { return <div className="research-empty"><SearchCheck size={15} />{text}</div> }
function failureExplanation(errorCode: string, causeCode: string | null) { return errorCode === 'dsh_evidence_unattested' && causeCode === 'synthesis_attestation' ? 'Synthesis evidence attestation failed. The model referenced Evidence outside the trusted input and MCP result set.' : null }
function evidenceSummary(value: string) {
  const normalized = value.trim()
  if (!normalized) return 'Empty evidence excerpt'
  if (normalized.startsWith('{') || normalized.startsWith('[')) {
    try {
      const fields: string[] = []
      collectEvidenceFields(JSON.parse(normalized) as unknown, fields)
      if (fields.length) return fields.slice(0, 8).join(' · ')
    } catch { /* Preserve a bounded plain-text fallback below. */ }
  }
  if (/<[a-z][\s\S]*>/i.test(normalized) && typeof DOMParser !== 'undefined') {
    const document = new DOMParser().parseFromString(normalized, 'text/html')
    document.querySelectorAll('script, style, nav, header, footer, aside').forEach((node) => node.remove())
    return boundedEvidenceText(document.body.textContent ?? '')
  }
  return boundedEvidenceText(normalized)
}
function collectEvidenceFields(value: unknown, result: string[], prefix = ''): void {
  if (result.length >= 8) return
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    if (prefix) result.push(`${prefix.split('.').at(-1)} ${String(value)}`)
    return
  }
  if (Array.isArray(value)) {
    value.slice(0, 4).forEach((item, index) => collectEvidenceFields(item, result, `${prefix}.${index}`))
    return
  }
  if (typeof value === 'object') Object.entries(value).forEach(([key, item]) => collectEvidenceFields(item, result, prefix ? `${prefix}.${key}` : key))
}
function boundedEvidenceText(value: string) { const text = value.replace(/\s+/g, ' ').trim(); return text.length <= 480 ? text : `${text.slice(0, 477)}...` }
function statusTone(status: ResearchRunView['status']) { return status === 'completed' ? 'success' : status === 'failed' || status === 'rejected' || status === 'cancelled' ? 'danger' : status === 'degraded' || status === 'research_only' || status === 'retry_wait' ? 'warning' : 'running' }
function traceLabel(value: string) { return value.split('_').map((item) => item[0]?.toUpperCase() + item.slice(1)).join(' ') }
function shortId(value: string) { return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value }
function formatDate(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
function formatTime(value: string) { return new Date(value).toLocaleTimeString('zh-CN', { hour12: false }) }
function formatRelative(value: string) { const diff = Math.max(0, Date.now() - new Date(value).getTime()); const minutes = Math.round(diff / 60_000); return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes}m ago` : `${Math.round(minutes / 60)}h ago` }
