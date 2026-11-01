// @vitest-environment jsdom
import { act } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { settingsApi } from '../api/decisionSettings'
import { DecisionSettingsPanel } from './DecisionSettingsPanel'
import observed from '../../../../artifacts/d2-settings-session-offset/session-wire-example.json'

;(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear() })

it('pairs and restores the observed HTTP 200 offset wire without changing its instant or spelling', async () => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-19T07:00:00Z'))
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(observed.response)))
  vi.stubGlobal('fetch', fetch)
  const value = await settingsApi.pair('test-only-pairing-token')
  expect(value).toEqual(observed.response)
  expect(settingsApi.session()).toEqual(observed.response)
  expect(Date.parse(value.expires_at)).toBe(Date.parse('2026-10-19T08:06:49Z'))
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][0]).toBe('/v1/decision-settings/owner-session')
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'same-origin' })
  vi.mocked(Date.now).mockReturnValue(Date.parse('2026-10-19T08:06:49Z'))
  expect(settingsApi.session()).toBeNull()
})

it('shows an identity response error for malformed session time rather than a number or leverage error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...observed.response, expires_at: 'not-a-date' }))))
  const host = document.createElement('div'); document.body.append(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><DecisionSettingsPanel /></QueryClientProvider>))
    const input = host.querySelector('input[type="password"]') as HTMLInputElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'test-only-pairing-token')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await act(async () => [...host.querySelectorAll('button')].find(button => button.textContent === '验证本机身份')!.click())
    expect(host.textContent).toContain('本机身份验证响应无法核验，请重新打开启动器创建的本机入口。')
    expect(host.textContent).not.toContain('金额、张数和杠杆')
    expect(settingsApi.session()).toBeNull()
  } finally { await act(async () => root.unmount()); client.clear(); host.remove() }
})
