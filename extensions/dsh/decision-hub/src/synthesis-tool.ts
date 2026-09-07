import type { Context } from '@deepseek-ai/cordis'
import {
  researchSynthesisCandidateSchema,
  type ResearchSynthesisCandidate,
} from '@decision-hub/contracts-ts'
import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools'

export const name = 'decision-hub-synthesis-tool'
export const inject = ['tools']
export const SYNTHESIS_TOOL_NAME = 'decision_hub_synthesis_submit'

const parameters = {
  candidate: {
    type: 'json',
    required: true,
    description: (
      'The complete research-synthesis-candidate.v1 object. It must use the exact '
      + 'request_id and Evidence IDs from the canonical request and successful research results.'
    ),
  },
} as const

/**
 * Capture synthesis through DSH's validated Tool boundary.
 *
 * The Zod validator is generated from the canonical YAML contract. Returning
 * the validated object as structured Tool output lets the Hub attest it from
 * the DSH trajectory even when the model adds prose to its final message.
 */
export function createSynthesisToolDefinition(): ToolDefinition {
  return defineTool({
    name: SYNTHESIS_TOOL_NAME,
    description: (
      'Submit the final Decision Hub research synthesis. Call this exactly once after all '
      + 'approved evidence work is complete. Invalid schema is a Tool error that must be corrected '
      + 'inside this DSH session; this Tool does not fetch data, publish, notify, or trade.'
    ),
    parameters,
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    async execute(args, exec): Promise<ResearchSynthesisCandidate> {
      if (exec.agent === undefined) throw new Error('decision_hub_synthesis_agent_required')
      return researchSynthesisCandidateSchema.parse(args.candidate)
    },
  })
}

export function apply(ctx: Context): void {
  ctx.tools.register(createSynthesisToolDefinition())
}
