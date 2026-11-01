import { z } from 'zod'
import { researchRunViewSchema } from './schemas'

export const researchHomeSchema = z.object({
  latest_report: researchRunViewSchema.nullable(),
  latest_report_at: z.string().datetime({ offset: true }).nullable(),
  latest_report_headline: z.string().nullable(),
  latest_report_preview: z.object({
    run_id: z.string().min(1),
    artifact_id: z.string().min(1),
    kind: z.enum(['synthesis', 'perpetual', 'legacy', 'unverified', 'unavailable']),
    text: z.string().min(1),
  }).strict().nullable().optional(),
  latest_run: researchRunViewSchema.nullable(),
  active_runs: researchRunViewSchema.array().max(3),
  active_count: z.number().int().nonnegative(),
  generated_at: z.string().datetime({ offset: true }),
})
