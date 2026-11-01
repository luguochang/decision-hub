import { expect, it } from 'vitest'
import { enhancementRoutes, enhancementViewSchema } from './marketEnhancements'

const at = '2026-10-08T04:00:00Z'
const value = { schema_version: 'market-enhancements-view.v1', scope_id: 'crypto:spot:okx:BTC-USDT:trade:1m',
  instrument: 'BTC-USDT', as_of: at, integrity: 'stored_payload_hash',
  routes: enhancementRoutes.map(route => ({ route, registration_id: null, registered_at: null, data_status: 'not_registered',
    admission_state: 'not_assessed', receipt_id: null, saved_at: null, cutoff_at: null, source_known_at: null,
    observation_date: null, features: [], missing_reasons: ['enhancement_not_registered'], original_count: 0,
    excluded_source_count: 0, scan_truncated: false, data_pit_verified: null, probability: null,
    probability_qualified: false, statistical_status: 'insufficient_evidence', promotion: false })),
}
it('accepts strict saved unknown states and rejects duplicate routes or cross-asset identities', () => {
  expect(enhancementViewSchema.parse(value).routes).toHaveLength(3)
  expect(() => enhancementViewSchema.parse({ ...value, routes: [value.routes[0], value.routes[0], value.routes[2]] })).toThrow()
  expect(() => enhancementViewSchema.parse({ ...value, instrument: 'ETH-USDT' })).toThrow()
})
it('rejects invented promotion, probabilities and non-finite values', () => {
  expect(() => enhancementViewSchema.parse({ ...value, routes: value.routes.map(item => ({ ...item, promotion: true })) })).toThrow()
  expect(() => enhancementViewSchema.parse({ ...value, routes: value.routes.map(item => ({ ...item, probability: 0.8 })) })).toThrow()
  expect(() => enhancementViewSchema.parse({ ...value, routes: value.routes.map(item => ({ ...item,
    features: [{ name: 'test', value: NaN, unit: 'index', status: 'available', reason_code: null }] })) })).toThrow()
})
