// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import fixture from './decisionSettingsReceiptFixture.json'
import { decisionSettingsCommandV1Schema } from '../../../../packages/contracts_ts/src/generated/r2'
import { bootstrapSettingsSession, settingsApi, type SettingsIntent } from './decisionSettings'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })
function setup(value: unknown = fixture.receipt) {
  vi.stubGlobal('crypto', webcrypto)
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: 'binding-owner', csrf_token: 'private-csrf', expires_at: '2099-01-01T00:00:00Z' })
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(value), { status: 200 })))
  return { owner_id: fixture.intent.owner_id, command: decisionSettingsCommandV1Schema.parse(fixture.intent.command) } as SettingsIntent
}
it('accepts the original persisted Python command receipt and refuses a changed original payload', async () => {
  const original = setup()
  expect((await settingsApi.receipt(original)).command_hash).toBe('6464e07005228fd17fa0a1b167bcde6d05c0a9893c782cf7c22b5e5d73cd12e1')
  const changed = structuredClone(original)
  changed.command.expected_state_version = 1
  await expect(settingsApi.receipt(changed)).rejects.toThrow('decision_settings_receipt_identity_mismatch')
})
it('refuses a structurally valid response for a different owner or request', async () => {
  const original = setup({ ...fixture.receipt, request_id: 'other-request' })
  await expect(settingsApi.receipt(original)).rejects.toThrow('decision_settings_receipt_identity_mismatch')
})
it('automatically consumes the launcher fragment without leaving its credential in history or storage', async () => {
  window.history.replaceState(null, '', '#/home?settings_pairing=' + 'b'.repeat(64))
  const paired = { owner_id: 'owner', csrf_token: 'csrf', expires_at: '2099-01-01T00:00:00Z' }
  vi.spyOn(settingsApi, 'pair').mockImplementation(async () => { window.dispatchEvent(new Event('decision-hub-settings-session-changed')); return paired })
  const changed = vi.fn(); window.addEventListener('decision-hub-settings-session-changed', changed)
  try {
    await bootstrapSettingsSession()
    expect(window.location.hash).toBe('#/home')
    expect(changed).toHaveBeenCalledTimes(1)
    expect([...Array(sessionStorage.length)].map((_, index) => sessionStorage.getItem(sessionStorage.key(index)!)).join('')).not.toContain('b'.repeat(64))
  } finally { window.removeEventListener('decision-hub-settings-session-changed', changed) }
})
