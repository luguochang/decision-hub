import { useState } from 'react'

export function exposureScenario(direction: 'long' | 'short', move: number, leverage: number, equity: number) {
  const exposure = equity * leverage
  const change = exposure * move / 100 * (direction === 'long' ? 1 : -1)
  return { exposure, change, equityChangePct: change / equity * 100 }
}

export function RiskExposure() {
  const [direction, setDirection] = useState<'long' | 'short'>('long')
  const [equity, setEquity] = useState('1000')
  const [leverage, setLeverage] = useState('1')
  const [move, setMove] = useState('-2')
  const e = Number(equity), l = Number(leverage), m = Number(move)
  const valid = equity.trim() !== '' && leverage.trim() !== '' && move.trim() !== '' && Number.isFinite(e) && e > 0 && e <= 1e12 && Number.isFinite(l) && l >= 1 && l <= 100 && Number.isFinite(m) && m >= -99 && m <= 100
  const result = valid ? exposureScenario(direction, m, l, e) : null
  const amount = (value: number) => new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)
  return <section className="product-card" aria-label="价格变化与资金暴露情景"><h2>价格变化会怎样影响资金</h2><p>自行设定假设波动，分别查看做多与做空的线性盈亏。此处没有预测未来价格，也不执行交易。</p>
    <div className="risk-exposure-inputs"><label>持仓方向<select aria-label="持仓方向" value={direction} onChange={event => setDirection(event.target.value as 'long' | 'short')}><option value="long">做多</option><option value="short">做空</option></select></label>
      <label>假设投入资金（USDT）<input aria-label="假设投入资金" type="number" min="0.01" max="1000000000000" value={equity} onChange={event => setEquity(event.target.value)} /></label>
      <label>假设杠杆倍数<input aria-label="假设杠杆倍数" type="number" min="1" max="100" value={leverage} onChange={event => setLeverage(event.target.value)} /></label>
      <label>假设价格变化（%）<input aria-label="假设价格变化" type="number" min="-99" max="100" value={move} onChange={event => setMove(event.target.value)} /></label></div>
    {result ? <p aria-live="polite">名义暴露 {amount(result.exposure)} USDT；假设盈亏 <strong>{result.change > 0 ? '+' : ''}{amount(result.change)} USDT</strong>，相对投入资金 {result.equityChangePct > 0 ? '+' : ''}{amount(result.equityChangePct)}%。{result.equityChangePct <= -100 && '线性损失已达到或超过假设投入资金。'}</p> : <p role="alert">请输入有效资金、1–100 倍杠杆和 −99% 至 +100% 的假设价格变化。</p>}
    <p className="product-muted">按线性 USDT 名义暴露计算，未计手续费、资金费和滑点。缺少实际账户、保证金、标记价和交易所规则时，无法计算你的清算价。杠杆输入仅用于解释敏感性。</p>
  </section>
}
