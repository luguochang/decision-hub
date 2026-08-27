// @vitest-environment jsdom

import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EvolutionPanel } from './App'
import type { EvolutionOverview } from './api/types'

const testGlobal = globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
testGlobal.IS_REACT_ACT_ENVIRONMENT = true

const overview: EvolutionOverview = {
  datasets: [],
  candidates: [{
    candidate_id: 'candidate-1',
    candidate_type: 'strategy',
    content_hash: 'a'.repeat(64),
    version: 'strategy.v2',
    parent_version: 'strategy.v1',
    status: 'candidate',
    created_at: '2026-08-28T01:00:00Z',
    content_ref: null,
    source: 'owner',
  }],
  experiments: [],
  results: [],
  pointers: [],
  failures: [],
  experiences: [],
  decisions: [],
}

let container: HTMLDivElement | null = null

afterEach(() => {
  container?.remove()
  container = null
})

describe('Promotion Desk', () => {
  it('requires a reason and explicit owner confirmation before Reject', async () => {
    container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    const onDecision = vi.fn(async () => ({}))
    const onReview = vi.fn(async () => { throw new Error('review should not run for reject') })

    await act(async () => {
      root.render(<EvolutionPanel evolution={overview} deciding={false} onReview={onReview} onDecision={onDecision} />)
    })
    const reject = container.querySelector<HTMLButtonElement>('[aria-label="Reject strategy.v2"]')
    expect(reject).not.toBeNull()
    await act(async () => { reject!.click() })

    const submit = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('确认Reject'))
    expect(submit?.disabled).toBe(true)
    const textarea = container.querySelector<HTMLTextAreaElement>('.reason-field textarea')
    const checkbox = container.querySelector<HTMLInputElement>('.confirm-row input')
    expect(textarea).not.toBeNull()
    expect(checkbox).not.toBeNull()

    await act(async () => {
      const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      valueSetter?.call(textarea, 'Evidence lineage is incomplete')
      textarea!.dispatchEvent(new Event('input', { bubbles: true }))
      checkbox!.click()
    })
    expect(submit?.disabled).toBe(false)
    await act(async () => { submit!.click() })

    expect(onReview).not.toHaveBeenCalled()
    expect(onDecision).toHaveBeenCalledWith(expect.objectContaining({
      candidate_id: 'candidate-1',
      decision: 'reject',
      evaluation_refs: [],
      expected_generation: 0,
      owner: 'owner',
      reason: 'Evidence lineage is incomplete',
    }))
    await act(async () => { root.unmount() })
  })
})
