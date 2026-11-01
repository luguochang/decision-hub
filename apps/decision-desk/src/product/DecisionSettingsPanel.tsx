import { useEffect, useRef, useState } from 'react'
import './DecisionSettingsPanel.css'
import { ApiRequestError } from '../api/client'
import { PerpetualResearchPanel } from './PerpetualResearchPanel'
import { PerpetualResearchSchedulePanel } from './PerpetualResearchSchedulePanel'
import type { DecisionHorizonV2 } from '../api/perpetualResearch'
import { PerpetualAdvicePanel } from './PerpetualAdvicePanel'
import { perpetualAdviceSelection } from '../api/perpetualAdvice'
import { readPerpetualHorizonPreference, savePerpetualHorizonPreference } from './perpetualHorizonPreference'
import { emptySettingsForm, makeSettingsIntent, readSettingsIntent, readSettingsSelection, saveSettingsSelection, saveSettingsIntent, settingsApi, settingsFormFromCurrent, settingsIntentKey,
  type DecisionSettingsCurrentViewV1, type DecisionSettingsPartitionV1, type SettingsForm, type SettingsIntent, type SettingsOptions } from '../api/decisionSettings'

function message(error: unknown) {
  const code = error instanceof Error ? error.message : ''
  if (error instanceof Error && error.name === 'ZodError') return '输入未通过核验。金额、张数和杠杆请使用正确的十进制数；未知持仓不填写杠杆。尚未提交。'
  const labels: Record<string, string> = {
    decision_settings_state_conflict: '设置版本已变化。请核对原回执后重新读取；旧输入没有覆盖新版本。',
    decision_settings_request_conflict: '原请求与已保存内容冲突，请核对原回执。',
    decision_settings_owner_session_not_configured: '设置的本机身份验证尚未配置，请在本机启动配置中启用。',
    decision_settings_session_required: '本机身份验证已过期，请重新验证后恢复原操作。',
    decision_settings_session_response_invalid: '本机身份验证响应无法核验，请重新打开启动器创建的本机入口。',
    local_owner_session_rejected: '本机身份验证已失效，请重新验证后恢复原操作。',
    local_owner_pairing_rejected: '本机配对口令未通过验证。',
    decision_settings_instrument_original_unavailable: '尚无可核验的合约元数据，请先读取交易所元数据。',
    decision_settings_instrument_source_unavailable: '合约元数据来源暂不可用，请稍后重试。',
  }
  if (labels[code]) return labels[code]
  return '当前结果尚未确认。请恢复原操作回执；原请求会保留。'
}

export function DecisionSettingsPanel() {
  const [session, setSession] = useState(() => { try { return settingsApi.session() } catch { return null } })
  const [token, setToken] = useState('')
  const [options, setOptions] = useState<SettingsOptions | null>(null)
  const [instrumentId, setInstrumentId] = useState('')
  const [accountId, setAccountId] = useState('')
  const [accountKind, setAccountKind] = useState<'user_declared' | 'paper'>('user_declared')
  const [selection, setSelection] = useState<DecisionSettingsPartitionV1 | null>(null)
  const [current, setCurrent] = useState<DecisionSettingsCurrentViewV1 | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [form, setForm] = useState<SettingsForm>({ ...emptySettingsForm })
  const [intent, setIntent] = useState<SettingsIntent | null>(null)
  const [storageBad, setStorageBad] = useState(false)
  const [missing, setMissing] = useState(false)
  const [rejected, setRejected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [horizon, setHorizon] = useState<DecisionHorizonV2>('4h')
  const active = useRef(false)
  const epoch = useRef(0)
  const paperAccountIds = [...new Set(options?.paper_accounts.map(item => item.partition.account_id))]
  const paperAccountLabel = paperAccountIds.includes(accountId) ? `模拟账户 ${paperAccountIds.indexOf(accountId) + 1}` : '模拟账户'
  const positionLabels = { flat: '空仓', long: '多仓', short: '空头仓位', unknown: '未知' }
  const publicOption = options?.instruments.find(item => item.instrument_id === instrumentId)
  const paper = options?.paper_accounts?.find(item => item.partition.account_id === accountId && item.partition.instrument_id === instrumentId)
  const option = accountKind === 'paper' ? paper ? { instrument: paper.position_context.instrument_ref, execution_profile: paper.execution_profile, instrument_id: paper.partition.instrument_id } : undefined : publicOption
  const retained = selection?.account_kind === accountKind && selection.account_id === accountId && selection.instrument_id === instrumentId ? selection : null
  const partition: DecisionSettingsPartitionV1 | null = retained ?? (option && accountId.trim() && accountId === accountId.trim() ? { account_kind: accountKind, account_id: accountId, instrument_id: option.instrument_id, profile_ref: option.execution_profile.ref } : null)

  async function refreshOptions() {
    const value = await settingsApi.options()
    setOptions(value)
    setAccountId(old => old || value.declared_account_id)
    setInstrumentId(old => old || value.instruments[0]?.instrument_id || '')
  }
  async function readCurrent(selected = partition) {
    if (!selected) return
    const generation = ++epoch.current
    setLoaded(false)
    const value = await settingsApi.current(selected)
    if (generation !== epoch.current) return
    setHorizon(session ? readPerpetualHorizonPreference(session.owner_id, selected) : '4h')
    setCurrent(value); setForm(settingsFormFromCurrent(value)); setLoaded(true); setSelection(selected)
    if (session) saveSettingsSelection(session.owner_id, selected)
  }
  async function accepted(original: SettingsIntent, receipt: Awaited<ReturnType<typeof settingsApi.receipt>>) {
    if (receipt.owner_id !== original.owner_id || receipt.request_id !== original.command.request_id) throw new Error('decision_settings_receipt_identity_mismatch')
    sessionStorage.removeItem(settingsIntentKey(original.owner_id))
    setIntent(null); setMissing(false); setRejected(false); setSaved(true); setError('')
    // Read the latest head separately; the receipt always belongs to the original command.
    if (partition && JSON.stringify(partition) === JSON.stringify(original.command.partition)) await readCurrent(partition)
  }
  async function post(original: SettingsIntent) {
    try { await accepted(original, await settingsApi.command(original)) }
    catch (failure) {
      try { await accepted(original, await settingsApi.receipt(original)); return }
      catch (lookup) { if (lookup instanceof ApiRequestError && lookup.status === 404) setMissing(true) }
      if (failure instanceof ApiRequestError && failure.status === 409 && failure.message === 'decision_settings_state_conflict') setRejected(true)
      setError(message(failure))
    }
  }
  async function restore(original = intent, retry = false) {
    if (!original || active.current) return
    active.current = true; setBusy(true); setError('')
    try { await accepted(original, await settingsApi.receipt(original)) }
    catch (failure) {
      if (failure instanceof ApiRequestError && failure.status === 404) {
        setMissing(true)
        if (retry) await post(original)
      } else setError(message(failure))
    } finally { active.current = false; setBusy(false) }
  }
  useEffect(() => {
    if (!session) return
    void refreshOptions().catch(failure => setError(message(failure)))
    try {
      const original = readSettingsIntent(session.owner_id)
      setIntent(original)
      if (original) { setSelection(original.command.partition); setAccountKind(original.command.partition.account_kind); setAccountId(original.command.partition.account_id); setInstrumentId(original.command.partition.instrument_id); void restore(original) }
      else {
        const selected = readSettingsSelection(session.owner_id)
        if (selected) { setSelection(selected); setAccountKind(selected.account_kind); setAccountId(selected.account_id); setInstrumentId(selected.instrument_id) }
      }
    } catch { setStorageBad(true); setError('无法核对本机原操作记录，尚未提交新操作。') }
  }, [session?.owner_id])
  useEffect(() => {
    const changed = () => { if (session) void refreshOptions().catch(failure => setError(message(failure))) }
    window.addEventListener('decision-hub-paper-accounts-changed', changed)
    return () => window.removeEventListener('decision-hub-paper-accounts-changed', changed)
  }, [session?.owner_id])
  useEffect(() => {
    const changed = () => { try { setSession(settingsApi.session()) } catch { setSession(null) } }
    const unavailable = () => setError('本机授权连接未完成。请重新打开启动器创建的本机入口。')
    window.addEventListener('decision-hub-settings-session-changed', changed)
    window.addEventListener('decision-hub-settings-session-unavailable', unavailable)
    return () => { window.removeEventListener('decision-hub-settings-session-changed', changed); window.removeEventListener('decision-hub-settings-session-unavailable', unavailable) }
  }, [])
  useEffect(() => {
    setSaved(false); setCurrent(null); setLoaded(false)
    ++epoch.current
    if (partition) void readCurrent(partition).catch(failure => setError(message(failure)))
    return () => { ++epoch.current }
  }, [session?.owner_id, accountKind, accountId, instrumentId, partition?.profile_ref])

  async function pair() {
    if (active.current) return
    active.current = true; setBusy(true); setError('')
    try { const value = await settingsApi.pair(token); setToken(''); setSession(value) }
    catch (failure) { setError(message(failure)) }
    finally { active.current = false; setBusy(false) }
  }
  async function prepare(instrument: 'okx:BTC-USDT-SWAP' | 'okx:ETH-USDT-SWAP') {
    if (active.current) return
    active.current = true; setBusy(true); setError('')
    try {
      const value = await settingsApi.prepare(instrument)
      await refreshOptions()
      setOptions(old => old ? { ...old, availability: 'available', reason: null, instruments: [...old.instruments.filter(item => item.instrument_id !== value.instrument_id), value] } : old)
      setInstrumentId(value.instrument_id)
    } catch (failure) { await refreshOptions().catch(() => undefined); setError(message(failure)) }
    finally { active.current = false; setBusy(false) }
  }
  async function submit(remove = false) {
    if (!session || !partition || (!remove && !option) || !loaded || intent || storageBad || active.current) return
    active.current = true; setBusy(true); setError(''); setSaved(false)
    try {
      const original = await makeSettingsIntent(session.owner_id, partition, option, current, form, remove, accountKind === 'paper' ? paper : undefined)
      saveSettingsSelection(session.owner_id, partition)
      saveSettingsIntent(original); setIntent(original)
      await post(original)
    } catch (failure) { setError(message(failure)) }
    finally { active.current = false; setBusy(false) }
  }
  const disabled = busy || Boolean(intent) || storageBad
  const field = (key: keyof SettingsForm, label: string, extra = '') => <label>{label}<input aria-label={label} inputMode="decimal" value={form[key]} placeholder={extra || '未知（留空）'} onChange={event => setForm(old => ({ ...old, [key]: event.target.value }))} /></label>

  return <section className="product-card decision-settings" aria-label="我的仓位与风险设置">
    <h2>我的仓位与风险设置</h2>
    <p>自报输入按账户与合约保存。未填写的余额、张数、保证金和强平位置保持未知；缺账户信息时仅用于单位名义研究及适用分支。</p>
    {!session ? <div><p>请打开启动器创建的“Decision Hub”本机入口，身份会自动连接。</p><details><summary>恢复本机授权</summary><label>本机配对口令<input type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} /></label><button type="button" disabled={busy || !token} onClick={() => void pair()}>验证本机身份</button></details></div> : <>
      <p>当前身份：{session.owner_id} · 本机授权有效至 {new Date(session.expires_at).toLocaleString('zh-CN')}。{options?.paper_available ? '已核验的模拟账户可单独保存风险设置；模拟执行需独立启用。' : '模拟账户暂不可用，需独立建账、来源核验和明确启用。'}</p>
      <div className="action-buttons"><button type="button" disabled={disabled} onClick={() => void prepare('okx:BTC-USDT-SWAP')}>读取 BTC 合约元数据</button><button type="button" disabled={disabled} onClick={() => void prepare('okx:ETH-USDT-SWAP')}>读取 ETH 合约元数据</button></div>
      {options?.availability === 'unavailable' && <p role="status">尚无可核验的合约元数据，请先读取交易所元数据。合约选项会在服务端核验后出现。</p>}
      <fieldset disabled={disabled}>
        <label>账户来源<select aria-label="设置账户来源" value={accountKind} onChange={event => { const kind = event.target.value as typeof accountKind; setAccountKind(kind); setAccountId(kind === 'paper' ? options?.paper_accounts[0]?.partition.account_id ?? '' : options?.declared_account_id ?? ''); if (kind === 'paper') setInstrumentId(options?.paper_accounts[0]?.partition.instrument_id ?? '') }}><option value="user_declared">用户自报（可未知）</option><option value="paper" disabled={!options?.paper_available}>已保存的模拟账户</option></select></label>
        {accountKind === 'paper' ? <label>模拟账户<select value={accountId} onChange={event => setAccountId(event.target.value)}>{paperAccountIds.map((id, index) => <option key={id} value={id}>模拟账户 {index + 1}</option>)}</select></label> : <label>自报账户标识<input aria-label="自报账户标识" value={accountId} onChange={event => setAccountId(event.target.value)} /></label>}
        <label>合约<select aria-label="设置合约" value={instrumentId} onChange={event => setInstrumentId(event.target.value)}><option value="">请选择已核验合约</option>{instrumentId && !option && <option value={instrumentId}>{instrumentId}（已选原分区，来源暂不可用）</option>}{accountKind === 'paper' ? options?.paper_accounts.filter(item => item.partition.account_id === accountId).map(item => <option key={item.partition.instrument_id} value={item.partition.instrument_id}>{item.partition.instrument_id}</option>) : options?.instruments.map(item => <option key={item.instrument_id} value={item.instrument_id}>{item.instrument_id}</option>)}</select></label>
        {option && <p>执行参考：净仓、逐仓、模拟新仓名义/初始保证金比 1。此为服务端模拟协议，不代表你的真实杠杆或已开启模拟。</p>}
        {accountKind === 'paper' && paper ? <p>模拟账本当前仓位：{positionLabels[paper.position_context.state]}；权益：{paper.account_snapshot.equity ?? '未知'} USDT；可用保证金：{paper.account_snapshot.available_margin ?? '未知'} USDT。保存采用服务端账本原件，余额与仓位由模拟账本维护。</p> : <>
        <label>当前仓位<select aria-label="当前仓位" value={form.state} onChange={event => setForm(old => ({ ...old, state: event.target.value as SettingsForm['state'] }))}><option value="unknown">未知（仅假设空仓比较）</option><option value="flat">空仓</option><option value="long">多仓</option><option value="short">空头仓位</option></select></label>
        {(form.state === 'long' || form.state === 'short') && <>{field('contracts', '持仓张数（正数，自报）')}{field('entryPrice', '入场价格（USDT/基础币）')}</>}
        {field('equity', '账户权益（USDT，自报）')}{field('availableMargin', '可用保证金（USDT，自报）')}
        <label>保证金模式<select value={form.marginMode} onChange={event => setForm(old => ({ ...old, marginMode: event.target.value as SettingsForm['marginMode'] }))}><option value="unknown">未知</option><option value="isolated">逐仓</option><option value="cross">全仓</option></select></label>
        <label>持仓模式<select value={form.positionMode} onChange={event => setForm(old => ({ ...old, positionMode: event.target.value as SettingsForm['positionMode'] }))}><option value="unknown">未知</option><option value="net">单向净仓</option></select></label>
        {field('leverage', '真实杠杆（自报）')}</>}{field('maxNotional', '最大名义暴露（USDT）')}{field('maxLoss', '最大计划损失（USDT）')}
        <label>允许方向<select value={form.directions} onChange={event => setForm(old => ({ ...old, directions: event.target.value as SettingsForm['directions'] }))}><option value="any">未限制方向</option><option value="long">只允许多头</option><option value="short">只允许空头</option></select></label>
      </fieldset>
      <div className="action-buttons"><button data-save type="button" disabled={disabled || !loaded || !partition || !option} onClick={() => void submit()}>保存设置</button><button type="button" disabled={disabled || !loaded || !current?.revision.values} onClick={() => void submit(true)}>删除活跃设置</button><button type="button" disabled={busy || !partition} onClick={() => void readCurrent().catch(failure => setError(message(failure)))}>重新读取设置</button></div>
      {loaded && <p role="status">{current ? current.revision.operation === 'remove' ? '活跃设置已删除，历史原件保留' : '已回读服务端设置' : '尚未保存'}{current ? ` · 版本 ${current.state_version} · ${current.reevaluation_intent_state === 'pending' ? '比较待更新' : '待核对比较结果'}` : ''}</p>}
      {saved && <p role="status">设置已保存 · 比较待更新。建议、监控与模拟结果需各自完成处理后核对。</p>}
      <label>预测期限<select aria-label="永续预测期限" value={horizon} onChange={event => { const next = event.target.value as DecisionHorizonV2; setHorizon(next); if (session && partition) savePerpetualHorizonPreference(session.owner_id, partition, next) }}><option value="4h">4 小时</option><option value="24h">24 小时</option><option value="72h">72 小时</option></select></label>
      <PerpetualResearchPanel current={loaded ? current : null} disabled={disabled || !loaded} horizon={horizon} accountLabel={accountKind === 'paper' ? paperAccountLabel : undefined} />
      <PerpetualResearchSchedulePanel current={loaded ? current : null} horizon={horizon} />
      <PerpetualAdvicePanel selection={perpetualAdviceSelection(loaded ? current : null, horizon)} />
      {intent && <div role="status"><strong>原操作结果待确认</strong><p>已保留原账户、合约、版本与请求，先核对回执再提交新操作。</p><button type="button" disabled={busy} onClick={() => void restore()}>恢复原操作回执</button>{missing && !rejected && <button type="button" disabled={busy} onClick={() => void restore(intent, true)}>重试原操作</button>}{missing && rejected && <button type="button" disabled={busy} onClick={() => { sessionStorage.removeItem(settingsIntentKey(intent.owner_id)); setIntent(null); setRejected(false); setMissing(false); setError(''); void readCurrent().catch(failure => setError(message(failure))) }}>放弃未保存的原操作</button>}</div>}
      <details><summary>数据与执行详情</summary><p>账户类型：{accountKind}；账户原始标识：{accountId}；设置按身份、账户、OKX 合约与执行参考隔离。删除活跃设置保留审计原件。当前设置重评、建议发布与纸面执行的消费链路尚未接通。</p>{option && <p>当前可选合约原件：{option.instrument.ref} · {option.instrument.hash}<br />执行参考：{option.execution_profile.ref}</p>}{current?.revision.values && <p>已保存设置的合约原件：{current.revision.values.instrument.ref} · {current.revision.values.instrument.hash}</p>}{intent && <p>原请求：{intent.command.request_id}</p>}</details>
    </>}
    {error && <p role="alert">{error}</p>}
    {session && Date.parse(session.expires_at) <= Date.now() && <button type="button" onClick={() => setSession(null)}>重新验证本机身份</button>}
  </section>
}
