// Null is missing data; a recorded zero is a real amount. Keep tiny nonzero
// amounts distinct from zero while exact values remain in the raw receipt.
export function moneyAmount(value: number | null, missing = '未记录') {
  if (value === null) return missing
  return `$${Math.abs(value) > 0 && Math.abs(value) < 0.0001 ? value.toExponential(4) : value.toFixed(4)}`
}
