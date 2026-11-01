import { afterEach, expect, it, vi } from 'vitest'
import { researchPreparationDeadlineAtMs } from '../src/host/baseline.js'

afterEach(() => vi.useRealTimers())
it.each(['correlation', 'link', 'outer'] as const)('keeps the earlier original %s ceiling; neither 60s nor model-step 600s is a preparation cap', earlier => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-15T17:00:00Z'))
  const start = Date.now(), early = start + 800_000, late = start + 1_800_000
  const result = researchPreparationDeadlineAtMs({
    correlationDeadlineAt: new Date(earlier === 'correlation' ? early : late).toISOString(),
    linkDeadlineAt: new Date(earlier === 'link' ? early : late).toISOString(),
    outerRemainingMs: earlier === 'outer' ? 800_000 : 1_800_000, legacyMs: 30_000,
  })
  expect(result).toBe(early)
  vi.setSystemTime(start + 700_000)
  // The captured absolute ceiling is passed forward unchanged, never now+800s.
  expect(result - Date.now()).toBe(100_000)
})
it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])('rejects invalid outer budget %s rather than silently replacing it', outerRemainingMs => {
  expect(() => researchPreparationDeadlineAtMs({ correlationDeadlineAt: new Date(Date.now() + 1_800_000).toISOString(),
    outerRemainingMs, legacyMs: 30_000 })).toThrow('dsh_host_read_deadline_elapsed')
})
it('retains legacy operation timeout only when no original absolute deadline exists', () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-15T17:00:00Z'))
  expect(researchPreparationDeadlineAtMs({ legacyMs: 30_000 })).toBe(Date.now() + 30_000)
})
