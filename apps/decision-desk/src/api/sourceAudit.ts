import { z } from 'zod'

export const sourcePollAttemptSchema = z.object({
  attempt_id: z.string(),
  source_id: z.string(),
  trigger_type: z.string(),
  status: z.string(),
  started_at: z.string(),
  completed_at: z.string().nullable(),
  cursor_before: z.string().nullable(),
  cursor_after: z.string().nullable(),
  received_count: z.number().int().nonnegative(),
  accepted_count: z.number().int().nonnegative(),
  duplicate_count: z.number().int().nonnegative(),
  revision_count: z.number().int().nonnegative(),
  latency_ms: z.number().int().nonnegative().nullable(),
  error_code: z.string().nullable(),
  content_snapshot: z.object({
    http_status: z.number().int().nullable(), parser_version: z.string(),
    content_hash: z.string().nullable(), parse_status: z.enum(['valid', 'invalid', 'not_attempted']),
    parsed_count: z.number().int().nonnegative().nullable(),
    upcoming_count: z.number().int().nonnegative().nullable(),
    first_upcoming_date: z.string().nullable(), last_upcoming_date: z.string().nullable(),
    coverage_status: z.enum(['available', 'no_upcoming', 'unknown']),
  }).nullable().optional(),
})

export type SourcePollAttempt = z.infer<typeof sourcePollAttemptSchema>
