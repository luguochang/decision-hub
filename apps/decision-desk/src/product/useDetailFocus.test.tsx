// @vitest-environment jsdom
import { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { useDetailFocus } from './useDetailFocus'

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let host: HTMLDivElement
let root: ReturnType<typeof createRoot>
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks() })
function Harness({ initial = null, update = 0, ready = true }: { initial?: string | null; update?: number; ready?: boolean }) {
  const [selected, setSelected] = useState(initial)
  const ref = useDetailFocus(selected, Boolean(selected) && ready)
  return <><button onClick={() => setSelected('record-1')}>查看判断</button>{selected && <section ref={ref} tabIndex={-1}><h2>详情 {update}</h2><input aria-label="笔记" /><button onClick={() => setSelected(null)}>收起详情</button></section>}</>
}
async function render(node: React.ReactNode) { await act(async () => root.render(node)) }
function setup() { host = document.createElement('div'); document.body.append(host); root = createRoot(host) }
it('focuses an opened detail once, preserves typing through data refresh, and returns to the opener on collapse', async () => {
  setup()
  await render(<Harness />)
  const opener = host.querySelector('button')!
  await act(async () => { opener.focus(); opener.click() })
  expect(document.activeElement).toBe(host.querySelector('section'))
  const input = host.querySelector('input')!
  input.focus()
  await render(<Harness update={1} />)
  expect(document.activeElement).toBe(input)
  await act(async () => { host.querySelector('section button')!.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
  expect(document.activeElement).toBe(opener)
})
it('focuses a directly linked detail on the initial mount', async () => {
  setup()
  await render(<Harness initial="linked-record" />)
  expect(document.activeElement).toBe(host.querySelector('section'))
})

it('waits for the selected async content before positioning and does not repeat on refresh', async () => {
  setup()
  await render(<Harness initial="linked-record" ready={false} />)
  expect(document.activeElement).not.toBe(host.querySelector('section'))
  await render(<Harness initial="linked-record" ready />)
  expect(document.activeElement).toBe(host.querySelector('section'))
  const input = host.querySelector('input')!
  input.focus()
  await render(<Harness initial="linked-record" ready={false} />)
  await render(<Harness initial="linked-record" ready />)
  expect(document.activeElement).toBe(input)
})
