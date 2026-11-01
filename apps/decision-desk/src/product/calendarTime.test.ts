import { describe, expect, it } from 'vitest'
import { macroEventViewSchema } from '../api/schemas'
import { calendarInNextDays, calendarTime } from './calendarTime'

const event = macroEventViewSchema.parse({
  schema_version: 'macro-event-view.v1', macro_event_id: 'one', family: 'fomc',
  scheduled_at: null, scheduled_date: '2026-10-28', time_precision: 'date',
  source_timezone: 'America/New_York', source_id: 'fed-calendar', source_url: null,
  source_authority: 'official', unit: null, prior_value: null, consensus_value: null,
  actual_value: null, surprise: null, surprise_z: null, revision_flag: false,
  status: 'scheduled', updated_at: '2026-09-29T00:00:00Z',
})

describe('official calendar time precision', () => {
  it('keeps the source day and never renders a fabricated midnight', () => {
    expect(calendarTime(event)).toBe('2026-10-28 · 具体时间待公布')
    expect(calendarInNextDays(event, 30, Date.parse('2026-09-29T12:00:00Z'))).toBe(true)
  })
  it('identifies legacy or naive time as unverified', () => {
    expect(calendarTime({ ...event, time_precision: undefined })).toContain('待核实')
    expect(calendarTime({ ...event, time_precision: 'exact', scheduled_at: '2026-10-28T12:00:00' })).toBe('发布时间待核实')
  })
  it('formats a confirmed instant with an explicit zone', () => {
    expect(calendarTime({ ...event, time_precision: 'exact', scheduled_at: '2026-10-28T12:30:00Z' })).not.toContain('待')
  })
})
