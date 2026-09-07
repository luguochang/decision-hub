/*
 * Browser face of the Decision Hub plugin.
 *
 * This file intentionally uses the official DSH client module-loader surface
 * only: the build wrapper provides `require`, and the plugin registers typed
 * status, report, replay-safety and intake projections through `ctx.slots`.
 * DSH owns Session/Trajectory and the Hub owns the durable business facts.
 */
const React = require('react')
const { createElement, useEffect, useRef, useState } = React

    const EMPTY = { kind: 'unknown', label: 'Hub status unavailable', detail: '', business: null, runtimeMode: null }

const gateLabels = { publish: '可发布', degraded: '降级', research_only: '仅研究', reject: '拒绝' }
const coverageLabels = { sufficient: '证据充分', insufficient: '证据不足', bounded_stop: '有界停止' }
const actionLabels = { long: '做多研究', short: '做空研究', neutral: '中性', no_trade: '不交易' }
const originLabels = { manual: '用户提交', automatic: '自动发现', scheduled_recheck: '定时复查', legacy: '历史兼容' }
const priorityLabels = { critical: '紧急', high: '高优先级', normal: '普通优先级', low: '低优先级' }
const businessStatusLabels = {
  admitted: '研究已受理', queued: '研究排队中', researching: '正在研究', retry_wait: '等待重试',
  completed: '研究完成', degraded: '研究降级完成', research_only: '仅研究完成',
  rejected: '研究已拒绝', failed: '研究失败', cancelled: '研究已取消',
}
const inboxStatusLabels = {
  watching: '观察中', queued: '排队中', researching: '研究中', report_ready: '报告就绪',
  research_only: '仅研究', rejected: '已拒绝', failed: '失败', cancelled: '已取消',
}
const baselineStatusLabels = {
  not_applicable: '无需基线', pending: '基线采集中', ready: '基线就绪', unavailable: '基线不可用',
}
const notificationStatusLabels = {
  not_applicable: '无需通知', pending: '通知待发送', retry_wait: '通知待重试',
  delivered: '通知已送达', failed: '通知失败',
}
const FINAL_BUSINESS = new Set(['completed', 'degraded', 'research_only', 'rejected', 'failed', 'cancelled'])
    const STATUS_POLL_MS = 3_000
const PRODUCT_WORKSPACE_TITLE = 'Crypto Macro Trader'

function productWorkspaceOf(snapshot) {
  const items = Array.isArray(snapshot?.items) ? snapshot.items : []
  return items.find(item => {
    if (item?.title === PRODUCT_WORKSPACE_TITLE) return true
    const path = typeof item?.path === 'string' ? item.path.replace(/[\\/]+$/, '') : ''
    return path.split(/[\\/]/).at(-1) === PRODUCT_WORKSPACE_TITLE
  }) || null
}

function latestProductSessionOf(workspace, snapshot, archivedSessionIds = []) {
  if (workspace === null || workspace === undefined) return null
  const byId = snapshot?.byId && typeof snapshot.byId === 'object' ? snapshot.byId : {}
  const archived = new Set(Array.isArray(archivedSessionIds) ? archivedSessionIds : [])
  const rows = (Array.isArray(workspace.sessionIds) ? workspace.sessionIds : [])
    .map(id => byId[id])
    .filter(row => row && typeof row.id === 'string' && !archived.has(row.id))
  if (rows.length === 0) return null
  // A non-empty Session is more useful on first load because it can expose the
  // report projection immediately. Fall back to an existing blank Session so
  // the Host does not accumulate duplicate empty Sessions.
  const nonBlank = rows.filter(row => row.blank !== true)
  const candidates = nonBlank.length > 0 ? nonBlank : rows
  candidates.sort((left, right) => (Number(right.updatedAt) || 0) - (Number(left.updatedAt) || 0))
  return candidates[0]?.id || null
}

function autoSelectProductWorkspace(sessions, workspaces) {
  let settled = false
  let creating = false
  let disposed = false
  const reconcile = () => {
    if (disposed || settled || creating) return
    const workspaceSnapshot = workspaces?.list?.getSnapshot?.()
    const sessionSnapshot = sessions?.list?.getSnapshot?.()
    if (workspaceSnapshot?.phase !== 'ready' || sessionSnapshot?.phase !== 'ready') return
    const workspace = productWorkspaceOf(workspaceSnapshot)
    if (workspace === null) return
    const current = sessionSnapshot.current
    if (typeof current === 'string' && workspace.sessionIds?.includes(current)) {
      settled = true
      return
    }
    const target = latestProductSessionOf(workspace, sessionSnapshot, workspaceSnapshot.archivedSessionIds)
    if (target !== null) {
      sessions.open(target)
      settled = true
      return
    }
    creating = true
    Promise.resolve(sessions.create({ workspaceId: workspace.workspaceId })).then(sessionId => {
      if (disposed) return
      sessions.open(sessionId)
      settled = true
    }).catch(() => {
      // Keep the selector retryable if the official Session Controller is
      // temporarily unavailable; the next snapshot or reconnect will retry.
      creating = false
    })
  }
  const disposeWorkspace = workspaces?.list?.subscribe?.(reconcile)
  const disposeSessions = sessions?.list?.subscribe?.(reconcile)
  reconcile()
  return () => {
    disposed = true
    if (typeof disposeWorkspace === 'function') disposeWorkspace()
    if (typeof disposeSessions === 'function') disposeSessions()
  }
}

    function runtimeModeLabel(mode) {
      return mode === 'replay' ? 'DSH 回放验收（只读）' : mode === 'live' ? 'DSH Live 交互' : 'DSH 运行模式未知'
    }

    function runtimeNotice(mode) {
      if (mode !== 'replay') return ''
      return '当前是 replay 离线验收，只能查看已录制轨迹；请使用 live 启动入口后再新建或继续会话。'
    }

    function protectedComposerTarget(target) {
      if (target === null || typeof target !== 'object' || typeof target.closest !== 'function') return false
      return target.closest('[data-composer-input], [aria-label="发送消息"]') !== null
    }

    function researchComposerSubmitIntent(event) {
      const target = event?.target
      if (target === null || typeof target !== 'object' || typeof target.closest !== 'function') return false
      if (event.type === 'click') return target.closest('[aria-label="发送消息"]') !== null
      return event.type === 'keydown'
        && event.key === 'Enter'
        && event.shiftKey !== true
        && event.isComposing !== true
        && target.closest('[data-composer-input]') !== null
    }

    function composerText(documentLike) {
      const input = documentLike?.querySelector?.('[data-composer-input]')
      return typeof input?.textContent === 'string' ? input.textContent.trim() : ''
    }

    function intakePayload(text, sessionId) {
      return {
        schema_version: 'dsh-research-intake.v1',
        text,
        source_id: 'dsh-web',
        language: 'zh',
        client_session_id: sessionId,
      }
    }

    function intakeHeaders(requestId) {
      return {
        'content-type': 'application/json',
        accept: 'application/json',
        'idempotency-key': requestId,
      }
    }

    function newIntakeRequestId() {
      const randomUUID = globalThis.crypto?.randomUUID
      if (typeof randomUUID !== 'function') throw new Error('research_intake_identity_unavailable')
      return `dsh-intake:${randomUUID.call(globalThis.crypto)}`
    }

    function conversationSessionIdOf(props) {
      const direct = typeof props?.sessionId === 'string' ? props.sessionId.trim() : ''
      if (direct.length > 0) return direct
      const nested = typeof props?.session?.sessionId === 'string' ? props.session.sessionId.trim() : ''
      return nested.length > 0 ? nested : null
    }

    function conversationScopeProps(props, owned = {}) {
      return { ...owned, sessionId: conversationSessionIdOf(props) }
    }

    function researchStatusUrl(runId) {
      return `/api/decision-hub/status?run_id=${encodeURIComponent(runId)}`
    }

    function retryCommand(runId) {
      return {
        schema_version: 'research-run-command.v1',
        request_id: `dsh-retry:${runId}`,
        command: 'retry',
        reason: 'Retry failed research from the official DSH Web.',
      }
    }

    function intakeAction(state, runId, managed = false) {
      if (state === 'error' && Boolean(runId)) return 'retry'
      if (['submitting', 'retrying', 'accepted', 'tracking'].includes(state)) return 'disabled'
      if (managed) return 'hidden'
      return 'submit'
    }

    function managedSubmissionOf(payload) {
      if (typeof payload?.run_id !== 'string') return null
      const businessStatus = payload?.business?.status
      const terminal = FINAL_BUSINESS.has(businessStatus)
      const failed = businessStatus === 'failed' || businessStatus === 'rejected'
      const detail = businessDetailOf(payload?.business)
      return {
        state: failed ? 'error' : terminal ? 'complete' : 'tracking',
        label: detail
          ? `已关联研究任务 · ${detail}`
          : terminal ? `已关联研究任务 · ${businessStatus}` : '已关联研究任务 · 研究进行中',
        href: typeof payload?.decision_desk_url === 'string' ? payload.decision_desk_url : null,
        runId: payload.run_id,
        managed: true,
      }
    }

    function managedSessionIdOf(payload, currentSessionId) {
      const managedSessionId = payload?.dsh_session_id
      if (typeof managedSessionId !== 'string' || managedSessionId === currentSessionId) return null
      return managedSessionId
    }

    async function openManagedSessionOf(sessions, payload, currentSessionId) {
      const managedSessionId = managedSessionIdOf(payload, currentSessionId)
      if (managedSessionId === null) return false
      await sessions.refresh()
      if (sessions.binding(managedSessionId) === undefined) {
        throw new Error('managed_session_unavailable')
      }
      sessions.open(managedSessionId)
      return true
    }

function failureLabel(failure) {
  const retry = failure.retryable ? '可重试' : '不可重试'
  const cause = failure.cause_code ? `/${failure.cause_code}` : ''
  return `${failure.capability_id}: ${failure.error_code} (${failure.origin}${cause}, ${retry})`
}

function businessDetailOf(business) {
  if (business === null || business === undefined) return ''
  const failures = Array.isArray(business.failures) ? business.failures : []
  return [
    business.gate_status ? `Gate: ${gateLabels[business.gate_status] || business.gate_status}` : null,
    business.coverage_status ? `${coverageLabels[business.coverage_status] || business.coverage_status} (${Math.round((business.hard_coverage_ratio || 0) * 100)}% hard)` : null,
    business.stop_reason_detail || null,
    ...failures.slice(0, 2).map(failureLabel),
    failures.length > 2 ? `另有 ${failures.length - 2} 个 capability 失败` : null,
  ].filter(Boolean).join(' · ')
}

function hubStatusLabel(link, gate) {
  if (link?.state === null || link?.state === undefined) return 'Hub ready'
  const businessLabel = businessStatusLabels[link?.business?.status]
  if (businessLabel) return `${businessLabel}${gate ? ` · ${gate}` : ''}`
  if (link.state === 'completed') return 'DSH 回合完成'
  if (link.state === 'failed') return 'DSH 回合失败'
  if (link.state === 'cancelled') return 'DSH 回合已取消'
  return 'DSH 回合进行中'
}

function researchReportUrl(runId) {
  return `/api/decision-hub/report?run_id=${encodeURIComponent(runId)}`
}

function reportViewRegistration() {
  return { name: 'conversation.view', id: 'decision-hub-report', order: 20, label: '研究报告' }
}

function inboxViewRegistration() {
  return { name: 'conversation.view', id: 'decision-hub-inbox', order: 10, label: '主动研究' }
}

function inboxItemModelOf(item) {
  if (item === null || typeof item !== 'object' || typeof item.event_id !== 'string') return null
  const sessionId = typeof item.dsh_session_id === 'string' && item.dsh_session_id.trim().length > 0
    ? item.dsh_session_id.trim()
    : null
  const runId = typeof item.run_id === 'string' && item.run_id.trim().length > 0
    ? item.run_id.trim()
    : null
  return {
    eventId: item.event_id,
    title: item.event_title || item.event_id,
    family: item.event_family || '未分类事件',
    status: inboxStatusLabels[item.status] || item.status,
    statusCode: item.status,
    baseline: baselineStatusLabels[item.baseline_status] || item.baseline_status,
    gate: item.gate_status ? gateLabels[item.gate_status] || item.gate_status : '待裁决',
    notification: notificationStatusLabels[item.notification_status] || item.notification_status,
    origin: originLabels[item.admission_origin] || item.admission_origin,
    headline: item.headline || null,
    summary: item.summary || null,
    scheduledAt: item.scheduled_at || null,
    nextRecheckAt: item.next_recheck_at || null,
    updatedAt: item.updated_at || null,
    runId,
    sessionId,
    reportAvailable: item.report_available === true,
    canOpenSession: sessionId !== null,
    isWatchOnly: item.status === 'watching' && runId === null,
  }
}

async function openInboxSessionOf(sessions, item, currentSessionId) {
  const model = inboxItemModelOf(item)
  if (model === null || !model.canOpenSession) return false
  return openManagedSessionOf(sessions, { dsh_session_id: model.sessionId }, currentSessionId)
}

function reportModelOf(link, detail) {
  const run = detail?.run
  if (run === null || run === undefined) return null
  const business = link?.business || null
  const coverage = run.coverage || null
  const evidence = Array.isArray(detail.evidence) ? detail.evidence : []
  const acceptedEvidence = evidence.filter(item => item?.quality === 'accepted' && item?.freshness_status !== 'stale')
  const causal = detail.causal_case || null
  const horizons = Array.isArray(detail.horizons) ? detail.horizons : []
  const gaps = Array.isArray(coverage?.gaps) ? coverage.gaps : []
  const failures = Array.isArray(business?.failures) ? business.failures : []
  return {
    runId: run.run_id,
    title: run.event_title,
    status: run.status,
    origin: originLabels[run.admission_origin] || run.admission_origin,
    priority: priorityLabels[run.priority] || run.priority,
    gate: business?.gate_status ? gateLabels[business.gate_status] || business.gate_status : '待裁决',
    coverage: business?.coverage_status
      ? `${coverageLabels[business.coverage_status] || business.coverage_status} · ${Math.round((business.hard_coverage_ratio || 0) * 100)}% hard`
      : '证据评估中',
    stopReason: business?.stop_reason_detail || run.stop_reason?.detail || null,
    thesis: causal?.thesis || null,
    mainChain: Array.isArray(causal?.main_chain) ? causal.main_chain : [],
    oppositeChain: Array.isArray(causal?.opposite_chain) ? causal.opposite_chain : [],
    horizons,
    evidence: acceptedEvidence,
    evidenceCount: acceptedEvidence.length,
    gaps,
    failures,
    totalToolCalls: Number.isInteger(detail.total_tool_calls) ? detail.total_tool_calls : 0,
    scheduledRecheckAt: detail.scheduled_recheck_at || null,
    href: typeof link?.decision_desk_url === 'string' ? link.decision_desk_url : null,
  }
}

function reportStateOf(link, detail, error) {
  if (link?.run_id === null || link?.run_id === undefined) {
    return error ? 'error' : 'idle'
  }
  if (detail?.run) return 'report'
  const businessStatus = link?.business?.status
  const linkState = link?.state
  const terminal = FINAL_BUSINESS.has(businessStatus) || ['completed', 'failed', 'cancelled'].includes(linkState)
  if (terminal) return error ? 'terminal_error' : 'terminal_pending'
  return error ? 'error' : 'loading'
}

function terminalReportModelOf(link, error) {
  if (link?.run_id === null || link?.run_id === undefined) return null
  const business = link?.business || {}
  const businessStatus = business.status || link?.state || 'unknown'
  return {
    runId: link.run_id,
    title: 'Decision Hub 研究任务',
    status: businessStatus,
    origin: '',
    priority: '',
    gate: business.gate_status ? gateLabels[business.gate_status] || business.gate_status : '待投影',
    coverage: business.coverage_status
      ? `${coverageLabels[business.coverage_status] || business.coverage_status} · ${Math.round((business.hard_coverage_ratio || 0) * 100)}% hard`
      : '详情暂不可用',
    stopReason: business.stop_reason_detail || error || '研究已终止，等待 Hub 详情同步。',
    thesis: null,
    mainChain: [],
    oppositeChain: [],
    horizons: [],
    evidence: [],
    evidenceCount: null,
    gaps: [],
    failures: Array.isArray(business.failures) ? business.failures : [],
    totalToolCalls: 0,
    scheduledRecheckAt: null,
    href: typeof link?.decision_desk_url === 'string' ? link.decision_desk_url : null,
  }
}

function shouldPollStatus(link) {
  const business = link?.business
  return business === null || business === undefined || !FINAL_BUSINESS.has(business.status)
}

function shouldPollReport(link, detail, error) {
  const phase = reportStateOf(link, detail, error)
  if (phase === 'terminal_pending' || phase === 'terminal_error') return false
  return shouldPollStatus(link)
}

function HubStatusUtility(props) {
  const sessionId = conversationSessionIdOf(props)
      const [status, setStatus] = useState({ kind: 'loading', label: 'Hub', detail: '', business: null, runtimeMode: null })

  useEffect(() => {
    let cancelled = false
    let timer = null
    let activeController = null
    const schedule = (link) => {
      if (cancelled || !shouldPollStatus(link)) return
      timer = setTimeout(() => {
        timer = null
        void load()
      }, STATUS_POLL_MS)
    }
    const load = async () => {
      const controller = new AbortController()
      activeController = controller
      let link = null
      try {
        const query = sessionId === null ? '' : `?session_id=${encodeURIComponent(sessionId)}`
        const response = await fetch(`/api/decision-hub/status${query}`, {
          signal: controller.signal,
          headers: { accept: 'application/json' },
        })
        link = await response.json()
        if (!response.ok || link.ready !== true) {
              if (!cancelled) setStatus({ kind: 'degraded', label: runtimeModeLabel(link.runtime_mode), detail: link.error_code || '', href: link.decision_desk_url || null, business: link.business || null, runtimeMode: link.runtime_mode || null })
          schedule(link)
          return
        }
        if (!cancelled) {
          const business = link.business || null
          const gate = business?.gate_status ? gateLabels[business.gate_status] || business.gate_status : null
          const coverage = business?.coverage_status ? coverageLabels[business.coverage_status] || business.coverage_status : null
              setStatus({
                kind: link.state === null ? 'ready' : link.state === 'completed' ? 'complete' : link.state === 'failed' ? 'failed' : 'running',
                label: link.runtime_mode === 'replay'
                  ? runtimeModeLabel(link.runtime_mode)
                  : hubStatusLabel(link, gate),
                detail: typeof link.run_id === 'string' ? link.run_id : '',
                href: typeof link.decision_desk_url === 'string' ? link.decision_desk_url : null,
                business,
                coverage,
                runtimeMode: link.runtime_mode || null,
              })
        }
      } catch (error) {
        if (cancelled || error?.name === 'AbortError') return
        setStatus(EMPTY)
      } finally {
        if (timer === null) schedule(link)
      }
    }
    void load()
    return () => {
      cancelled = true
      if (timer !== null) clearTimeout(timer)
      if (activeController !== null) activeController.abort()
    }
  }, [sessionId])

  const href = status.href || null
      const business = status.business
      const businessDetail = businessDetailOf(business)
      const notice = runtimeNotice(status.runtimeMode)
      return createElement(
    'a',
    {
      ...(href === null ? {} : { href }),
      target: '_blank',
      rel: 'noreferrer',
          title: notice || status.detail || status.label,
      'data-decision-hub-status': status.kind,
      style: {
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        color: 'inherit',
        textDecoration: 'none',
        fontSize: '12px',
        opacity: status.kind === 'degraded' || status.kind === 'unknown' ? 0.7 : 1,
      },
    },
    createElement('span', { 'aria-hidden': 'true' }, '●'),
    createElement('span', { style: { display: 'inline-flex', flexDirection: 'column', gap: '2px' } },
      createElement('span', null, status.label),
      businessDetail ? createElement('span', { style: { fontSize: '11px', opacity: 0.75, maxWidth: 'min(720px, 70vw)', overflowWrap: 'anywhere', lineHeight: 1.35 }, title: businessDetail }, businessDetail) : null,
    ),
      )
    }

function ResearchReportCard(props) {
  const viewSurface = props?.surface === 'view'
  const sessionId = conversationSessionIdOf(props)
  const [state, setState] = useState({ link: null, detail: null, error: null })

  useEffect(() => {
    let cancelled = false
    let timer = null
    let controller = null
    const load = async () => {
      controller = new AbortController()
      let link = null
      let detail = null
      let loadError = null
      try {
        const query = sessionId === null ? '' : `?session_id=${encodeURIComponent(sessionId)}`
        const statusResponse = await fetch(`/api/decision-hub/status${query}`, {
          signal: controller.signal, headers: { accept: 'application/json' },
        })
        link = await statusResponse.json()
        if (!statusResponse.ok || typeof link?.run_id !== 'string') {
          loadError = statusResponse.ok ? null : link?.error_code || 'report_status_unavailable'
          if (!cancelled) setState({
            link, detail: null,
            error: loadError,
          })
        } else {
          const reportResponse = await fetch(researchReportUrl(link.run_id), {
            signal: controller.signal, headers: { accept: 'application/json' },
          })
          const reportBody = await reportResponse.json()
          if (reportResponse.ok) detail = reportBody
          loadError = reportResponse.ok ? null : reportBody?.error?.code || 'research_report_unavailable'
          if (!cancelled) setState({
            link, detail,
            error: loadError,
          })
        }
      } catch (error) {
        if (!cancelled && error?.name !== 'AbortError') {
          loadError = 'research_report_unavailable'
          setState(previous => ({ ...previous, error: 'research_report_unavailable' }))
        }
      }
      const keepPolling = shouldPollReport(link, detail, loadError)
      if (!cancelled && keepPolling) timer = setTimeout(() => { void load() }, STATUS_POLL_MS)
    }
    void load()
    return () => {
      cancelled = true
      if (timer !== null) clearTimeout(timer)
      if (controller !== null) controller.abort()
    }
  }, [sessionId])

  const model = reportModelOf(state.link, state.detail)
  const reportState = reportStateOf(state.link, state.detail, state.error)
  if (model === null) {
    if (reportState === 'idle') {
      if (!viewSurface) return null
      return createElement('section', {
        'data-decision-hub-report': 'idle',
        'aria-label': 'Decision Hub 研究报告',
        style: viewReportShellStyle,
      },
      createElement('span', { style: reportEyebrowStyle }, '研究报告'),
      createElement('p', {
        style: { margin: '6px 0 0', fontSize: '13px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)' },
      }, '当前会话尚未关联正式研究任务'))
    }
    const terminalModel = reportState === 'terminal_pending' || reportState === 'terminal_error'
      ? terminalReportModelOf(state.link, state.error)
      : null
    if (terminalModel !== null) {
      return createElement('section', {
        'data-decision-hub-report': reportState,
        'aria-label': 'Decision Hub 研究报告',
        style: viewSurface ? viewReportShellStyle : reportShellStyle,
      },
      createElement('span', { style: reportEyebrowStyle }, '研究报告'),
      createElement('p', { style: { margin: '5px 0 0', fontSize: '13px', lineHeight: 1.5 } },
        terminalModel.stopReason),
      createElement('div', { style: { marginTop: '7px', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)' } },
        `${terminalModel.coverage} · ${terminalModel.gate}`),
      terminalModel.failures.length > 0 ? createElement('div', {
        role: 'alert', style: { marginTop: '7px', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-status-error)' },
      }, ...terminalModel.failures.map(failure => createElement('div', {
        key: `${failure.capability_id}:${failure.error_code}`,
      }, failureLabel(failure)))) : null,
      terminalModel.href ? createElement('a', {
        href: terminalModel.href, target: '_blank', rel: 'noreferrer', style: { ...reportLinkStyle, display: 'inline-block', marginTop: '7px' },
      }, '打开完整审计') : null)
    }
    return createElement('section', {
      'data-decision-hub-report': reportState === 'error' ? 'error' : 'loading',
      'aria-label': 'Decision Hub 研究报告',
      style: viewSurface ? viewReportShellStyle : reportShellStyle,
    }, createElement('span', {
      style: { fontSize: '12px', color: 'var(--dsw-alias-label-secondary)' },
    }, state.error ? `研究报告不可用 · ${state.error}` : '研究报告生成中'))
  }

  return createElement('section', {
    'data-decision-hub-report': model.status,
    'aria-label': 'Decision Hub 研究报告',
    style: viewSurface ? viewReportShellStyle : reportShellStyle,
  },
  createElement('header', {
    style: { display: 'flex', gap: '12px', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap' },
  },
  createElement('div', { style: { minWidth: 0, flex: '1 1 320px' } },
    createElement('span', { style: reportEyebrowStyle }, '研究报告'),
    createElement('h2', {
      style: { margin: '3px 0 0', fontSize: '15px', lineHeight: 1.45, fontWeight: 650, overflowWrap: 'anywhere' },
    }, model.title),
  ),
  createElement('div', {
    style: { display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' },
  },
  reportBadge(model.gate), reportBadge(model.coverage),
  model.href ? createElement('a', {
    href: model.href, target: '_blank', rel: 'noreferrer', style: reportLinkStyle,
  }, '完整审计') : null,
  )),
  model.thesis ? createElement('p', {
    style: { margin: '10px 0 0', fontSize: '13px', lineHeight: 1.55, fontWeight: 550 },
  }, model.thesis) : null,
  model.stopReason ? createElement('p', {
    style: { margin: '7px 0 0', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-label-secondary)' },
  }, model.stopReason) : null,
  createElement('div', {
    style: { marginTop: '10px', display: 'flex', gap: '12px', flexWrap: 'wrap', fontSize: '11px', color: 'var(--dsw-alias-label-secondary)' },
  },
  createElement('span', null, model.evidenceCount === null ? '证据详情暂不可用' : `${model.evidenceCount} 条有效证据`),
  createElement('span', null, `${model.totalToolCalls} 次工具调用`),
  createElement('span', null, `${model.origin} · ${model.priority}`),
  model.scheduledRecheckAt ? createElement('span', null,
    `复查 ${new Date(model.scheduledRecheckAt).toLocaleString('zh-CN', { hour12: false })}`) : null,
  ),
  model.horizons.length > 0 ? createElement('div', {
    style: { display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '10px' },
  }, ...model.horizons.map(horizon => createElement('article', {
    key: horizon.horizon,
    style: {
      flex: '1 1 190px', minWidth: 0, border: '1px solid var(--dsw-alias-border-normal)',
      borderRadius: '6px', padding: '9px 10px', boxSizing: 'border-box',
    },
  },
  createElement('div', {
    style: { display: 'flex', justifyContent: 'space-between', gap: '8px', alignItems: 'baseline' },
  },
  createElement('strong', { style: { fontSize: '12px' } }, horizon.horizon),
  createElement('span', { style: { fontSize: '12px', fontWeight: 650 } },
    `${actionLabels[horizon.action] || horizon.action} · ${Math.round((horizon.subjective_probability || 0) * 100)}%`),
  ),
  createElement('p', { style: reportCompactTextStyle }, `触发：${horizon.trigger}`),
  createElement('p', { style: reportCompactTextStyle }, `失效：${horizon.invalidation}`),
  ))) : null,
  model.failures.length > 0 ? createElement('div', {
    role: 'alert', style: { marginTop: '10px', fontSize: '12px', lineHeight: 1.5, color: 'var(--dsw-alias-status-error)' },
  }, ...model.failures.map(failure => createElement('div', {
    key: `${failure.capability_id}:${failure.error_code}`,
  }, failureLabel(failure)))) : null,
  model.gaps.length > 0 ? createElement('details', { style: reportDetailsStyle },
    createElement('summary', null, `证据缺口（${model.gaps.length}）`),
    createElement('ul', { style: reportListStyle }, ...model.gaps.map(gap => createElement('li', {
      key: gap.requirement_id,
    }, `${gap.requirement_id} · ${gap.reason_code} · ${gap.query_hint}`))),
  ) : null,
  model.mainChain.length > 0 || model.oppositeChain.length > 0 ? createElement('details', {
    style: reportDetailsStyle,
  },
  createElement('summary', null, '主因果链与反向假设'),
  createElement('div', { style: { marginTop: '6px', display: 'grid', gap: '7px' } },
    ...model.mainChain.map((link, index) => createElement('p', {
      key: link.link_id, style: reportCompactTextStyle,
    }, `主链 ${index + 1}：${link.statement}`)),
    ...model.oppositeChain.map((link, index) => createElement('p', {
      key: link.link_id, style: reportCompactTextStyle,
    }, `反链 ${index + 1}：${link.statement}`)),
  )) : null,
  model.evidence.length > 0 ? createElement('details', { style: reportDetailsStyle },
    createElement('summary', null, `关键事实（${model.evidence.length}）`),
    createElement('ul', { style: reportListStyle }, ...model.evidence.map(item => createElement('li', {
      key: item.evidence_id,
    },
    createElement('strong', null, `${item.requirement_id} · ${item.authority} · ${item.source_id}`),
    createElement('span', { style: { display: 'block', marginTop: '2px' } }, item.excerpt),
    ))),
  ) : null)
}

function ResearchReportView(props) {
  return createElement('main', {
    'data-decision-hub-report-view': 'canonical',
    style: {
      width: '100%', minWidth: 0, boxSizing: 'border-box', padding: '20px clamp(12px, 3vw, 32px)',
      overflowY: 'auto', color: 'inherit',
    },
  }, createElement(ResearchReportCard, conversationScopeProps(props, { surface: 'view' })))
}

function formatInboxTime(value) {
  if (typeof value !== 'string' || value.length === 0) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function ResearchInboxView(props) {
  const currentSessionId = conversationSessionIdOf(props)
  const [state, setState] = useState({ phase: 'loading', items: [], error: null })
  const [opening, setOpening] = useState(null)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    fetch('/api/decision-hub/inbox?limit=100', {
      signal: controller.signal, headers: { accept: 'application/json' },
    })
      .then(async response => {
        const payload = await response.json()
        if (!response.ok) throw new Error(payload?.error?.code || 'research_inbox_unavailable')
        return payload
      })
      .then(payload => {
        if (cancelled) return
        const items = Array.isArray(payload?.items)
          ? payload.items.map(inboxItemModelOf).filter(Boolean)
          : []
        setState({ phase: 'ready', items, error: null })
      })
      .catch(error => {
        if (cancelled || error?.name === 'AbortError') return
        setState({ phase: 'error', items: [], error: error?.message || 'research_inbox_unavailable' })
      })
    return () => { cancelled = true; controller.abort() }
  }, [])

  const openSession = async model => {
    if (!model.canOpenSession || typeof props.openInboxSession !== 'function') return
    setOpening(model.eventId)
    try {
      await props.openInboxSession({ dsh_session_id: model.sessionId }, currentSessionId)
    } catch {
      setState(previous => ({ ...previous, error: '受管 Session 暂不可用，请稍后重试' }))
    } finally {
      setOpening(null)
    }
  }

  return createElement('main', {
    'data-decision-hub-inbox-view': state.phase,
    style: {
      width: '100%', minWidth: 0, boxSizing: 'border-box', padding: '20px clamp(12px, 3vw, 32px)',
      overflowY: 'auto', color: 'inherit',
    },
  }, createElement('section', { style: inboxShellStyle },
  createElement('header', {
    style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap' },
  },
  createElement('div', null,
    createElement('span', { style: reportEyebrowStyle }, '主动研究'),
    createElement('h2', { style: { margin: '3px 0 0', fontSize: '16px', lineHeight: 1.4, fontWeight: 650 } },
      '事件观察与研究收件箱'),
  ),
  createElement('span', { style: { fontSize: '11px', color: 'var(--dsw-alias-label-secondary)' } },
    state.phase === 'ready' ? String(state.items.length) + ' 项' : '同步中'),
  ),
  state.error ? createElement('p', {
    role: 'alert', style: { margin: '10px 0 0', color: 'var(--dsw-alias-status-error)', fontSize: '12px' },
  }, state.error) : null,
  state.phase === 'loading' ? createElement('p', { style: inboxEmptyStyle }, '正在读取主动研究任务') : null,
  state.phase === 'ready' && state.items.length === 0
    ? createElement('p', { style: inboxEmptyStyle }, '暂无主动研究')
    : null,
  state.items.length > 0 ? createElement('div', { style: inboxListStyle }, ...state.items.map(model =>
    createElement('article', {
      key: model.eventId + ':' + (model.runId || 'watch'),
      'data-decision-hub-inbox-status': model.statusCode,
      style: inboxItemStyle,
    },
    createElement('header', {
      style: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '10px', flexWrap: 'wrap' },
    },
    createElement('div', { style: { minWidth: 0, flex: '1 1 320px' } },
      createElement('span', { style: reportEyebrowStyle }, model.family + ' · ' + model.origin),
      createElement('h3', {
        style: { margin: '3px 0 0', fontSize: '14px', lineHeight: 1.45, fontWeight: 650, overflowWrap: 'anywhere' },
      }, model.title),
    ),
    createElement('div', { style: { display: 'flex', gap: '5px', flexWrap: 'wrap' } },
      reportBadge(model.status), reportBadge(model.gate)),
    ),
    model.headline ? createElement('p', { style: { margin: '9px 0 0', fontSize: '13px', lineHeight: 1.5, fontWeight: 550 } }, model.headline) : null,
    model.summary ? createElement('p', { style: { ...reportCompactTextStyle, color: 'var(--dsw-alias-label-secondary)' } }, model.summary) : null,
    createElement('div', { style: inboxMetaStyle },
      createElement('span', null, model.baseline),
      createElement('span', null, model.notification),
      model.scheduledAt ? createElement('span', null, '计划 ' + formatInboxTime(model.scheduledAt)) : null,
      model.nextRecheckAt ? createElement('span', null, '复查 ' + formatInboxTime(model.nextRecheckAt)) : null,
      model.updatedAt ? createElement('span', null, '更新 ' + formatInboxTime(model.updatedAt)) : null,
    ),
    createElement('div', { style: { marginTop: '10px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' } },
      model.canOpenSession ? createElement('button', {
        type: 'button', disabled: opening === model.eventId,
        onClick: () => { void openSession(model) },
        style: inboxActionStyle,
      }, opening === model.eventId ? '正在打开...' : '打开研究会话') : null,
      model.isWatchOnly ? createElement('span', { style: { fontSize: '11px', color: 'var(--dsw-alias-label-secondary)' } },
        '尚未触发研究，不创建空会话') : null,
      model.reportAvailable && !model.canOpenSession ? createElement('span', { style: { fontSize: '11px', color: 'var(--dsw-alias-label-secondary)' } },
        '报告已记录，等待会话关联') : null,
    )))
  ) : null))
}

const reportShellStyle = {
  width: '100%', maxWidth: 'var(--dsh-composer-card-max-width)', boxSizing: 'border-box',
  marginBottom: '8px', padding: '11px 12px', border: '1px solid var(--dsw-alias-border-normal)',
  borderRadius: '8px', background: 'var(--dsw-alias-bg-base)', color: 'inherit',
}
const viewReportShellStyle = {
  ...reportShellStyle, maxWidth: '1040px', margin: '0 auto', padding: '16px',
}
const inboxShellStyle = {
  width: '100%', maxWidth: '1040px', margin: '0 auto', boxSizing: 'border-box',
  border: '1px solid var(--dsw-alias-border-normal)', borderRadius: '8px',
  background: 'var(--dsw-alias-bg-base)', padding: '16px', color: 'inherit',
}
const inboxListStyle = { marginTop: '14px', display: 'grid', gap: '8px' }
const inboxItemStyle = {
  minWidth: 0, boxSizing: 'border-box', borderTop: '1px solid var(--dsw-alias-border-normal)', padding: '12px 0 2px',
}
const inboxMetaStyle = {
  marginTop: '9px', display: 'flex', gap: '10px', flexWrap: 'wrap', fontSize: '11px',
  color: 'var(--dsw-alias-label-secondary)',
}
const inboxEmptyStyle = { margin: '16px 0 0', fontSize: '13px', color: 'var(--dsw-alias-label-secondary)' }
const inboxActionStyle = {
  minHeight: '32px', padding: '6px 10px', borderRadius: '6px', border: '1px solid var(--dsw-alias-border-normal)',
  background: 'var(--dsw-alias-interactive-bg-default)', color: 'inherit', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
}
const reportEyebrowStyle = { fontSize: '11px', fontWeight: 650, color: 'var(--dsw-alias-label-secondary)' }
const reportCompactTextStyle = { margin: '6px 0 0', fontSize: '11px', lineHeight: 1.45, overflowWrap: 'anywhere' }
const reportDetailsStyle = { marginTop: '9px', fontSize: '12px', lineHeight: 1.45 }
const reportListStyle = { margin: '6px 0 0', paddingLeft: '18px', display: 'grid', gap: '6px' }
const reportLinkStyle = { color: 'inherit', fontSize: '11px', textDecoration: 'underline', textUnderlineOffset: '2px' }

function reportBadge(label) {
  return createElement('span', {
    style: {
      padding: '3px 6px', borderRadius: '4px',
      background: 'var(--dsw-alias-interactive-bg-hover)', fontSize: '11px', lineHeight: 1.35,
    },
  }, label)
}

    function ReplayInteractionGuard(props) {
      const sessionId = conversationSessionIdOf(props)
      const [mode, setMode] = useState(null)
      useEffect(() => {
        let cancelled = false
        fetch('/api/decision-hub/status', { headers: { accept: 'application/json' } })
          .then(response => response.json())
          .then(payload => {
            if (!cancelled) setMode(payload?.runtime_mode === 'replay' ? 'replay' : 'live')
          })
          .catch(() => { if (!cancelled) setMode('live') })
        return () => { cancelled = true }
      }, [sessionId])

      useEffect(() => {
        if (mode !== 'replay') return undefined
        const card = document.querySelector('[data-composer-card]')
        if (card === null) return undefined
        const sync = () => {
          const input = card.querySelector('[data-composer-input]')
          if (input !== null) {
            input.setAttribute('contenteditable', 'false')
            input.setAttribute('aria-disabled', 'true')
            input.setAttribute('data-decision-hub-replay-locked', 'true')
          }
          const send = card.querySelector('[aria-label="发送消息"]')
          if (send !== null) {
            send.setAttribute('disabled', 'true')
            send.setAttribute('data-decision-hub-replay-locked', 'true')
          }
        }
        const prevent = event => {
          if (!protectedComposerTarget(event.target)) return
          event.preventDefault()
          event.stopPropagation()
        }
        sync()
        card.addEventListener('beforeinput', prevent, true)
        card.addEventListener('keydown', prevent, true)
        card.addEventListener('click', prevent, true)
        card.addEventListener('pointerdown', prevent, true)
        const observer = new MutationObserver(sync)
        observer.observe(card, { childList: true, subtree: true, attributes: true, attributeFilter: ['contenteditable', 'disabled', 'aria-disabled'] })
        return () => {
          card.removeEventListener('beforeinput', prevent, true)
          card.removeEventListener('keydown', prevent, true)
          card.removeEventListener('click', prevent, true)
          card.removeEventListener('pointerdown', prevent, true)
          observer.disconnect()
          card.querySelectorAll('[data-decision-hub-replay-locked]').forEach(element => {
            element.removeAttribute('data-decision-hub-replay-locked')
            if (element.matches('[data-composer-input]')) {
              element.removeAttribute('aria-disabled')
              element.setAttribute('contenteditable', 'false')
            }
            if (element.matches('[aria-label="发送消息"]')) element.removeAttribute('disabled')
          })
        }
      }, [mode, sessionId])

      if (mode !== 'replay') return null
      return createElement('div', {
        role: 'status',
        'data-decision-hub-runtime-notice': 'replay',
        style: {
          width: '100%', maxWidth: 'var(--dsh-composer-card-max-width)', boxSizing: 'border-box',
          marginBottom: '6px', padding: '7px 10px', borderRadius: '8px',
          background: 'var(--dsw-alias-interactive-bg-hover)', color: 'var(--dsw-alias-label-secondary)',
          fontSize: '12px', lineHeight: '18px',
        },
      }, runtimeNotice(mode))
    }

    function ResearchIntakeControl(props) {
      const sessionId = conversationSessionIdOf(props)
      const [mode, setMode] = useState(null)
      const [submission, setSubmission] = useState({ state: 'idle', label: '', href: null, runId: null, managed: false })
      const intakeIdentity = useRef({ text: null, requestId: null })

      useEffect(() => {
        let cancelled = false
        const query = sessionId === null ? '' : `?session_id=${encodeURIComponent(sessionId)}`
        fetch(`/api/decision-hub/status${query}`, { headers: { accept: 'application/json' } })
          .then(response => response.json())
          .then(payload => {
            if (cancelled) return
            setMode(payload?.runtime_mode === 'replay' ? 'replay' : 'live')
            const managed = managedSubmissionOf(payload)
            if (managed !== null) setSubmission(managed)
          })
          .catch(() => { if (!cancelled) setMode('unavailable') })
        return () => { cancelled = true }
      }, [sessionId])

      useEffect(() => {
        const runId = submission.runId
        if (!runId) return undefined
        let cancelled = false
        let timer = null
        const poll = async () => {
          if (cancelled) return
          try {
            const response = await fetch(researchStatusUrl(runId), { headers: { accept: 'application/json' } })
            const payload = await response.json()
            if (!cancelled && response.ok) {
              if (typeof props.openManagedSession === 'function') {
                try {
                  const opened = await props.openManagedSession(payload, sessionId)
                  if (opened) {
                    cancelled = true
                    return
                  }
                } catch {
                  setSubmission(previous => ({
                    ...previous,
                    state: 'tracking',
                    label: '研究进行中 · 受管 Session 暂未同步，正在重试',
                    managed: true,
                  }))
                }
              }
              const business = payload?.business
              const businessStatus = business?.status
              const terminal = ['completed', 'degraded', 'research_only', 'rejected', 'failed', 'cancelled'].includes(businessStatus)
              if (terminal) {
                const detail = businessDetailOf(business)
                setSubmission(previous => ({
                  ...previous,
                  state: businessStatus === 'failed' || businessStatus === 'rejected' ? 'error' : 'complete',
                  label: detail ? `研究${businessStatus === 'failed' || businessStatus === 'rejected' ? '停止' : '完成'} · ${detail}` : `研究状态 · ${businessStatus}`,
                  href: typeof payload.decision_desk_url === 'string' ? payload.decision_desk_url : previous.href,
                  managed: true,
                }))
                return
              }
              setSubmission(previous => ({
                ...previous,
                state: 'tracking',
                label: businessStatus === 'researching' ? '研究进行中 · DSH 正在补证' : '研究任务已排队',
                href: typeof payload.decision_desk_url === 'string' ? payload.decision_desk_url : previous.href,
                managed: true,
              }))
            }
          } catch {
            // Keep the last durable status visible; the next bounded poll may recover.
          }
          if (!cancelled) timer = setTimeout(() => { void poll() }, STATUS_POLL_MS)
        }
        void poll()
        return () => { cancelled = true; if (timer !== null) clearTimeout(timer) }
      }, [submission.runId])

      const submit = async () => {
        const text = composerText(document)
        if (text.length === 0) {
          setSubmission({ state: 'error', label: '请输入研究目标', href: null, runId: null, managed: false })
          return
        }
        const requestId = intakeIdentity.current.text === text
          && typeof intakeIdentity.current.requestId === 'string'
          ? intakeIdentity.current.requestId
          : newIntakeRequestId()
        intakeIdentity.current = { text, requestId }
        setSubmission({ state: 'submitting', label: '正在建立研究任务', href: null, runId: null, managed: false })
        try {
          const response = await fetch('/api/decision-hub/research', {
            method: 'POST',
            headers: intakeHeaders(requestId),
            body: JSON.stringify(intakePayload(text, sessionId)),
          })
          const body = await response.json()
          if (!response.ok) throw new Error(body?.error?.code || `research_intake_http_${response.status}`)
          setSubmission({
            state: 'accepted',
            label: `研究任务已排队 · ${body.run_id}`,
            href: typeof body.decision_desk_url === 'string' ? body.decision_desk_url : null,
            runId: typeof body.run_id === 'string' ? body.run_id : null,
            managed: true,
          })
        } catch (error) {
          setSubmission({ state: 'error', label: error?.message || 'research_intake_failed', href: null, runId: null, managed: false })
        }
      }

      const retry = async () => {
        const sourceRunId = submission.runId
        if (!sourceRunId) return
        setSubmission(previous => ({ ...previous, state: 'retrying', label: '正在建立重试任务' }))
        try {
          const response = await fetch('/api/decision-hub/research/retry', {
            method: 'POST',
            headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify(retryCommand(sourceRunId)),
          })
          const body = await response.json()
          if (!response.ok) throw new Error(body?.error?.code || `research_retry_http_${response.status}`)
          if (body?.status === 'rejected' || typeof body?.target_run_id !== 'string') {
            throw new Error('research_retry_rejected')
          }
          setSubmission({
            state: 'accepted',
            label: `重试任务已排队 · ${body.target_run_id}`,
            href: null,
            runId: body.target_run_id,
            managed: true,
          })
        } catch (error) {
          setSubmission(previous => ({ ...previous, state: 'error', label: error?.message || 'research_retry_failed' }))
        }
      }

      const action = intakeAction(submission.state, submission.runId, submission.managed)
      const isRetry = action === 'retry'
      const isBusy = action === 'disabled'
      const buttonLabel = submission.state === 'accepted' || submission.state === 'tracking'
        ? '研究中'
        : isBusy ? '建立中...' : isRetry ? '重新研究' : '建立研究任务'

      useEffect(() => {
        if (mode !== 'live' || action !== 'submit' || submission.managed) return undefined
        const card = document.querySelector('[data-composer-card]')
        if (card === null) return undefined
        const intercept = event => {
          if (!researchComposerSubmitIntent(event)) return
          event.preventDefault()
          event.stopPropagation()
          if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation()
          void submit()
        }
        card.addEventListener('click', intercept, true)
        card.addEventListener('keydown', intercept, true)
        return () => {
          card.removeEventListener('click', intercept, true)
          card.removeEventListener('keydown', intercept, true)
        }
      }, [mode, action, submission.managed, sessionId])

      if (mode !== 'live' || action === 'hidden') return null
      return createElement('div', {
        'data-decision-hub-research-intake': submission.state,
        style: {
          width: '100%', maxWidth: 'var(--dsh-composer-card-max-width)', boxSizing: 'border-box',
          marginBottom: '6px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap',
        },
      },
      createElement('button', {
        type: 'button',
        disabled: isBusy,
        onClick: () => { void (isRetry ? retry() : submit()) },
        'aria-label': buttonLabel,
        style: {
          minHeight: '32px', padding: '6px 10px', borderRadius: '6px',
          border: '1px solid var(--dsw-alias-border-normal)',
          background: 'var(--dsw-alias-interactive-bg-default)', color: 'inherit',
          fontSize: '12px', fontWeight: 600, cursor: isBusy ? 'wait' : 'pointer',
        },
      }, buttonLabel),
      submission.label ? createElement(submission.href ? 'a' : 'span', {
        ...(submission.href ? { href: submission.href, target: '_blank', rel: 'noreferrer' } : {}),
        role: 'status',
        style: {
          color: submission.state === 'error' ? 'var(--dsw-alias-status-error)' : 'var(--dsw-alias-label-secondary)',
          fontSize: '12px', lineHeight: '18px', overflowWrap: 'anywhere',
        },
      }, submission.label) : null)
    }

module.exports = {
  inject: ['slots', 'sessions', 'workspaces'],
      __test: { PRODUCT_WORKSPACE_TITLE, productWorkspaceOf, latestProductSessionOf, autoSelectProductWorkspace, businessDetailOf, failureLabel, hubStatusLabel, shouldPollStatus, shouldPollReport, runtimeModeLabel, runtimeNotice, protectedComposerTarget, researchComposerSubmitIntent, composerText, intakePayload, intakeHeaders, researchStatusUrl, researchReportUrl, reportModelOf, reportStateOf, terminalReportModelOf, reportViewRegistration, inboxViewRegistration, inboxItemModelOf, openInboxSessionOf, retryCommand, intakeAction, managedSubmissionOf, managedSessionIdOf, openManagedSessionOf, conversationSessionIdOf, conversationScopeProps },
      apply(ctx) {
        const sessions = ctx.get('sessions')
        const workspaces = ctx.get('workspaces')
        const openManagedSession = (payload, currentSessionId) => (
          openManagedSessionOf(sessions, payload, currentSessionId)
        )
        const openInboxSession = (payload, currentSessionId) => (
          openManagedSessionOf(sessions, payload, currentSessionId)
        )
        ctx.effect(
          () => autoSelectProductWorkspace(sessions, workspaces),
          'decision-hub.product-entry-selection',
        )
        ctx.slots.inject('conversation.session.header.utilities', () => ctx.slots.register({
      name: 'conversation.session.header.utilities',
      id: 'decision-hub-status',
          order: 20,
        }, HubStatusUtility))
        ctx.slots.inject('conversation.view', () => ctx.slots.register(
          reportViewRegistration(), ResearchReportView))
        ctx.slots.inject('conversation.view', () => ctx.slots.register(
          inboxViewRegistration(), props => createElement(ResearchInboxView, conversationScopeProps(props, { openInboxSession }))))
        ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
          name: 'conversation.input.dock',
          id: 'decision-hub-runtime-notice',
          order: 0,
        }, ReplayInteractionGuard))
        ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
          name: 'conversation.input.dock',
          id: 'decision-hub-research-intake',
          order: 10,
        }, props => createElement(ResearchIntakeControl, conversationScopeProps(props, { openManagedSession }))))
      },
}
