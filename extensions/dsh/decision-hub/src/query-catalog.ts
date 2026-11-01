import { createHash } from 'node:crypto'
import { z } from 'zod'

const identifier = z.string().min(1).max(128)
const domains = z.array(z.string().regex(/^[a-z0-9.-]+$/).max(253)).max(64)
const modes = z.array(z.enum(['live', 'replay'])).max(2)
const catalogSchema = z.object({
  schema_version: z.literal('research-query-catalog.v1'),
  catalog_hash: z.string().regex(/^[a-f0-9]{64}$/),
  scope: z.object({
    run_id: identifier, dsh_session_id: identifier,
    request_hash: z.string().regex(/^[a-f0-9]{64}$/), generation: z.number().int().positive(),
    status: z.enum(['bound', 'legacy_unbound']),
    method_hash: z.string().regex(/^[a-f0-9]{64}$/).nullable(),
    execution_mode: z.enum(['live', 'replay']).nullable(),
    // Frozen Method/Link ceiling, included in catalog_hash. Optional only for
    // older/unbound catalogs; managed-live execution requires it before POST.
    deadline_at: z.string().datetime({ offset: true }).nullable().optional(),
  }).strict().optional(),
  capabilities: z.array(z.object({
    capability_id: identifier,
    allowed_domains: domains,
    modes,
    routes: z.array(z.object({
      provider_id: identifier.nullable(),
      allowed_domains: domains,
      fields: z.array(identifier).max(128).nullable(),
      modes,
      requires_event_window: z.boolean(),
      symbols: z.array(identifier).max(256).nullable().optional(),
      symbols_required: z.boolean().nullable().optional(),
      max_symbols: z.number().int().positive().nullable().optional(),
      symbol_fields: z.record(z.array(identifier).max(128)).nullable().optional(),
      sources: z.array(z.object({
        source_ref: identifier, domains, allowed_paths: z.array(z.string().max(2048)).max(128),
        requirement_ids: z.array(identifier).max(128),
        excluded_paths: z.array(z.string().regex(/^\/[^?#]*$/).max(2048)).max(128).optional(),
        match_paths_exactly: z.boolean().optional(),
      }).strict()).max(256).nullable().optional(),
      target_url_required: z.boolean().nullable().optional(),
      evidence_ref_required: z.boolean().nullable().optional(),
      usage_notes: z.string().max(4096).nullable().optional(),
    }).strict()).max(64),
  }).strict()).max(64),
}).strict()

export type ResearchQueryCatalog = z.infer<typeof catalogSchema>
// Catalog preparation is an authenticated local read, with its own finite
// contract. It does not borrow the 65s external research-tool execution budget.
export const DEFAULT_RESEARCH_CATALOG_TIMEOUT_MS = 30_000
type CatalogTransportPhase = 'fetch' | 'response_body'

function catalogLifetime<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const abort = () => {
      signal.removeEventListener('abort', abort)
      reject(signal.reason)
    }
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
    operation.then(value => {
      signal.removeEventListener('abort', abort)
      resolve(value)
    }, error => {
      signal.removeEventListener('abort', abort)
      reject(error)
    })
  })
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value !== null && typeof value === 'object') {
    return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`
  }
  return JSON.stringify(value)
}

export async function loadResearchQueryCatalog(options: {
  serviceUrl: string; authKey: string; fetchImpl?: typeof fetch; sessionId?: string
  catalogTimeoutMs?: number; signal?: AbortSignal
}): Promise<ResearchQueryCatalog> {
  const url = new URL(options.serviceUrl)
  if (!['http:', 'https:'].includes(url.protocol) || !url.pathname.endsWith('/execute')) {
    throw new Error('research_catalog_service_url_invalid')
  }
  url.pathname = url.pathname.replace(/\/execute$/, '/catalog')
  url.search = ''
  url.hash = ''
  if (options.sessionId) url.searchParams.set('research_session_id', options.sessionId)
  const budget = options.catalogTimeoutMs ?? DEFAULT_RESEARCH_CATALOG_TIMEOUT_MS
  if (!Number.isSafeInteger(budget) || budget < 1 || budget > DEFAULT_RESEARCH_CATALOG_TIMEOUT_MS) {
    throw new Error('research_catalog_budget_invalid')
  }
  const controller = new AbortController()
  const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal
  const timer = setTimeout(() => controller.abort(), budget)
  let phase: CatalogTransportPhase = 'fetch'
  const transport = async <T>(operation: () => Promise<T>): Promise<T> => {
    try {
      signal.throwIfAborted()
      const result = await catalogLifetime(operation(), signal)
      signal.throwIfAborted()
      return result
    } catch {
      const code = options.signal?.aborted ? 'research_catalog_transport_cancelled'
        : controller.signal.aborted ? 'research_catalog_transport_timeout'
        : 'research_catalog_transport_unavailable'
      // Only this exact code/phase reaches the native error chain. Never copy
      // arbitrary transport messages, URLs, headers or credentials into it.
      // The timeout budget is local, bounded configuration rather than remote
      // text. Preserve it in the native turn error so the authenticated
      // terminal receipt can carry the actual deadline into ErrorProvenance.
      const deadline = code === 'research_catalog_transport_timeout'
        ? ` deadline_ms=${budget}` : ''
      throw new Error(`${code} phase=${phase}${deadline}`)
    }
  }
  try {
    const response = await transport(() => (options.fetchImpl ?? fetch)(url, {
      headers: { 'x-decision-hub-bridge-key': options.authKey }, signal, redirect: 'error',
    }))
    if (!response.ok) throw new Error(`research_catalog_http_${response.status}`)
    phase = 'response_body'
    const body = await transport(() => response.text())
    if (body.length > 64_000) throw new Error('research_catalog_too_large')
    const catalog = catalogSchema.parse(JSON.parse(body))
    const { catalog_hash, ...content } = catalog
    if (createHash('sha256').update(canonical(content)).digest('hex') !== catalog_hash) {
      throw new Error('research_catalog_hash_mismatch')
    }
    if (options.sessionId && catalog.scope?.dsh_session_id !== options.sessionId) {
      throw new Error('research_catalog_scope_identity_mismatch')
    }
    if (catalog.scope?.status === 'legacy_unbound' && catalog.capabilities.length !== 0) {
      throw new Error('research_catalog_legacy_scope_invalid')
    }
    return await transport(() => Promise.resolve(catalog))
  } finally { clearTimeout(timer) }
}

export function renderResearchQueryCatalog(catalog: ResearchQueryCatalog): string {
  return ' Gateway query catalog (filtered to the frozen Run when scope is present; not evidence or execution authorization): '
    + JSON.stringify(catalog)
    + ' Use only capabilities allowed by this Run plan. fields are requested metric names, '
    + 'not response metadata such as venue, source_url or observed_at. Select fields supported '
    + 'together by one route; do not combine spot-only and derivatives-only field sets. '
    + 'fields=null in this catalog means unspecified, not arbitrary fields allowed. '
    + 'allowed_domains may narrow a route using its exact listed host or be empty for server '
    + 'selection; never replace an API host with its parent website domain. Keep requested '
    + 'symbols from the plan. Event-window routes require the existing event and offsets; '
    + 'Select symbols, fields and domains supported together by the SAME route. '
    + 'A symbols list is the adapter allowlist; null is unknown, not unrestricted. '
    + 'Honor symbols_required and max_symbols; never send [] when symbols are required. '
    + 'For historical datasets use symbol_fields for that exact dataset and an explicit window. '
    + 'For document URLs match the listed source domain, path AND requirement_id; '
    + 'If match_paths_exactly=true, allowed_paths are exact paths, never prefixes; '
    + 'otherwise their existing prefix semantics apply. Always reject paths in excluded_paths '
    + 'even when an allowed prefix would match. Do not drop either restriction. These catalog '
    + 'hints do not grant admission: the server source registry still validates every fetch. '
    + 'web.fetch and browser.readback may have different source scopes. Do not relabel '
    + 'the requirement to evade a rejection. Missing retained documents require a new approved fetch; '
    + 'never invent evidence_ref or content_ref. Follow usage_notes. '
    + 'current snapshots use event_id=null and requested_event_offsets=[]. '
    + 'A rejected request remains a failed attempt; correcting invalid input requires a new '
    + 'call and never authorizes widening permissions, changing PIT or relabeling evidence.'
}
