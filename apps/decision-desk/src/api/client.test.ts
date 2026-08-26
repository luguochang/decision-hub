import { describe, expect, it } from 'vitest'
import { fallbackSummary } from './client'

describe('Decision Desk API fixtures', () => {
  it('keeps the UI contract focused on product views', () => {
    expect(fallbackSummary.inbox.latest).toHaveLength(2)
    expect(fallbackSummary.inbox.latest[0].gate_status).toBe('publish')
    expect(fallbackSummary.active_pack).toBe('crypto_macro.v1')
  })
})
