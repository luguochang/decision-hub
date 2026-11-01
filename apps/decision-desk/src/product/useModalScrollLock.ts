import { useLayoutEffect } from 'react'

const overflowProperties = new Set(['overflow', 'overflow-x', 'overflow-y'])
type InlineProperty = { name: string; value: string; priority: string }
type LockState = {
  tokens: Set<symbol>
  targets: { element: HTMLElement; original: InlineProperty[] }[]
}
const documentLocks = new WeakMap<Document, LockState>()

function acquireScrollLock(document: Document): () => void {
  let state = documentLocks.get(document)
  if (!state) {
    const targets = [document.documentElement, document.body].map(element => ({
      element,
      original: Array.from({ length: element.style.length }, (_, index) => element.style.item(index))
        .filter(name => overflowProperties.has(name))
        .map(name => ({ name, value: element.style.getPropertyValue(name), priority: element.style.getPropertyPriority(name) })),
    }))
    state = { tokens: new Set(), targets }
    documentLocks.set(document, state)
    for (const { element } of targets) {
      element.style.setProperty('overflow-x', 'hidden', 'important')
      element.style.setProperty('overflow-y', 'hidden', 'important')
    }
  }
  const token = Symbol('modal-scroll-lock')
  state.tokens.add(token)
  const acquired = state
  return () => {
    if (!acquired.tokens.delete(token) || acquired.tokens.size > 0) return
    documentLocks.delete(document)
    for (const { element, original } of acquired.targets) {
      for (const name of overflowProperties) element.style.removeProperty(name)
      for (const { name, value, priority } of original) element.style.setProperty(name, value, priority)
    }
    // Overflow preserves the natural position. Do not scroll to a captured
    // coordinate here: navigation may have deliberately positioned a new page.
  }
}

export function useModalScrollLock(active = true): void {
  useLayoutEffect(() => {
    if (!active || typeof document === 'undefined' || !document.body) return
    return acquireScrollLock(document)
  }, [active])
}
