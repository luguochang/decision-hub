import { z } from 'zod'
import { actionExecutionReceiptV1Schema, actionExecutionProtocolV1Schema, actionPaperCheckpointV1Schema, actionExecutionEpisodeV1Schema, actionOutcomeV2Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { ACTION_OWNER, type ActionHorizon } from './actionPlans'
import { request } from './client'

const key = z.string().min(1).max(128)
const hash = z.string().regex(/^[a-f0-9]{64}$/)
const time = z.string().datetime({ offset: true })
const source = z.object({ opportunity_id: key, run_id: key, scope_id: z.string(), horizon: z.enum(['30m', '24h', '72h']), method_hash: hash, original_ref: key, original_hash: hash }).strict()
export const paperCommandSchema = z.object({ request_id: key, source_opportunity_id: key, scenario: z.enum(['cash', 'existing_long']) }).strict()
export type PaperCommand = z.infer<typeof paperCommandSchema>
export const paperRegistrationSchema = z.object({ schema_version: z.literal('action-paper-registration.v1'), owner_id: key, request_id: key, command_hash: hash, source, scenario: z.enum(['cash', 'existing_long']), portfolio_id: key, protocol: actionExecutionProtocolV1Schema, accepted_at: time, payload_hash: hash }).strict()
export type PaperRegistration = z.infer<typeof paperRegistrationSchema>
export const paperOptionsSchema = z.object({ schema_version: z.literal('action-paper-options.v1'), owner_id: key, scope_id: z.string(), horizon: z.enum(['30m', '24h', '72h']), enabled: z.boolean(), source: source.nullable(), registrations: z.array(paperRegistrationSchema).max(20), registrations_more: z.boolean(), active_request_ids: z.array(key), gap_codes: z.array(z.string()) }).strict()
export const paperStatusSchema = z.object({ schema_version: z.literal('action-paper-status.v1'), registration: paperRegistrationSchema, bootstrap: z.object({ schema_version: z.literal('action-paper-bootstrap.v1'), registration_hash: hash, portfolio_id: key, status: z.enum(['opened', 'unknown']), reason: z.string().nullable(), checkpoint_ref: key.nullable(), checkpoint_hash: hash.nullable(), decided_at: time, payload_hash: hash }).strict().nullable(), state: z.enum(['waiting_start', 'waiting_opening', 'unknown', 'running', 'ended']), account: actionPaperCheckpointV1Schema.nullable(), episode: actionExecutionEpisodeV1Schema.nullable(), pending_intents: z.number().int().nonnegative(), outcomes_count: z.number().int().nonnegative(), outcomes: z.array(actionOutcomeV2Schema).max(3), execution_reasons: z.record(z.string().nullable()), opportunities_count: z.number().int().nonnegative(), unresolved_inventory: z.boolean(), financial_qualified: z.literal(false), observed_at: time }).strict()
export type PaperStatus = z.infer<typeof paperStatusSchema>

const headers = { 'X-Owner-Id': ACTION_OWNER }
const base = '/v1/actions/paper'
const identity = (value: PaperRegistration, id: string, scope?: string, horizon?: string) => {
  if (value.owner_id !== ACTION_OWNER || value.request_id !== id || (scope && value.source.scope_id !== scope) || (horizon && value.source.horizon !== horizon) || value.protocol.scopes.length !== 1 || value.protocol.scopes[0] !== value.source.scope_id || value.protocol.primary_horizon !== value.source.horizon) throw new Error('模拟验证回执身份不匹配，请重新读取。')
  return value
}
export const actionPaperApi = {
  options: async (scopeId: string, horizon: ActionHorizon) => {
    const value = paperOptionsSchema.parse(await request<unknown>(`${base}/options?${new URLSearchParams({ scope_id: scopeId, horizon })}`, { headers }))
    if (value.owner_id !== ACTION_OWNER || value.scope_id !== scopeId || value.horizon !== horizon || (value.source && (value.source.scope_id !== scopeId || value.source.horizon !== horizon))) throw new Error('模拟验证范围不匹配，请重新读取。')
    value.registrations.forEach(item => identity(item, item.request_id, scopeId, horizon))
    return value
  },
  register: async (command: PaperCommand) => {
    const value = identity(paperRegistrationSchema.parse(await request<unknown>(`${base}/registrations`, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(paperCommandSchema.parse(command)) })), command.request_id)
    if (value.source.opportunity_id !== command.source_opportunity_id || value.scenario !== command.scenario) throw new Error('模拟登记与原请求不一致，结果尚待核对。')
    return value
  },
  receipt: async (command: PaperCommand) => {
    const value = identity(paperRegistrationSchema.parse(await request<unknown>(`${base}/registrations/${encodeURIComponent(command.request_id)}`, { headers })), command.request_id)
    if (value.source.opportunity_id !== command.source_opportunity_id || value.scenario !== command.scenario) throw new Error('原登记回执不匹配。')
    return value
  },
  status: async (registration: PaperRegistration) => {
    const value = paperStatusSchema.parse(await request<unknown>(`${base}/registrations/${encodeURIComponent(registration.request_id)}/status`, { headers }))
    identity(value.registration, registration.request_id, registration.source.scope_id, registration.source.horizon)
    if (value.registration.payload_hash !== registration.payload_hash || (value.account && value.account.portfolio_id !== registration.portfolio_id) || (value.episode && value.episode.portfolio_id !== registration.portfolio_id) || value.outcomes.some(item => item.portfolio_id !== registration.portfolio_id) || (value.bootstrap && (value.bootstrap.portfolio_id !== registration.portfolio_id || value.bootstrap.registration_hash !== registration.payload_hash))) throw new Error('模拟账户或结果身份不匹配。')
    return value
  },
  outcomes: async (registration: PaperRegistration, after?: string) => {
    const value = z.object({ portfolio_id: key, items: z.array(z.object({ outcome: actionOutcomeV2Schema, receipt: actionExecutionReceiptV1Schema }).strict()).max(5), next_cursor: z.string().nullable(), financial_qualified: z.literal(false) }).strict().parse(await request<unknown>(`${base}/registrations/${encodeURIComponent(registration.request_id)}/outcomes${after ? `?after=${encodeURIComponent(after)}` : ''}`, { headers }))
    if (value.portfolio_id !== registration.portfolio_id || value.items.some(item => item.outcome.portfolio_id !== registration.portfolio_id || item.receipt.portfolio_id !== registration.portfolio_id || item.receipt.outcome_ref !== item.outcome.id || item.receipt.outcome_hash !== item.outcome.payload_hash)) throw new Error('执行结果原件与账户不匹配。')
    return value
  },
  opportunities: async (registration: PaperRegistration, after?: string) => {
    const value = z.object({ portfolio_id: key, items: z.array(z.object({ opportunity: z.object({ id: key, registered_at: time }).passthrough(), has_plan: z.boolean(), category: z.string(), observations: z.array(z.object({ status: z.string(), reason: z.string() }).passthrough()), observation_count: z.number().int(), observation_next_cursor: z.string().nullable(), is_trade_outcome: z.literal(false) }).strict()), next_cursor: z.string().nullable(), financial_qualified: z.literal(false) }).strict().parse(await request<unknown>(`${base}/registrations/${encodeURIComponent(registration.request_id)}/opportunities${after ? `?after=${encodeURIComponent(after)}` : ''}`, { headers }))
    if (value.portfolio_id !== registration.portfolio_id) throw new Error('机会记录不属于当前模拟账户。')
    return value
  },
}
