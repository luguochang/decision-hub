import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function loadClientTestSurface(): {
  businessDetailOf: (value: unknown) => string
  hubStatusLabel: (link: unknown, gate: string | null) => string
  shouldPollStatus: (value: unknown, attempt: number) => boolean
  runtimeModeLabel: (value: unknown) => string
  runtimeNotice: (value: unknown) => string
  protectedComposerTarget: (value: unknown) => boolean
  composerText: (value: unknown) => string
  intakePayload: (text: string, sessionId: string | null) => unknown
  intakeHeaders: (requestId: string) => Record<string, string>
  researchStatusUrl: (runId: string) => string
  researchReportUrl: (runId: string) => string
  reportModelOf: (link: unknown, detail: unknown) => any
  reportStateOf: (link: unknown, detail: unknown, error: unknown) => string
  shouldPollReport: (link: unknown, detail: unknown, error: unknown) => boolean
  terminalReportModelOf: (link: unknown, error: unknown) => any
  reportViewRegistration: () => { name: string; id: string; order: number; label: string }
  inboxViewRegistration: () => { name: string; id: string; order: number; label: string }
  inboxItemModelOf: (value: unknown) => any
  openInboxSessionOf: (sessions: unknown, value: unknown, currentSessionId: string | null) => Promise<boolean>
  retryCommand: (runId: string) => unknown
  intakeAction: (state: string, runId: string | null, managed?: boolean) => string
  managedSubmissionOf: (value: unknown) => any
  managedSessionIdOf: (value: unknown, currentSessionId: string | null) => string | null
  openManagedSessionOf: (sessions: unknown, value: unknown, currentSessionId: string | null) => Promise<boolean>
  conversationSessionIdOf: (props: unknown) => string | null
  conversationScopeProps: (props: unknown, owned?: Record<string, unknown>) => Record<string, unknown>
  productWorkspaceOf: (value: unknown) => any
  latestProductSessionOf: (workspace: unknown, snapshot: unknown, archived?: unknown) => string | null
  autoSelectProductWorkspace: (sessions: unknown, workspaces: unknown) => () => void
} {
  const source = readFileSync(resolve(process.cwd(), 'src/client/index.js'), 'utf8')
  const module = { exports: {} as Record<string, unknown> }
  const fakeReact = {
    createElement: () => null,
    useEffect: () => undefined,
    useRef: (value: unknown) => ({ current: value }),
    useState: (value: unknown) => [value, () => undefined],
  }
  Function('require', 'module', 'exports', source)(
    (name: string) => { if (name === 'react') return fakeReact; throw new Error(`unexpected require: ${name}`) },
    module,
    module.exports,
  )
  return (module.exports.__test as ReturnType<typeof loadClientTestSurface>)
}

describe('Decision Hub DSH Client projection', () => {
  it('finds the product workspace by its durable title or path basename', () => {
    const { productWorkspaceOf } = loadClientTestSurface()
    const workspace = { workspaceId: 'workspace-trader', title: 'Crypto Macro Trader', path: '/tmp/trader' }
    expect(productWorkspaceOf({ phase: 'ready', items: [workspace] })).toEqual(workspace)
    expect(productWorkspaceOf({ phase: 'ready', items: [{ workspaceId: 'workspace-trader', title: 'Other', path: '/tmp/Crypto Macro Trader' }] }))
      .toMatchObject({ workspaceId: 'workspace-trader' })
    expect(productWorkspaceOf({ phase: 'ready', items: [{ title: 'Codex', path: '/tmp/Codex' }] })).toBeNull()
  })

  it('opens the latest non-blank product session and avoids creating duplicates', () => {
    const { latestProductSessionOf } = loadClientTestSurface()
    const workspace = { workspaceId: 'workspace-trader', sessionIds: ['blank', 'old', 'new'] }
    const snapshot = {
      byId: {
        blank: { id: 'blank', blank: true, updatedAt: 999 },
        old: { id: 'old', blank: false, updatedAt: 100 },
        new: { id: 'new', blank: false, updatedAt: 200 },
      },
    }
    expect(latestProductSessionOf(workspace, snapshot, [])).toBe('new')
    expect(latestProductSessionOf({ ...workspace, sessionIds: ['blank'] }, snapshot, [])).toBe('blank')
    expect(latestProductSessionOf(workspace, snapshot, ['new'])).toBe('old')
  })

  it('selects the product session once after both official snapshots are ready', async () => {
    const { autoSelectProductWorkspace } = loadClientTestSurface()
    const calls: string[] = []
    const workspaceState = {
      phase: 'ready',
      archivedSessionIds: [],
      items: [{ workspaceId: 'workspace-trader', title: 'Crypto Macro Trader', path: '/tmp/trader', sessionIds: ['run-session'] }],
    }
    const sessionState = {
      phase: 'ready', current: 'codex-session',
      byId: { 'run-session': { id: 'run-session', blank: false, updatedAt: 100 } },
    }
    const workspaces = { list: { getSnapshot: () => workspaceState, subscribe: (fn: () => void) => { fn(); return () => undefined } } }
    const sessions = {
      list: { getSnapshot: () => sessionState, subscribe: (fn: () => void) => { fn(); return () => undefined } },
      open: (id: string) => { calls.push(`open:${id}`) },
      create: async () => { calls.push('create'); return 'created' },
    }
    const dispose = autoSelectProductWorkspace(sessions, workspaces)
    await Promise.resolve()
    expect(calls).toEqual(['open:run-session'])
    dispose()
  })

  it('renders a completed but rejected business result without raw JSON', () => {
    const { businessDetailOf } = loadClientTestSurface()
    const detail = businessDetailOf({
      gate_status: 'reject', coverage_status: 'insufficient', hard_coverage_ratio: 1 / 6,
      stop_reason_detail: 'Critical evidence remains unavailable.',
      failures: [{ capability_id: 'market.cross_asset', error_code: 'research_capability_timeout',
        origin: 'transport', cause_code: 'deadline', retryable: true }],
    })

    expect(detail).toContain('Gate: 拒绝')
    expect(detail).toContain('证据不足 (17% hard)')
    expect(detail).toContain('market.cross_asset: research_capability_timeout')
    expect(detail).toContain('transport/deadline, 可重试')
    expect(detail).not.toContain('{"')
  })

  it('returns an empty readable projection when no business Run exists', () => {
    expect(loadClientTestSurface().businessDetailOf(null)).toBe('')
  })

  it('labels terminal business state instead of treating a completed DSH turn as research success', () => {
    const { hubStatusLabel } = loadClientTestSurface()
    expect(hubStatusLabel({ state: 'completed', business: { status: 'failed' } }, null)).toBe('研究失败')
    expect(hubStatusLabel({ state: 'completed', business: { status: 'research_only' } }, '仅研究'))
      .toBe('仅研究完成 · 仅研究')
    expect(hubStatusLabel({ state: 'completed', business: null }, null)).toBe('DSH 回合完成')
  })

  it('polls through the Session-completed/Hub-pending window but stops on a business terminal', () => {
    const { shouldPollStatus } = loadClientTestSurface()
    expect(shouldPollStatus({ state: 'completed', business: null }, 1)).toBe(true)
    expect(shouldPollStatus({ state: 'completed', business: { status: 'researching' } }, 2)).toBe(true)
    expect(shouldPollStatus({ state: 'completed', business: { status: 'rejected' } }, 3)).toBe(false)
    expect(shouldPollStatus({ state: 'completed', business: null }, 10_000)).toBe(true)
  })

  it('labels replay as read-only instead of presenting it as a live DSH session', () => {
    const { runtimeModeLabel, runtimeNotice } = loadClientTestSurface()
    expect(runtimeModeLabel('replay')).toBe('DSH 回放验收（只读）')
    expect(runtimeNotice('replay')).toContain('只能查看已录制轨迹')
    expect(runtimeNotice('live')).toBe('')
  })

  it('recognizes only composer input and send controls as guarded targets', () => {
    const { protectedComposerTarget } = loadClientTestSurface()
    const input = { closest: (selector: string) => selector.includes('data-composer-input') ? {} : null }
    const other = { closest: () => null }
    expect(protectedComposerTarget(input)).toBe(true)
    expect(protectedComposerTarget(other)).toBe(false)
    expect(protectedComposerTarget(null)).toBe(false)
  })

  it('builds a typed Hub intake from the official DSH composer text', () => {
    const { composerText, intakePayload, intakeHeaders } = loadClientTestSurface()
    const documentLike = {
      querySelector: (selector: string) => selector === '[data-composer-input]'
        ? { textContent: '  Analyze the latest Fed speech and BTC reaction.  ' }
        : null,
    }
    const text = composerText(documentLike)

    expect(text).toBe('Analyze the latest Fed speech and BTC reaction.')
    expect(intakePayload(text, 'session-user-1')).toEqual({
      schema_version: 'dsh-research-intake.v1', text,
      source_id: 'dsh-web', language: 'zh', client_session_id: 'session-user-1',
    })
    expect(intakeHeaders('client-intake-0001')).toEqual({
      'content-type': 'application/json', accept: 'application/json',
      'idempotency-key': 'client-intake-0001',
    })
  })

  it('polls the same durable Run after intake instead of treating queue acceptance as completion', () => {
    expect(loadClientTestSurface().researchStatusUrl('run/one')).toBe('/api/decision-hub/status?run_id=run%2Fone')
  })

  it('projects a readable report from canonical Hub facts without parsing assistant JSON', () => {
    const { researchReportUrl, reportModelOf } = loadClientTestSurface()
    const model = reportModelOf({
      business: {
        gate_status: 'publish', coverage_status: 'sufficient', hard_coverage_ratio: 1,
        stop_reason_detail: 'All hard facts passed.', failures: [],
      },
      decision_desk_url: 'http://hub.local/?run_id=run-1',
    }, {
      run: {
        run_id: 'run-1', event_title: 'Fed statement and BTC transmission', status: 'completed',
        admission_origin: 'manual', priority: 'high',
        coverage: { gaps: [] }, stop_reason: null,
      },
      causal_case: {
        thesis: 'The event repriced rates and transmitted into BTC.',
        main_chain: [{ link_id: 'main-1', statement: 'Rates repriced higher.' }],
        opposite_chain: [{ link_id: 'counter-1', statement: 'The move was already priced.' }],
      },
      horizons: [{ horizon: '30m', action: 'short', subjective_probability: 0.62,
        trigger: 'DXY and yields confirm.', invalidation: 'Rates reverse.' }],
      evidence: [
        { evidence_id: 'ev-1', requirement_id: 'event_identity', quality: 'accepted',
          freshness_status: 'fresh', authority: 'official', source_id: 'fed', excerpt: 'Official statement.' },
        { evidence_id: 'ev-stale', requirement_id: 'macro_transmission', quality: 'accepted',
          freshness_status: 'stale', authority: 'exchange', source_id: 'rates', excerpt: 'Stale quote.' },
      ],
      total_tool_calls: 6, scheduled_recheck_at: '2026-09-01T12:00:00Z',
    })

    expect(researchReportUrl('run/one')).toBe('/api/decision-hub/report?run_id=run%2Fone')
    expect(model).toMatchObject({
      title: 'Fed statement and BTC transmission', gate: '可发布',
      origin: '用户提交', priority: '高优先级',
      coverage: '证据充分 · 100% hard', evidenceCount: 1, totalToolCalls: 6,
    })
    expect(model.mainChain[0].statement).toBe('Rates repriced higher.')
    expect(model.oppositeChain[0].statement).toBe('The move was already priced.')
    expect(model.horizons[0].horizon).toBe('30m')
    expect(model.evidence.map((item: { evidence_id: string }) => item.evidence_id)).toEqual(['ev-1'])
  })

  it('keeps typed capability failures and evidence gaps in the report projection', () => {
    const { reportModelOf } = loadClientTestSurface()
    const model = reportModelOf({
      business: {
        gate_status: 'reject', coverage_status: 'insufficient', hard_coverage_ratio: 1 / 6,
        stop_reason_detail: 'Critical data unavailable.',
        failures: [{ capability_id: 'web.search', error_code: 'research_capability_timeout',
          origin: 'transport', cause_code: 'deadline', retryable: true }],
      },
    }, {
      run: { run_id: 'run-2', event_title: 'Unresolved event', status: 'rejected',
        coverage: { gaps: [{ requirement_id: 'macro_transmission', reason_code: 'stale', query_hint: 'Refresh rates.' }] },
        stop_reason: null },
      evidence: [], causal_case: null, horizons: [], total_tool_calls: 2, scheduled_recheck_at: null,
    })

    expect(model.gate).toBe('拒绝')
    expect(model.gaps).toHaveLength(1)
    expect(model.failures[0]).toMatchObject({
      capability_id: 'web.search', error_code: 'research_capability_timeout', retryable: true,
    })
    expect(model.horizons).toEqual([])
  })

  it('does not present an unlinked DSH session as a report still generating', () => {
    const { reportStateOf } = loadClientTestSurface()
    expect(reportStateOf({ run_id: null, business: null }, null, null)).toBe('idle')
  })

  it('projects a terminal status when the canonical detail is temporarily unavailable', () => {
    const { reportStateOf, terminalReportModelOf } = loadClientTestSurface()
    const link = {
      run_id: 'run-failed', state: 'failed', decision_desk_url: 'http://hub.local/run-failed',
      business: {
        status: 'failed', gate_status: 'research_only', coverage_status: 'insufficient',
        hard_coverage_ratio: 0.67, stop_reason_detail: 'Synthesis attestation failed; 47 facts retained.',
        failures: [{ capability_id: 'dsh', error_code: 'dsh_evidence_unattested',
          origin: 'orchestration', cause_code: 'synthesis_attestation', retryable: false }],
      },
    }
    expect(reportStateOf(link, null, null)).toBe('terminal_pending')
    const model = terminalReportModelOf(link, null)
    expect(model).toMatchObject({
      status: 'failed', gate: '仅研究', coverage: '证据不足 · 67% hard',
    })
    expect(model.stopReason).toContain('47 facts retained')
    expect(model.failures[0].error_code).toBe('dsh_evidence_unattested')

    expect(reportStateOf(link, null, 'research_report_unavailable')).toBe('terminal_error')
    expect(terminalReportModelOf({ ...link, business: { ...link.business, stop_reason_detail: null } }, 'research_report_unavailable'))
      .toMatchObject({ status: 'failed', stopReason: 'research_report_unavailable' })
  })

  it('keeps refreshing a readable report until the Hub business reaches a terminal state', () => {
    const { shouldPollReport } = loadClientTestSurface()
    const detail = { run: { run_id: 'run-live', status: 'researching' } }

    expect(shouldPollReport({ business: { status: 'researching' } }, detail, null)).toBe(true)
    expect(shouldPollReport({ business: { status: 'research_only' } }, detail, null)).toBe(false)
  })

  it('uses the official conversation view seam without replacing shipped DSH views', () => {
    const registration = loadClientTestSurface().reportViewRegistration()
    expect(registration).toEqual({
      name: 'conversation.view', id: 'decision-hub-report', order: 20, label: '研究报告',
    })
    expect(registration.id).not.toBe('chat')
    expect(registration.id).not.toBe('trajectory')
  })

  it('registers a separate official active-research Inbox view', () => {
    expect(loadClientTestSurface().inboxViewRegistration()).toEqual({
      name: 'conversation.view', id: 'decision-hub-inbox', order: 10, label: '主动研究',
    })
    const source = readFileSync(resolve(process.cwd(), 'src/client/index.js'), 'utf8')
    expect(source).toContain("fetch('/api/decision-hub/inbox?limit=100'")
    expect(source).toContain("inboxViewRegistration(), props => createElement(ResearchInboxView")
  })

  it('maps canonical Inbox state without exposing raw JSON', () => {
    const { inboxItemModelOf } = loadClientTestSurface()
    const model = inboxItemModelOf({
      event_id: 'event-fed-1', event_title: 'Fed Chair speech', event_family: 'central_bank_speech',
      run_id: 'run-1', dsh_session_id: 'dsh-1', status: 'research_only',
      baseline_status: 'ready', gate_status: 'research_only', notification_status: 'delivered',
      admission_origin: 'automatic', report_available: true, headline: '研究结论', summary: '关键事实仍受限。',
      scheduled_at: null, next_recheck_at: '2026-09-05T10:00:00Z', updated_at: '2026-09-05T09:00:00Z',
    })
    expect(model).toMatchObject({
      eventId: 'event-fed-1', title: 'Fed Chair speech', status: '仅研究', baseline: '基线就绪',
      gate: '仅研究', notification: '通知已送达', origin: '自动发现', runId: 'run-1',
      sessionId: 'dsh-1', canOpenSession: true, isWatchOnly: false, reportAvailable: true,
    })
    expect(JSON.stringify(model)).not.toContain('schema_version')
  })

  it('keeps watch-only Inbox items sessionless', async () => {
    const { inboxItemModelOf, openInboxSessionOf } = loadClientTestSurface()
    const item = {
      event_id: 'event-watch', event_title: 'Upcoming CPI', event_family: 'macro_release',
      run_id: null, dsh_session_id: null, status: 'watching', baseline_status: 'pending', gate_status: null,
      notification_status: 'not_applicable', admission_origin: 'automatic', report_available: false, updated_at: '2026-09-05T09:00:00Z',
    }
    expect(inboxItemModelOf(item)).toMatchObject({
      status: '观察中', baseline: '基线采集中', canOpenSession: false, isWatchOnly: true,
    })
    const sessions = { refresh: async () => { throw new Error('must not refresh') } }
    await expect(openInboxSessionOf(sessions, item, null)).resolves.toBe(false)
  })

  it('opens only a canonical Inbox managed Session through the official controller', async () => {
    const { openInboxSessionOf } = loadClientTestSurface()
    const calls: string[] = []
    const sessions = {
      refresh: async () => { calls.push('refresh') },
      binding: (id: string) => { calls.push('binding:' + id); return { sessionId: id } },
      open: (id: string) => { calls.push('open:' + id) },
    }
    await expect(openInboxSessionOf(sessions, {
      event_id: 'event-1', event_title: 'Event', run_id: 'run-1', dsh_session_id: 'dsh-managed',
      status: 'report_ready', baseline_status: 'ready', gate_status: 'publish',
      notification_status: 'delivered', admission_origin: 'automatic', report_available: true,
    }, 'dsh-current')).resolves.toBe(true)
    expect(calls).toEqual(['refresh', 'binding:dsh-managed', 'open:dsh-managed'])
  })

  it('does not inject a duplicate full report into the composer dock', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/client/index.js'), 'utf8')
    expect(source).toContain("ctx.slots.inject('conversation.view'")
    expect(source).not.toContain("ctx.slots.inject('conversation.composer.dock'")
    expect(source).not.toContain('decision-hub-report-inline')
  })

  it('builds one deterministic retry command per failed source Run', () => {
    expect(loadClientTestSurface().retryCommand('run/one')).toEqual({
      schema_version: 'research-run-command.v1',
      request_id: 'dsh-retry:run/one',
      command: 'retry',
      reason: 'Retry failed research from the official DSH Web.',
    })
  })

  it('disables duplicate intake while the durable child Run is active', () => {
    const { intakeAction } = loadClientTestSurface()
    expect(intakeAction('accepted', 'run-2')).toBe('disabled')
    expect(intakeAction('tracking', 'run-2')).toBe('disabled')
    expect(intakeAction('error', 'run-2')).toBe('retry')
    expect(intakeAction('complete', 'run-2')).toBe('submit')
  })

  it('routes native send and Enter through the Hub intake intent only for unmanaged research', () => {
    const { researchComposerSubmitIntent } = loadClientTestSurface()
    const sendTarget = { closest: (selector: string) => selector.includes('发送消息') ? {} : null }
    const inputTarget = { closest: (selector: string) => selector.includes('data-composer-input') ? {} : null }

    expect(researchComposerSubmitIntent({ type: 'click', target: sendTarget })).toBe(true)
    expect(researchComposerSubmitIntent({ type: 'keydown', key: 'Enter', shiftKey: false, isComposing: false, target: inputTarget })).toBe(true)
    expect(researchComposerSubmitIntent({ type: 'keydown', key: 'Enter', shiftKey: true, isComposing: false, target: inputTarget })).toBe(false)
    expect(researchComposerSubmitIntent({ type: 'keydown', key: 'Enter', shiftKey: false, isComposing: true, target: inputTarget })).toBe(false)
    expect(researchComposerSubmitIntent({ type: 'click', target: inputTarget })).toBe(false)
  })

  it('treats an already linked DSH session as managed and never offers a second mainline intake', () => {
    const { intakeAction, managedSubmissionOf, managedSessionIdOf } = loadClientTestSurface()
    const managed = managedSubmissionOf({
      run_id: 'run-managed', decision_desk_url: 'http://hub.local/run-managed',
      business: { status: 'researching', gate_status: null, failures: [] },
    })

    expect(managed).toMatchObject({ managed: true, runId: 'run-managed', state: 'tracking' })
    expect(intakeAction('tracking', 'run-managed', true)).toBe('disabled')
    expect(intakeAction('complete', 'run-managed', true)).toBe('hidden')
    expect(intakeAction('error', 'run-managed', true)).toBe('retry')
    expect(managedSubmissionOf({ run_id: null, business: null })).toBeNull()
    expect(managedSessionIdOf({ dsh_session_id: 'dsh/session-1' }, null)).toBe('dsh/session-1')
    expect(managedSessionIdOf({ dsh_session_id: 'dsh/session-1' }, 'dsh/session-1')).toBeNull()
  })

  it('uses only the official slot-owned Session identity', () => {
    const { conversationSessionIdOf } = loadClientTestSurface()

    expect(conversationSessionIdOf({ sessionId: 'direct-session' })).toBe('direct-session')
    expect(conversationSessionIdOf({ session: { sessionId: 'nested-session' } })).toBe('nested-session')
    expect(conversationSessionIdOf({})).toBeNull()
  })

  it('projects a non-enumerable official Session identity across nested plugin views', () => {
    const { conversationScopeProps } = loadClientTestSurface()
    const hostProps = Object.defineProperty({}, 'sessionId', {
      value: ' dsh-managed-session ',
      enumerable: false,
    })

    expect({ ...hostProps }).not.toHaveProperty('sessionId')
    expect(conversationScopeProps(hostProps, { surface: 'view' })).toEqual({
      surface: 'view',
      sessionId: 'dsh-managed-session',
    })

    const source = readFileSync(resolve(process.cwd(), 'src/client/index.js'), 'utf8')
    expect(source).toContain('createElement(ResearchReportCard, conversationScopeProps(props')
    expect(source).toContain('createElement(ResearchInboxView, conversationScopeProps(props')
    expect(source).toContain('createElement(ResearchIntakeControl, conversationScopeProps(props')
  })

  it('refreshes and opens a Host-created Session through the official controller', async () => {
    const { openManagedSessionOf } = loadClientTestSurface()
    const calls: string[] = []
    const sessions = {
      refresh: async () => { calls.push('refresh') },
      binding: (id: string) => { calls.push(`binding:${id}`); return { sessionId: id } },
      open: (id: string) => { calls.push(`open:${id}`) },
    }

    await expect(openManagedSessionOf(
      sessions, { dsh_session_id: 'dsh-managed' }, 'dsh-blank',
    )).resolves.toBe(true)
    expect(calls).toEqual(['refresh', 'binding:dsh-managed', 'open:dsh-managed'])
    calls.length = 0
    await expect(openManagedSessionOf(
      sessions, { dsh_session_id: 'dsh-managed' }, 'dsh-managed',
    )).resolves.toBe(false)
    expect(calls).toEqual([])
  })

  it('fails closed when the refreshed Session list cannot resolve the managed identity', async () => {
    const { openManagedSessionOf } = loadClientTestSurface()
    const sessions = {
      refresh: async () => undefined,
      binding: () => undefined,
      open: () => { throw new Error('must not open an unknown Session') },
    }

    await expect(openManagedSessionOf(
      sessions, { dsh_session_id: 'missing-session' }, null,
    )).rejects.toThrow('managed_session_unavailable')
  })
})
