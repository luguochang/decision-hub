/** Display only: retain exact ledger strings in the caller's detail view. */
export function ledgerAmount(value: string | null, digits = 2): string {
  if (value === null) return '未知'
  if (!Number.isInteger(digits) || digits < 0 || digits > 12) return '待核对'
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value)
  if (!match) return '待核对'
  const [, sign, whole, fraction = ''] = match
  const integer = whole.replace(/^0+(?=\d)/, '')
  const visible = fraction.padEnd(digits, '0').slice(0, digits)
  const remainder = /[1-9]/.test(fraction.slice(digits))
  const zeroVisible = !/[1-9]/.test(integer + visible)
  if (zeroVisible && remainder) {
    const unit = digits ? `0.${'0'.repeat(digits - 1)}1` : '1'
    return sign ? `介于 -${unit} 与 0` : `小于 ${unit}`
  }
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const number = `${zeroVisible ? '' : sign}${grouped}${digits ? `.${visible}` : ''}`
  return remainder ? `约 ${number}` : number
}
