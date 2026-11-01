import { z } from 'zod'
import { decisionActionContextV2Schema, decisionSourceRefV1Schema, decisionReviewOriginalsV1Schema } from '@decision-hub/contracts-ts'

/** Transport envelope only; generated DTOs own every financial/ref body. */
export const nativePerpetualBindingReplySchema = z.object({
  schema_version: z.literal('decision-v2-native-binding.v1'), run_id: z.string().min(1),
  request_hash: z.string().regex(/^[a-f0-9]{64}$/), generation: z.number().int().min(1), binding_ref: decisionSourceRefV1Schema,
  opportunity_id: z.string().min(1), context: decisionActionContextV2Schema,
  candidate_target_ref: decisionSourceRefV1Schema, anchor_rule_ref: decisionSourceRefV1Schema,
  method_ref: decisionSourceRefV1Schema, method_code_hash: z.string().regex(/^[a-f0-9]{64}$/),
  review_originals: decisionReviewOriginalsV1Schema,
}).strict()
