// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it } from 'vitest'
import { RiskExposure, exposureScenario } from './RiskExposure'

it('separates opposite directions and leverage with no liquidation inference', async () => {
  expect(exposureScenario('long', -2, 10, 1000)).toEqual({ exposure: 10000, change: -200, equityChangePct: -20 })
  expect(exposureScenario('short', -2, 10, 1000)).toEqual({ exposure: 10000, change: 200, equityChangePct: 20 })
  const node = document.createElement('div'); document.body.append(node)
  const root = createRoot(node)
  await act(async () => root.render(<RiskExposure />))
  expect(node.textContent).toContain('无法计算你的清算价')
  expect(node.textContent).toContain('-20')
  expect(node.textContent).toContain('没有预测未来价格')
  await act(async () => root.unmount()); node.remove()
})
