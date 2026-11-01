import { describe, expect, it } from 'vitest'
import { researchDisplayText } from './researchLanguage'

describe('the actual release32 report language', () => {
  it('renders its registered prose without changing numbers, opaque values or the original view', () => {
    const raw = '已观察驱动：日线 period_close 81754.5 低于 sma_20 84247.41；受影响变量：BTC-USDT spot trend and momentum；订单簿 ask-heavy。反方：perpetual squeeze risk and spot upside extension。缺少 event_window_baseline、liquidation_map、event_id/window offsets，probability_status 为 uncalibrated。'
    const displayed = researchDisplayText(raw)
    expect(displayed).toContain('期间收盘价 81754.5')
    expect(displayed).toContain('20日均价 84247.41')
    expect(displayed).toContain('BTC-USDT 现货趋势与动量')
    expect(displayed).toContain('订单簿 卖盘较重')
    expect(displayed).toContain('永续合约挤压风险与现货上行动量')
    expect(displayed).toContain('事件窗口基线、强平分布、事件编号和窗口偏移')
    expect(displayed).toContain('概率状态 为 尚未校准')
    expect(displayed).not.toContain('卖方-heavy')
    expect(researchDisplayText(`原文：${raw}`)).toBe(`原文：${raw}`)
    expect(researchDisplayText('说明：0.000001337 与 https://example.test/period_close/ask-heavy?spot=1 保留。'))
      .toContain('0.000001337 与 https://example.test/period_close/ask-heavy?spot=1')
    expect(researchDisplayText('说明：request_id=riskresearch_abc 与 `liquidation_map` 原件保留。'))
      .toContain('request_id=riskresearch_abc 与 `liquidation_map`')
    expect(raw).toContain('period_close 81754.5')
  })
})
