import { useEffect, useRef, useState } from 'react'
import type { PaperAccountSummaryV1 } from '../../../../packages/contracts_ts/src/generated/r2'
import { settingsApi, type SettingsOptions } from '../api/decisionSettings'
import {
  clearPaperAccountIntent, makePaperAccountIntent, perpetualPaperAccounts, readPaperAccountIntent, savePaperAccountIntent,
  type PaperAccountCreateIntent, type PaperAccountCreationReceiptV1,
} from '../api/perpetualPaperAccounts'
import './PerpetualPaperAccountPanel.css'

export function PerpetualPaperAccountPanel() {
  const [options, setOptions] = useState<SettingsOptions | null>(null)
  const [accounts, setAccounts] = useState<PaperAccountSummaryV1[]>([])
  const [intent, setIntent] = useState<PaperAccountCreateIntent | null>(null)
  const [confirmed, setConfirmed] = useState<PaperAccountCreationReceiptV1 | null>(null)
  const [cash, setCash] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [mode, setMode] = useState<'manual_after_visible' | 'server_auto_paper'>('manual_after_visible')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  const confirmedRequest = useRef('')

  function accept(receipt: PaperAccountCreationReceiptV1) {
    setConfirmed(receipt)
    setMessage('原创建回执已确认。模拟账户已保存；账户创建与纸面成交分别记录。')
    if (confirmedRequest.current !== receipt.request_id) {
      confirmedRequest.current = receipt.request_id
      window.dispatchEvent(new Event('decision-hub-paper-accounts-changed'))
    }
  }

  async function refresh(checkOriginal = true) {
    const current = ++generation.current
    setBusy(true)
    try {
      const session = settingsApi.session()
      if (!session) { setMessage('请从本机启动入口打开设置，完成身份验证后再使用模拟账户。'); return }
      const saved = readPaperAccountIntent(session.owner_id)
      setIntent(saved)
      const reads = await Promise.allSettled([settingsApi.options(), perpetualPaperAccounts.list()])
      if (generation.current !== current) return
      if (reads[0].status === 'fulfilled') setOptions(reads[0].value)
      if (reads[1].status === 'fulfilled') setAccounts(reads[1].value.accounts)
      if (reads.some(item => item.status === 'rejected')) setMessage('账户或合约记录暂未完整读取；此前原件会保留，请稍后重新读取。')
      if (checkOriginal && saved) {
        try {
          const receipt = await perpetualPaperAccounts.receipt(saved)
          if (generation.current === current) accept(receipt)
        } catch {
          if (generation.current === current) setMessage('原创建请求尚未确认。先核对原回执，或以同一请求重新提交。')
        }
      }
    } catch { if (generation.current === current) setMessage('保存的创建请求或本机身份尚不能核验，请保留原记录并重新打开本机入口。') }
    finally { if (generation.current === current) setBusy(false) }
  }

  useEffect(() => {
    const reload = () => { setConfirmed(null); confirmedRequest.current = ''; void refresh() }
    void refresh()
    window.addEventListener('decision-hub-settings-session-changed', reload)
    return () => { ++generation.current; window.removeEventListener('decision-hub-settings-session-changed', reload) }
  }, [])

  async function create(original?: PaperAccountCreateIntent) {
    let command = original
    if (!command) {
      try {
        command = makePaperAccountIntent(options?.instruments ?? [], cash, mode, acknowledged)
        savePaperAccountIntent(command)
        setIntent(command)
      } catch { setMessage('请填写正数模拟资金、加载适用合约，并确认模拟规则。尚未发出创建请求。'); return }
    }
    setBusy(true)
    try {
      accept(await perpetualPaperAccounts.create(command))
      await refresh(false)
    } catch { setMessage('原创建请求尚未确认。请核对原回执；再次提交会使用同一请求。') }
    finally { setBusy(false) }
  }

  async function originalReceipt() {
    if (!intent) return
    setBusy(true)
    try { accept(await perpetualPaperAccounts.receipt(intent)) }
    catch { setMessage('原创建请求尚未确认。先核对原回执，或以同一请求重新提交。') }
    finally { setBusy(false) }
  }

  function startAnother() {
    if (!intent || !confirmed || confirmed.request_id !== intent.command.request_id) return
    try {
      clearPaperAccountIntent(intent)
      setAccounts(old => old.some(item => item.account_id === confirmed.account.account_id) ? old : [...old, confirmed.account])
      setIntent(null); setConfirmed(null); confirmedRequest.current = ''
      setCash(''); setAcknowledged(false); setMode('manual_after_visible')
      setMessage('可以再建一个独立模拟账户。已保存的账户和原创建回执仍保留；请重新填写模拟资金并确认规则。')
    } catch { setMessage('当前创建记录尚不能安全切换，请先核对原创建回执。') }
  }

  const shown = [...accounts]
  if (confirmed && !shown.some(item => item.account_id === confirmed.account.account_id)) shown.push(confirmed.account)
  return <section className="decision-settings paper-account-panel" aria-label="永续模拟账户">
    <h2>用模拟资金验证方案</h2>
    <p>创建独立的 USDT 模拟账户，跟踪 BTC / ETH 永续方案。采用单向净仓、逐仓和初始 1 倍名义暴露；不会连接真实交易账户。</p>
    <button disabled={busy} onClick={() => void refresh()}>重新读取模拟账户</button>
    {message && <p role="status">{message}</p>}
    {shown.map((account, index) => {
      const projection = options?.paper_accounts?.find(item => item.partition.account_id === account.account_id)?.account_snapshot
      return <article key={account.account_id} className="paper-account-summary">
        <h3>模拟账户 {index + 1}</h3>
        <p>适用合约：{Object.keys(account.instrument_refs).map(item => item.includes('BTC') ? 'BTC 永续' : 'ETH 永续').join('、')}。{account.execution_mode === 'server_auto_paper' ? '按已审核条件自动模拟' : '看到方案后手动确认模拟'}。</p>
        {projection && <p>最近核验的模拟权益：{projection.equity === null ? '尚无法完整核算' : `${projection.equity} USDT`}；可用保证金：{projection.available_margin === null ? '尚无法核算' : `${projection.available_margin} USDT`}。</p>}
        <p>{account.execution_enabled ? '可进行纸面验证，是否已有成交请查看对应方案记录。' : '纸面执行尚未就绪：账户已保存，但创建账户本身不授权成交。BTC/ETH 四小时手动模拟须在对应建议下独立确认金额与退出条件。'}</p>
      </article>
    })}
    {intent ? <div className="paper-account-original">
      <p>{confirmed ? '原创建回执已确认' : '原创建请求尚未确认'}；初始模拟资金 {intent.command.initial_cash_usdt} USDT。</p>
      <button disabled={busy} onClick={() => void originalReceipt()}>核对原创建回执</button>
      {confirmed && <button disabled={busy} onClick={startAnother}>再建一个模拟账户</button>}
      {!confirmed && <button disabled={busy} onClick={() => void create(intent)}>重新提交原请求</button>}
    </div> : <fieldset disabled={busy}>
      <label>初始模拟资金（USDT）<input aria-label="初始模拟资金" type="text" inputMode="decimal" placeholder="请输入模拟金额" value={cash} onChange={event => setCash(event.target.value)} /></label>
      <label>模拟方式<select aria-label="模拟方式" value={mode} onChange={event => setMode(event.target.value as typeof mode)}>
        <option value="manual_after_visible">看到方案后手动确认</option><option value="server_auto_paper">按已审核条件自动模拟</option>
      </select></label>
      <p>适用合约：{options?.instruments.map(item => item.instrument_id.includes('BTC') ? 'BTC 永续' : 'ETH 永续').join('、') || '尚未加载，请先在上方读取交易所合约'}。所列合约共用账户资金与保证金预算。</p>
      <label className="paper-account-ack"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />我确认这是模拟资金，并同意所选模拟方式；创建账户不表示已经开仓或开始成交。</label>
      <button disabled={busy || !cash.trim() || !acknowledged || !options?.instruments.length} onClick={() => void create()}>创建模拟账户</button>
    </fieldset>}
  </section>
}
