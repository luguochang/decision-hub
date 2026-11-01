import { z } from 'zod'
import { researchProviderToolEmissionRecordedSchema } from '@decision-hub/contracts-ts'

/** Authenticated Hub diagnostics, not permission to publish or trade. */
export const actionSynthesisPreflightSchema = z.object({
  schema_version: z.literal('action-synthesis-preflight.v1'),
  emission: researchProviderToolEmissionRecordedSchema,
  catalog: z.object({
    schema_version: z.literal('research-action-catalog.v1'),
    scope_id: z.string().min(1),
    instrument: z.enum(['BTC-USDT', 'ETH-USDT']),
    qualification: z.literal('research_trial_only'),
  }).passthrough(),
  catalog_hash: z.string().regex(/^[a-f0-9]{64}$/),
  issues: z.array(z.object({
    path: z.string().min(1), code: z.string().min(1),
    actual: z.unknown(), expected: z.unknown(),
  }).strict()).max(96),
  issues_truncated: z.boolean(),
}).strict()

export type ActionSynthesisPreflight = z.infer<typeof actionSynthesisPreflightSchema>
export type ActionCatalogContext = Pick<ActionSynthesisPreflight, 'catalog' | 'catalog_hash'>
