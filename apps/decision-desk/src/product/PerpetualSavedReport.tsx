import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import type { ProbabilityEstimateV1 } from '../../../../packages/contracts_ts/src/generated/r2'
import { apiUrl } from '../api/client'
import { classifySavedReportReadError, perpetualReportPath, primarySavedPerpetualSynthesis, readSavedPerpetualReport, type SavedPerpetualReport, type SavedReportReadErrorKind } from '../api/perpetualSavedReport'
import { settingsApi } from '../api/decisionSettings'
import { buildPerpetualReadingSnapshot, downloadPerpetualReading, renderPerpetualReadingHtml } from './perpetualReportExport'
import { SavedPerpetualAction } from './PerpetualSavedAction'

const sideNames = { flat: '空仓', long: '多头', short: '空头' }
const actionNames = { open: '开仓', add: '加仓', reduce: '减仓', close: '平仓', conditional: '条件动作', no_change: '维持' }
const instrumentNames = { 'okx:BTC-USDT-SWAP': '比特币 USDT 永续合约（BTC）', 'okx:ETH-USDT-SWAP': '以太坊 USDT 永续合约（ETH）' }
function percent(value: string) {
  const [whole, fraction = ''] = value.split('.')
  const integer = `${whole}${fraction.padEnd(2, '0').slice(0, 2)}`.replace(/^0+(?=\d)/, '')
  const remainder = fraction.slice(2).replace(/0+$/, '')
  return `${integer}${remainder ? `.${remainder}` : ''}%`
}
function SavedEstimate({ title, value, direction = false, occurrence = false, eventLabels = false, eventDefinition, targetDefinition, conditionDefinition }: {
  title: string; value: ProbabilityEstimateV1; direction?: boolean; occurrence?: boolean
  eventLabels?: boolean
  eventDefinition?: string; targetDefinition?: string; conditionDefinition?: string
}) {
  const directionNames: Record<string, string> = { up: '上涨', down: '下跌', neutral: '横盘' }
  const occurrenceNames: Record<string, string> = { occurs: '发生', not_occurs: '不发生', occurred: '发生', not_occurred: '不发生' }
  const names: Record<string, string> = direction ? directionNames : occurrence && targetDefinition ? occurrenceNames
    : eventLabels ? { cool: '低于比较基准', neutral: '接近比较基准', hot: '高于比较基准' } : {}
  const unnamed = value.probabilities?.some((item: { outcome_id: string }) => !names[item.outcome_id])
  return <section aria-label={title}><h4>{title}</h4>
    {eventDefinition && <p style={{ whiteSpace: 'pre-wrap' }}>事件原文：{eventDefinition}</p>}
    {targetDefinition && <p style={{ whiteSpace: 'pre-wrap' }}>目标原文：{targetDefinition}</p>}
    {!direction && !targetDefinition && <p>未保存可读定义，结果含义无法完整核对。</p>}
    {value.probabilities === null ? <p>未知（原报告未提供概率）。</p> : <>
      <ul>{value.probabilities.map((item: { outcome_id: string; value: string }) =>
        <li key={item.outcome_id}>{names[item.outcome_id] ?? (targetDefinition ? item.outcome_id : `${item.outcome_id}（名称未提供）`)}：{percent(item.value)}</li>)}</ul>
      {unnamed && targetDefinition && <p>自定义结果沿用原标识，与上方目标原文对应；原报告未提供逐项中文名称。</p>}
    </>}
    {value.conditioning_ref && <p style={{ whiteSpace: 'pre-wrap' }}>{conditionDefinition ? `条件原文：${conditionDefinition}` : '以上概率以原报告引用的条件为前提；未保存该条件的可读定义。'}</p>}
  </section>
}

const readableEventText = (value: string) => value.replace(/\bcool\b/g, '低于比较基准').replace(/\bhot\b/g, '高于比较基准')
const eventLine = (value: string) => /^(事前事件情景：|情景\d+：)/.test(value)
const eventSummaryLine = (value: string) => /^(事前事件情景：|按事件情景概率加权的市场方向：|最高概率事件情景之一：)/.test(value)

function SavedEventScenarios({ rows, summaries }: { rows: string[]; summaries: string[] }) {
  const scenarios = rows.filter(row => /^情景\d+：/.test(row))
  return <section className="research-section" aria-label="已保存事件情景预案">
    <h4>事前制定的事件后响应预案（{scenarios.length} 个情景）</h4>
    <p>以下是事件公布后、满足各自窗口和确认条件时才适用的条件预案。与上方普通方向预测分别核对；不表示已经发生、已成交或取得交易权限。</p>
    {rows.filter(row => row.startsWith('事前事件情景：')).map((row, index) => <p key={index}>{readableEventText(row)}</p>)}
    {summaries.filter(row => !row.startsWith('事前事件情景：')).map((row, index) => <p key={index} aria-label={row.startsWith('按事件情景概率加权') ? '情景概率加权方向' : undefined}>{readableEventText(row)}</p>)}
    {scenarios.map(row => {
      const [title, ...lines] = row.split('\n')
      const plans = lines.filter(line => /^(空仓|持有多头仓位|持有空头仓位)首选预案：/.test(line))
      const narrative = lines.filter(line => !plans.includes(line))
      return <article key={title} aria-label={`已保存${title.split('：')[0]}`}>
        <h5>{readableEventText(title)}</h5>
        {narrative.map((line, index) => <p key={index} style={{ whiteSpace: 'pre-wrap' }}>{readableEventText(line)}</p>)}
        <details aria-label={`${title.split('：')[0]}的持仓预案`}><summary>按当前持仓核对预案与假设单位收益</summary>
          <p>空仓、多头和空头是互斥分支，仅核对实际持仓对应的一行。研究单位收益不代表可执行数量，成交及账户条件仍须核验。</p>
          <ul>{plans.map((line, index) => <li key={index}>{readableEventText(line)}</li>)}</ul>
        </details>
      </article>
    })}
    {scenarios.length === 0 && <p>已保存原件未提供可读的逐情景说明，条件预案保持待核对。</p>}
  </section>
}

function CandidateDefinitionText({ report, role, references, title }: {
  report: SavedPerpetualReport; role: 'entry_condition' | 'exit_policy' | 'basis'
  references: { ref: string; hash: string }[]; title: string
}) {
  return <section aria-label={title}><h6>{title}</h6>
    {references.length === 0 ? <p>{role === 'entry_condition' ? '尚未保存额外入场条件正文。' : '原报告未保存此项正文，适用内容保持未知。'}</p>
      : references.map(reference => {
        const row = report.candidate_definitions?.definitions.find(item => item.role === role
          && item.definition_ref.ref === reference.ref && item.definition_ref.hash === reference.hash)
        if (!row) return <p key={reference.ref}>引用正文未知：原报告未附可读取的对应原件。</p>
        const original: unknown = JSON.parse(row.body_json)
        const statement = original && typeof original === 'object' && 'statement' in original && typeof original.statement === 'string'
          ? original.statement : row.body_json
        return <p key={reference.ref} style={{ whiteSpace: 'pre-wrap' }}>{statement}</p>
      })}
  </section>
}

function ReadingExport({ value, section, canExport }: {
  value: SavedPerpetualReport; section: RefObject<HTMLElement | null>; canExport: () => boolean
}) {
  const [error, setError] = useState<string | null>(null)
  const save = (format: 'json' | 'html') => {
    setError(null)
    try {
      const owner = settingsApi.session()
      if (!canExport() || !section.current || owner && owner.owner_id !== value.owner_id) throw new Error('Reading value is no longer current')
      const content = format === 'json' ? buildPerpetualReadingSnapshot(value)
        : renderPerpetualReadingHtml(value, section.current)
      downloadPerpetualReading(content, value.run_id, format)
    } catch { setError('阅读副本尚未保存成功，请重新核对报告后重试；原报告和资格保持不变。') }
  }
  return <nav aria-label="保存报告阅读副本">
    <button className="button secondary" onClick={() => save('json')}>保存阅读快照 JSON</button>
    <button className="button secondary" onClick={() => save('html')}>保存离线 HTML</button>
    <p>阅读副本只保存这次已核对的正文和定义，不是完整审计封包，不授予交易权限；浏览器保存完成后可离线阅读。</p>
    {error && <p role="alert">{error}</p>}
  </nav>
}

function SavedReport({ value, canExport }: { value: SavedPerpetualReport; canExport: () => boolean }) {
  const section = useRef<HTMLElement | null>(null)
  const { artifact, payload } = value, outer = payload.perpetual_synthesis.synthesis
  const eventMode = outer.schema_version === 'research-decision-synthesis.v3'
  const synthesis = primarySavedPerpetualSynthesis(outer), analysis = synthesis.analysis
  const scenarioRows = eventMode ? payload.inferences.filter(eventLine) : []
  const summaryLines = artifact.summary.split('\n')
  const eventSummaries = eventMode ? summaryLines.filter(eventSummaryLine) : []
  const summary = eventMode ? summaryLines.filter(line => !eventSummaryLine(line) && !line.startsWith('假设当前')).map(readableEventText).join('\n') : artifact.summary
  const candidateProse = eventMode ? payload.inferences.filter(line => line.startsWith('假设当前')) : []
  const ordinaryInferences = eventMode ? payload.inferences.filter(line => !eventLine(line) && !line.startsWith('假设当前')) : payload.inferences
  const verdict = payload.perpetual_synthesis.binding.native_review_verdict
  const reviewText = verdict === 'approved' && payload.perpetual_synthesis.authorization?.verdict === 'approved'
    ? '独立审核已通过' : verdict === null ? '独立审核结论未知' : verdict === 'needs_revision' ? '独立审核要求修订' : '独立审核未通过'
  const context = synthesis.candidate_set.context, candidates = synthesis.candidate_set.candidates
  const definitions = value.probability_definitions.status === 'available' ? value.probability_definitions.definitions : null
  const eventDefinition = definitions?.[0].definition.statement
  const conditionDefinition = (estimate: ProbabilityEstimateV1) => definitions && estimate.conditioning_ref?.ref === definitions[0].definition_ref.ref
    && estimate.conditioning_ref.hash === definitions[0].definition_ref.hash ? eventDefinition : undefined
  const estimates = [{ title: '事件发生概率', value: synthesis.event_probability.occurrence_distribution, occurrence: true,
    eventDefinition, targetDefinition: definitions?.[1].definition.statement },
    { title: '事件发生后的结果概率', value: synthesis.event_probability.outcome_given_occurrence, targetDefinition: definitions?.[2].definition.statement },
    { title: '市场方向概率', value: synthesis.market_distribution.direction_distribution, direction: true }]
  const paragraphs = [{ title: '事实与依据', rows: payload.facts }, { title: '推断与假说', rows: ordinaryInferences }, { title: '传导机制', rows: payload.transmission_chain }, { title: '限制与未解问题', rows: payload.uncertainty }]
  return <section ref={section} className="research-section" aria-label="已保存的永续研究报告">
    <h3>{artifact.headline}</h3><p style={{ whiteSpace: 'pre-wrap' }}>{summary}</p>
    <section aria-label="已核对报告审核状态"><strong>{reviewText}</strong><p>审核结论仅对应这份已核对的报告；不表示预测已经校准，也不授予成交权限。</p></section>
    <p>这是该次任务保存的事前研究。主观概率和机制假说尚未取得金融资格；本页不授权模拟成交或真实交易。</p>
    <p>保存于 {new Date(value.published_at).toLocaleString('zh-CN', { hour12: false })}。本次完整核对已保存原件，未重新生成研究。</p>
    <section aria-label="报告身份"><h4>本报告对应范围</h4>
      <p>{instrumentNames[context.instrument_id]}；{context.horizon.slice(0, -1)} 小时；{context.account_kind === 'paper' ? '模拟账户' : '用户自报账户'}。</p>
    </section>
    <section aria-label="已保存概率"><h4>{eventMode ? '普通方向预测（发布后目标窗口）' : '原报告保存的概率'}</h4>
      {eventMode && <p>以下仅对应原报告的普通主预测；事件后的条件方向及响应窗口见下方逐情景预案。</p>}
      {estimates.map(estimate => <SavedEstimate key={estimate.title} {...estimate} eventLabels={eventMode} conditionDefinition={conditionDefinition(estimate.value)} />)}
    </section>
    {eventMode && <SavedEventScenarios rows={scenarioRows} summaries={eventSummaries} />}
    <details aria-label="原报告范围与条件" style={{ overflowWrap: 'anywhere' }}><summary>核对原报告范围与条件</summary>
      <p>合约原标识：<code>{context.instrument_id}</code>；期限：{context.horizon}。</p>
      <p>原任务：<code>{value.run_id}</code>；原研究请求：<code>{synthesis.request_id}</code>。</p>
      <p>账户：<code>{context.account_id}</code>；设置版本：<code>{context.settings_ref.ref}</code>（{context.settings_ref.hash}）。</p>
      {estimates.map(estimate => <section key={estimate.title}><h5>{estimate.title}</h5>
        <p>目标引用：<code>{estimate.value.target_ref.ref}</code>（{estimate.value.target_ref.hash}）。</p>
        <p>条件引用：{estimate.value.conditioning_ref ? <><code>{estimate.value.conditioning_ref.ref}</code>（{estimate.value.conditioning_ref.hash}）</> : '原报告未附加条件引用'}。</p>
        {estimate.value.probabilities && <ul>{estimate.value.probabilities.map((item: { outcome_id: string; value: string }, index: number) =>
          <li key={item.outcome_id}>结果 {index + 1}：<code>{item.outcome_id}</code>；原保存概率 {item.value}。</li>)}</ul>}
      </section>)}
      {candidates.filter(candidate => candidate.condition_refs.length > 0).map(candidate => <p key={candidate.candidate_id}>候选 <code>{candidate.candidate_id}</code> 的附加条件：{candidate.condition_refs.map(reference => `${reference.ref}（${reference.hash}）`).join('；')}。</p>)}
      <h5>概率定义原件与任务绑定</h5><pre>{JSON.stringify(value.probability_definitions, null, 2)}</pre>
    </details>
    <section aria-label="候选动作与比较"><h4>候选动作与条件盈利概率</h4>
      <SavedPerpetualAction value={value.action_publication} researchPublishedAt={value.published_at} />
      <details><summary>已保存的候选动作与适用分支（{candidates.length} 项，未排序）</summary>
        {!value.candidate_definitions && <p>原报告未附候选条件正文，引用内容保持未知。</p>}
        <p>本笔保护方案的盈利概率未单独计算；原比较仍采用原报告保存的路径与成交假设。</p>
        {eventMode && <><p>这些是相互替代的研究候选，不是同时执行的指令；仅按当前持仓、条件和独立比较核对。</p>{candidateProse.map((line, index) => <p key={index}>{line}</p>)}</>}
        {(['flat', 'long', 'short'] as const).map(branch => {
          const rows = candidates.filter(candidate => candidate.position_branch === branch)
          return rows.length > 0 && <section key={branch}><h5>{branch === 'flat' ? '空仓时' : `已有${sideNames[branch]}仓位时`}</h5>
            <ul>{rows.map(candidate => <li key={candidate.candidate_id}>{actionNames[candidate.action]}，目标{sideNames[candidate.target_side]}。
              {candidate.condition_refs.length > 0 && <span>附有额外条件，请在“核对原报告范围与条件”中核对。</span>}
              <CandidateDefinitionText report={value} role="entry_condition" references={candidate.condition_refs} title="入场与确认条件原文" />
              <CandidateDefinitionText report={value} role="exit_policy" references={candidate.exit_ref ? [candidate.exit_ref] : []} title="保护与退出原文" />
              <CandidateDefinitionText report={value} role="basis" references={candidate.basis_refs} title="适用、持仓与失效依据原文" />
            </li>)}</ul>
          </section>
        })}
      </details>
    </section>
    <nav aria-label="报告后续操作"><p><a className="button secondary" href="#/settings">查看当前建议与模拟</a></p>
      <p>进入后先核对原任务、账户、合约与设置版本，精确范围见上方折叠核对区，再读取当前建议。历史报告不能沿用为当前授权。</p>
      <details><summary>同账户继续研究 ETH</summary><ol>
        <li>返回账户设置，重新读取同一模拟账户及其当前资金、仓位。</li>
        <li>选择以太坊 USDT 永续合约（ETH），核对期限与风险设置，保存新的 ETH 设置。</li>
        <li>发起独立 ETH 研究，按该次任务读取建议与模拟回执。</li>
      </ol></details>
    </nav>
    {paragraphs.map(item => <section key={item.title}><h4>{item.title}</h4>{item.rows.length ? <ul>{item.rows.map((row, index) => <li key={index}>{row}</li>)}</ul> : <p>原报告未提供此项。</p>}</section>)}
    <h4>反方说明</h4><p style={{ whiteSpace: 'pre-wrap' }}>{payload.counter_thesis || '原报告未提供此项。'}</p>
    <details><summary>完整机制、确认与失效条件</summary><p>{analysis.thesis}</p>
      {[{ title: '主要机制', links: analysis.main_chain }, { title: '反方机制', links: analysis.opposite_chain }].map(item => <section key={item.title}><h4>{item.title}</h4><ol>{item.links.map(link => <li key={link.link_id}><p>{link.claim_type === 'fact' ? '事实主张' : link.claim_type === 'scenario' ? '情景假设' : '机制假说'}：{link.statement}</p><p>确认：{link.confirmation}</p><p>失效：{link.invalidation}</p></li>)}</ol></section>)}
      <ul>{analysis.unresolved_questions.map((row, index) => <li key={index}>{row}</li>)}</ul>
    </details>
    <ReadingExport value={value} section={section} canExport={canExport} />
    <p>PDF 导出尚未接通；原始 JSON 可核验。</p>
    <a className="button secondary" href={apiUrl(perpetualReportPath(value.run_id) + '/source')} download={`perpetual-research-${value.run_id}-source.json`}>下载已保存原件 JSON</a>
    <details><summary>核对原件与概率、候选方案</summary><p>源内容 SHA-256：<code>{value.source_sha256}</code></p><pre>{JSON.stringify(payload.perpetual_synthesis, null, 2)}</pre></details>
  </section>
}

export function PerpetualSavedReportBoundary({ runId, visible = true, readRevision = 0, children, technicalRecords }: { runId: string; visible?: boolean; readRevision?: number; children: ReactNode; technicalRecords?: ReactNode }) {
  const [state, setState] = useState<{ run: string; value: SavedPerpetualReport | null; status: 'loading' | 'legacy' | 'ready' | 'error'; failure?: SavedReportReadErrorKind }>({ run: runId, value: null, status: 'loading' })
  const readable = useRef<SavedPerpetualReport | null>(null)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    readable.current = null
    setState({ run: runId, value: null, status: 'loading' })
    if (visible) void readSavedPerpetualReport(runId, controller.signal).then(value => {
      if (!controller.signal.aborted) {
        readable.current = value
        setState({ run: runId, value, status: value ? 'ready' : 'legacy' })
      }
    }).catch(error => { if (!controller.signal.aborted) setState({ run: runId, value: null, status: 'error', failure: classifySavedReportReadError(error) }) })
    const renew = () => {
      readable.current = null
      setState({ run: runId, value: null, status: 'loading' })
      setRetry(value => value + 1)
    }
    window.addEventListener('decision-hub-settings-session-changed', renew)
    return () => { readable.current = null; controller.abort(); window.removeEventListener('decision-hub-settings-session-changed', renew) }
  }, [runId, visible, readRevision, retry])
  const records = technicalRecords ? <details key={runId} className="research-section" aria-label="取证与过程记录">
    <summary>取证与过程记录</summary><p>此区保留原过程统计；独立审核结论以已核对报告为准</p>{technicalRecords}
  </details> : null
  if (!visible) return records
  if (state.run !== runId || state.status === 'loading') return <><p role="status">正在核对已保存报告…</p>{records}</>
  if (state.status === 'legacy') return <>{children}</>
  if (state.status === 'ready' && state.value) return <><SavedReport value={state.value} canExport={() => visible && state.run === runId && readable.current === state.value} />{records}</>
  const message = state.failure === 'identity'
    ? '尚未完成这份报告的身份核对。请从本次产品启动入口恢复本地会话后重试；原报告不会被重新生成。'
    : state.failure === 'integrity'
      ? '暂时无法核对这份已保存报告的原件或完整性。未展示未经核对的内容；可重试读取，原报告不会被重新生成。'
      : '暂时未能读取这份已保存报告。请稍后重试；原报告不会被重新生成。'
  return <><section className="research-section"><p role="alert">{message}</p><button className="button secondary" onClick={() => setRetry(value => value + 1)}>重试读取</button></section>{records}</>
}
