import { expect, it } from 'vitest'
import { moneyAmount } from './numericPresentation'

it('distinguishes missing fees, true zero and tiny nonzero fees', () => {
  expect(moneyAmount(null)).toBe('未记录')
  expect(moneyAmount(null, '费用未知')).toBe('费用未知')
  expect(moneyAmount(0)).toBe('$0.0000')
  expect(moneyAmount(0.0000000123)).toBe('$1.2300e-8')
  expect(moneyAmount(0.00009999)).toBe('$9.9990e-5')
  expect(moneyAmount(0.0001)).toBe('$0.0001')
  expect(moneyAmount(0.18)).toBe('$0.1800')
})
