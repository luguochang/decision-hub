// @vitest-environment jsdom
import { act, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { RouteContent } from './RouteContent'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

it('keeps navigation available during loading and recovers another route after a chunk failure', async () => {
  let fail!: (error: Error) => void
  const BrokenPage = lazy(() => new Promise<{ default: () => React.ReactNode }>((_resolve, reject) => { fail = reject }))
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
  try {
    await act(async () => root.render(<><nav><button>今日研究</button></nav><RouteContent scene="facts"><BrokenPage /></RouteContent></>))
    expect(container.querySelector('[role="status"]')?.textContent).toContain('正在载入页面')
    expect(container.querySelector('nav button')).not.toBeNull()
    await act(async () => fail(new Error('chunk unavailable')))
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('页面未能载入')
    expect(container.querySelector('[role="alert"] button')?.textContent).toBe('重新载入页面')
    expect(container.querySelector('nav button')).not.toBeNull()
    await act(async () => root.render(<><nav><button>今日研究</button></nav><RouteContent scene="home"><h1>今日研究</h1></RouteContent></>))
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.querySelector('h1')?.textContent).toBe('今日研究')
  } finally { await act(async () => root.unmount()); consoleError.mockRestore(); container.remove() }
})
