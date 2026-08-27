import { describe, expect, it } from 'vitest'
import { sourceHealthDisplay } from '../App'
import { fallbackSummary } from './client'
import { productHealthSchema } from './schemas'

describe('Decision Desk API fixtures', () => {
  it('keeps the UI contract focused on product views', () => {
    expect(fallbackSummary.inbox.latest).toHaveLength(2)
    expect(fallbackSummary.inbox.latest[0].gate_status).toBe('publish')
    expect(fallbackSummary.active_pack).toBe('crypto_macro.v1')
  })

  it('turns a degraded source into a compact health summary', () => {
    const health = productHealthSchema.parse({
      status: 'degraded',
      running_runs: 0,
      failed_runs: 0,
      sources: [
        {
          source_id: 'fed-press',
          status: 'healthy',
          cursor: '12',
          last_success_at: '2026-08-27T01:00:00Z',
          last_error_at: null,
          consecutive_failures: 0,
          latency_ms: 30,
          error_code: null,
          next_poll_at: '2026-08-27T01:01:00Z',
        },
        {
          source_id: 'bls-calendar',
          status: 'degraded',
          cursor: '9',
          last_success_at: null,
          last_error_at: '2026-08-27T01:00:00Z',
          consecutive_failures: 1,
          latency_ms: 1_000,
          error_code: 'source_timeout',
          next_poll_at: '2026-08-27T01:02:00Z',
        },
      ],
    })

    expect(sourceHealthDisplay(health)).toEqual({
      healthyCount: 1,
      degraded: [health.sources[1]],
      detail: 'bls-calendar: source_timeout',
    })
  })
})
