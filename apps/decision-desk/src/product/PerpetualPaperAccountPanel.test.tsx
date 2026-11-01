// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiRequestError } from '../api/client'
import { settingsApi, type SettingsOptions } from '../api/decisionSettings'
import { perpetualPaperAccounts, makePaperAccountIntent, savePaperAccountIntent, paperAccountIntentKey } from '../api/perpetualPaperAccounts'
import { PerpetualPaperAccountPanel } from './PerpetualPaperAccountPanel'

const ref = { ref: 'btc-contract', hash: 'a'.repeat(64) }
const option = { instrument_id: 'okx:BTC-USDT-SWAP', instrument: ref, execution_profile: { ref: 'paper-profile', hash: 'b'.repeat(64) } }
const options = { owner_id: 'owner', instruments: [option], paper_accounts: [] } as unknown as SettingsOptions
async function settle() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) }) }
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear()
  vi.spyOn(settingsApi, 'session').mockReturnValue({ owner_id: 'owner', csrf_token: 'test-csrf', expires_at: '2099-01-01T00:00:00Z' })
  vi.spyOn(settingsApi, 'options').mockResolvedValue(options)
  vi.spyOn(perpetualPaperAccounts, 'list').mockResolvedValue({ schema_version: 'perpetual-paper-account-list.v1', owner_id: 'owner', accounts: [], next_after_account_id: null })
})
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear() })

async function page() {
  const host = document.createElement('div'); document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => root.render(<PerpetualPaperAccountPanel />)); await settle(); await settle()
  return { host, close: async () => { await act(async () => root.unmount()); host.remove() } }
}

it('starts without invented cash or automatic account creation', async () => {
  const create = vi.spyOn(perpetualPaperAccounts, 'create')
  const view = await page()
  try {
    expect(view.host.querySelector<HTMLInputElement>('input[aria-label="初始模拟资金"]')?.value).toBe('')
    expect(view.host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(false)
    expect([...view.host.querySelectorAll('button')].find(button => button.textContent === '创建模拟账户')?.disabled).toBe(true)
    expect(create).not.toHaveBeenCalled()
    expect(view.host.textContent).not.toContain('10,000')
  } finally { await view.close() }
})

it('reopening an unconfirmed create reads its original receipt and never resubmits', async () => {
  const intent = makePaperAccountIntent(options.instruments, '2500.00', 'manual_after_visible', true)
  savePaperAccountIntent(intent)
  const create = vi.spyOn(perpetualPaperAccounts, 'create')
  const read = vi.spyOn(perpetualPaperAccounts, 'receipt').mockRejectedValue(new ApiRequestError('perp_paper_command_missing', 'http', 404))
  const view = await page()
  try {
    expect(read).toHaveBeenLastCalledWith(intent)
    expect(create).not.toHaveBeenCalled()
    expect(view.host.textContent).toContain('原创建请求尚未确认')
    expect(localStorage.getItem(paperAccountIntentKey('owner'))).toContain(intent.command.request_id)
    const button = [...view.host.querySelectorAll('button')].find(item => item.textContent === '核对原创建回执')!
    await act(async () => button.click()); await settle()
    expect(read).toHaveBeenCalledTimes(2)
    expect(create).not.toHaveBeenCalled()
  } finally { await view.close() }
})

it('confirmed account receipt remains separate from execution readiness', async () => {
  const intent = makePaperAccountIntent(options.instruments, '2500', 'server_auto_paper', true)
  savePaperAccountIntent(intent)
  const receipt = { schema_version: 'perpetual-paper-account-create-receipt.v1' as const, owner_id: 'owner', request_id: intent.command.request_id,
    command_hash: 'c'.repeat(64), account: { schema_version: 'perpetual-paper-account-summary.v1' as const, owner_id: 'owner', account_id: intent.command.account_id,
      profile: intent.command.profile, instrument_refs: { 'okx:BTC-USDT-SWAP': ref }, state_version: 1, revision: { ref: 'account-revision', hash: 'd'.repeat(64) },
      execution_mode: 'server_auto_paper' as const, execution_enabled: false, created_at: '2026-10-19T00:00:00Z' } }
  vi.spyOn(perpetualPaperAccounts, 'receipt').mockResolvedValue(receipt)
  const create = vi.spyOn(perpetualPaperAccounts, 'create')
  const view = await page()
  try {
    expect(view.host.textContent).toContain('原创建回执已确认')
    expect(view.host.textContent).toContain('纸面执行尚未就绪')
    expect(create).not.toHaveBeenCalled()
    expect(view.host.textContent).not.toContain('已成交')
  } finally { await view.close() }
})

it('keeps the confirmed account visible and requires fresh input before another account', async () => {
  const intent = makePaperAccountIntent(options.instruments, '2500', 'server_auto_paper', true)
  savePaperAccountIntent(intent)
  const receipt = { schema_version: 'perpetual-paper-account-create-receipt.v1' as const, owner_id: 'owner', request_id: intent.command.request_id,
    command_hash: 'c'.repeat(64), account: { schema_version: 'perpetual-paper-account-summary.v1' as const, owner_id: 'owner', account_id: intent.command.account_id,
      profile: intent.command.profile, instrument_refs: { 'okx:BTC-USDT-SWAP': ref }, state_version: 1, revision: { ref: 'account-revision', hash: 'd'.repeat(64) },
      execution_mode: 'server_auto_paper' as const, execution_enabled: false, created_at: '2026-10-19T00:00:00Z' } }
  vi.spyOn(perpetualPaperAccounts, 'list').mockResolvedValue({ schema_version: 'perpetual-paper-account-list.v1', owner_id: 'owner', accounts: [receipt.account], next_after_account_id: null })
  vi.spyOn(perpetualPaperAccounts, 'receipt').mockResolvedValue(receipt)
  const create = vi.spyOn(perpetualPaperAccounts, 'create')
  const view = await page()
  try {
    const another = [...view.host.querySelectorAll('button')].find(item => item.textContent === '再建一个模拟账户')!
    expect(another).toBeDefined()
    await act(async () => another.click()); await settle()
    expect(view.host.textContent).toContain('模拟账户 1')
    expect(view.host.textContent).toContain('已保存的账户和原创建回执仍保留')
    expect(localStorage.getItem(paperAccountIntentKey('owner'))).toBeNull()
    expect(view.host.querySelector<HTMLInputElement>('input[aria-label="初始模拟资金"]')?.value).toBe('')
    expect(view.host.querySelector<HTMLInputElement>('input[type="checkbox"]')?.checked).toBe(false)
    expect(view.host.querySelector<HTMLSelectElement>('select[aria-label="模拟方式"]')?.value).toBe('manual_after_visible')
    expect([...view.host.querySelectorAll('button')].find(item => item.textContent === '创建模拟账户')?.disabled).toBe(true)
    expect(create).not.toHaveBeenCalled()
  } finally { await view.close() }
})
