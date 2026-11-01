import { describe, expect, it } from 'vitest'
import { hasExactReviewerPrompt } from '../src/reviewer-system-snapshot.js'

// Explicitly synthetic native sessions; the parent is supplied independently of the child.
const baseline = (id = 'child-baseline'): any => ({ seq: 8, type: 'user/message', data: {
  role: 'user', id, source: { kind: 'agent-instructions', form: 'instructions', baseline: true,
    baselineIdentity: 'synthetic-config', changes: [{ action: 'set', scope: '.', path: 'AGENTS.md', digest: 'a'.repeat(40) }] },
  content: [{ type: 'text', text: 'Synthetic workspace instructions.' }] } })
const child = (): any[] => [{ seq: 7, type: 'user/message', data: { content: [{ type: 'text', text: 'exact product' }] } }, baseline(),
  { seq: 10, type: 'assistant/message', data: { message: { content: [{ type: 'text', text: 'needs_revision' }] } } }]
const parent = (): any[] => [baseline('parent-baseline'), { seq: 20, type: 'tool/call', data: { callId: 'review', name: 'subagent_reviewer' } }]
// Fourth argument is the exact authenticated original reviewer-call seq, never finalize seq.
const check = (events: any[], originals = parent(), cutoff = 20) => (hasExactReviewerPrompt as any)(events, 'exact product', originals, cutoff)

describe('independently authenticated parent instruction baseline', () => {
  it('accepts exactly matching source and content with distinct native message ids', () => {
    const events = child(), originals = parent(), before = JSON.stringify(events)
    expect(check(events, originals)).toBe(true)
    expect(JSON.stringify(events)).toBe(before)
  })
  it('retains the legacy no-baseline path', () => {
    expect(check(child().filter(e => e.seq !== 8))).toBe(true)
  })
  it.each(['missing', 'duplicate', 'delta', 'after', 'foreign', 'content', 'metadata'])('rejects %s parent authority', mode => {
    const originals = parent()
    if (mode === 'missing') originals.shift()
    if (mode === 'duplicate') originals.splice(1, 0, baseline('duplicate'))
    if (mode === 'delta') originals[0].data.source.baseline = false
    if (mode === 'after') originals[0].seq = 21
    if (mode === 'foreign') originals[0].data.source.kind = 'user'
    if (mode === 'content') originals[0].data.content[0].text += ' changed'
    if (mode === 'metadata') originals[0].data.source.baselineIdentity += ' changed'
    expect(check(child(), originals)).toBe(false)
  })
  it.each(['duplicate', 'before', 'late', 'role', 'id', 'delta', 'foreign', 'extra-key', 'change', 'content', 'extra-prompt'])('rejects %s child frame', mode => {
    const events = child(), data = events[1].data
    if (mode === 'duplicate') events.splice(2, 0, baseline())
    if (mode === 'before') events.unshift(events.splice(1, 1)[0])
    if (mode === 'late') events.push(events.splice(1, 1)[0])
    if (mode === 'role') data.role = 'assistant'
    if (mode === 'id') data.id = ''
    if (mode === 'delta') data.source.baseline = false
    if (mode === 'foreign') data.source.kind = 'user'
    if (mode === 'extra-key') data.source.trusted = true
    if (mode === 'change') data.source.changes[0].action = 'replace'
    if (mode === 'content') data.content[0].text += ' changed'
    if (mode === 'extra-prompt') events.splice(2, 0, structuredClone(events[0]))
    expect(check(events)).toBe(false)
  })
  it('ignores later parent changes at the original cutoff but rejects ambiguous pre-call history', () => {
    const originals = parent(), update = baseline('updated'); update.seq = 25; update.data.source.baseline = false
    originals.push(update)
    expect(check(child(), originals)).toBe(true)
    update.seq = 15
    expect(check(child(), originals)).toBe(false)
  })
  it('requires an independently supplied finite original cutoff', () => {
    expect((hasExactReviewerPrompt as any)(child(), 'exact product', parent())).toBe(false)
    expect(check(child(), parent(), NaN)).toBe(false)
  })
})
