import type { Context } from '@deepseek-ai/cordis'
import {
  errorProvenanceSchema,
  researchCapabilityQuerySchema,
  researchCapabilityResultSchema,
} from '@decision-hub/contracts-ts'
import { defineTool, type ToolDefinition } from '@deepseek-ai/dsh-tools'
import z from '@deepseek-ai/schemastery'

export const name = 'decision-hub-research-tool'
export const inject = ['tools']
export const RESEARCH_TOOL_NAME = 'decision_hub_research'
export const DEFAULT_RESEARCH_TOOL_TIMEOUT_MS = 25_000

export interface ResearchToolConfig {
  serviceUrl: string
  authKey: string
  timeoutMs: number
  fetchImpl?: typeof fetch
}

export const Config: z<Omit<ResearchToolConfig, 'fetchImpl'>> = z.object({
  serviceUrl: z.string(),
  authKey: z.string(),
  // The Gateway owns the 20s capability deadline. Keep a small transport grace
  // so its typed ErrorProvenance reaches DSH before the wrapper times out.
  timeoutMs: z.natural().min(1).default(DEFAULT_RESEARCH_TOOL_TIMEOUT_MS),
})

const parameters = {
  request_id: { type: 'string', required: true },
  capability_id: { type: 'string', required: true },
  requirement_id: { type: 'string', required: true },
  query: { type: 'string', required: true },
  target_url: { oneOf: [{ type: 'string' }, { type: 'null' }], required: true },
  symbols: { type: 'array', items: { type: 'string' }, required: true },
  fields: { type: 'array', items: { type: 'string' }, required: true },
  allowed_domains: { type: 'array', items: { type: 'string' }, required: true },
  max_results: { type: 'integer', required: true },
  max_cost_usd: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
  event_id: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  event_at: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  window_start_at: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  window_end_at: { oneOf: [{ type: 'string' }, { type: 'null' }] },
  requested_event_offsets: { type: 'array', items: { type: 'string' } },
  round: { type: 'integer', required: true },
  mode: { type: 'string', enum: ['live', 'replay'], required: true },
  observed_at: { type: 'string', required: true },
  cutoff_at: { type: 'string', required: true },
} as const

export function createResearchToolDefinition(options: ResearchToolConfig): ToolDefinition {
  const serviceUrl = new URL(options.serviceUrl)
  if (!['http:', 'https:'].includes(serviceUrl.protocol)) {
    throw new Error('decision_hub_research_service_url_invalid')
  }
  if (options.authKey.length === 0) throw new Error('decision_hub_research_auth_key_missing')
  if (!Number.isInteger(options.timeoutMs) || options.timeoutMs < 1) {
    throw new Error('decision_hub_research_timeout_invalid')
  }
  const fetchImpl = options.fetchImpl ?? fetch
  return defineTool({
    name: RESEARCH_TOOL_NAME,
    description: (
      'Execute one audited Decision Hub research capability. Session identity is supplied '
      + 'by the trusted DSH execution context; never include or infer a Session identifier.'
    ),
    parameters,
    output: {
      // Canonical Zod validation below is generated from the schema source. The
      // DSH output layer uses explicit JsonValue to avoid maintaining a second schema.
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
    },
    timeoutMs: options.timeoutMs,
    async execute(args, exec) {
      if (exec.agent === undefined) throw new Error('decision_hub_research_agent_required')
      if (Object.prototype.hasOwnProperty.call(args, 'research_session_id')) {
        throw new Error('decision_hub_research_session_identity_forbidden')
      }
      const query = researchCapabilityQuerySchema.parse({
        schema_version: 'research-capability-query.v1',
        ...args,
        research_session_id: exec.agent.id,
      })
      const response = await fetchImpl(serviceUrl, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'x-decision-hub-bridge-key': options.authKey,
        },
        body: JSON.stringify(query),
        signal: AbortSignal.any([exec.signal, AbortSignal.timeout(options.timeoutMs)]),
      })
      const payload: unknown = await response.json()
      if (!response.ok) {
        const provenance = errorProvenanceSchema.safeParse(payload)
        throw new Error(
          provenance.success
            ? `${provenance.data.error_code} provenance=${JSON.stringify(provenance.data)}`
            : `decision_hub_research_http_${response.status}`,
        )
      }
      return researchCapabilityResultSchema.parse(payload)
    },
  })
}

export function apply(ctx: Context, config: Omit<ResearchToolConfig, 'fetchImpl'>): void {
  ctx.tools.register(createResearchToolDefinition(config))
}
