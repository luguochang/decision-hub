import { timingSafeEqual } from 'node:crypto'

export const HOST_KEY_HEADER = 'x-decision-hub-host-key'

export function secretMatches(provided: string | string[] | undefined, expected: string): boolean {
  if (typeof provided !== 'string' || expected.length === 0) return false
  const left = Buffer.from(provided)
  const right = Buffer.from(expected)
  return left.length === right.length && timingSafeEqual(left, right)
}
