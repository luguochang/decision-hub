import { z } from 'zod'

export const dataJobViewSchema = z.object({
  schema_version: z.literal('data-job.v1'),
  job_id: z.string(),
  dataset_ids: z.array(z.string()),
  window_start_at: z.string(),
  window_end_at: z.string(),
  status: z.enum(['queued', 'running', 'partial', 'failed', 'completed']),
  attempt: z.number().int(),
  partition_refs: z.array(z.string()),
  coverage_manifest_ref: z.string().nullable(),
  errors: z.array(z.string()),
  created_at: z.string(),
  updated_at: z.string(),
  finished_at: z.string().nullable(),
}).passthrough()

export type DataJobView = z.infer<typeof dataJobViewSchema>
