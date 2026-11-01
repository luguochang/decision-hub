import { useLayoutEffect, useRef, type RefObject } from 'react'

type ModalFocusOptions = {
  active?: boolean
  scopeKey: string
  initialFocusRef?: RefObject<HTMLElement | null>
  returnFocusRef?: RefObject<HTMLElement | null>
}
type FocusEntry = {
  token: symbol
  root: HTMLElement
  previous: HTMLElement | null
  lastFocused: HTMLElement | null
  current: () => ModalFocusOptions
  scopeKey: string
}
type FocusState = {
  entries: FocusEntry[]
  onKeyDown: (event: KeyboardEvent) => void
  onFocusIn: (event: FocusEvent) => void
}
const documentFocus = new WeakMap<Document, FocusState>()
const focusSelector = 'a[href],button,input:not([type="hidden"]),select,textarea,summary,[tabindex],[contenteditable="true"]'

function firstSummary(details: HTMLElement): HTMLElement | undefined {
  return Array.from(details.children).find(element => element.tagName === 'SUMMARY') as HTMLElement | undefined
}

function visible(element: HTMLElement): boolean {
  if (!element.isConnected || element.matches(':disabled,[aria-disabled="true"]')) return false
  for (let current: HTMLElement | null = element; current; current = current.parentElement) {
    if (current.hidden || current.hasAttribute('inert')) return false
    const style = current.ownerDocument.defaultView?.getComputedStyle(current)
    if (style?.display === 'none' || style?.visibility === 'hidden' || style?.visibility === 'collapse') return false
    if (current.tagName === 'DETAILS' && !current.hasAttribute('open') && !firstSummary(current)?.contains(element)) return false
  }
  return element.getClientRects().length > 0
}

function tabbable(element: HTMLElement): boolean {
  if (element.tabIndex < 0 || !visible(element)) return false
  if (element.tagName === 'SUMMARY' && !element.hasAttribute('tabindex')) {
    return element.parentElement?.tagName === 'DETAILS' && firstSummary(element.parentElement) === element
  }
  return true
}

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(focusSelector))
    .filter(tabbable)
    .map((element, order) => ({ element, order }))
    .sort((left, right) => {
      const leftIndex = left.element.tabIndex > 0 ? left.element.tabIndex : Number.MAX_SAFE_INTEGER
      const rightIndex = right.element.tabIndex > 0 ? right.element.tabIndex : Number.MAX_SAFE_INTEGER
      return leftIndex - rightIndex || left.order - right.order
    })
    .map(item => item.element)
}

function topEntry(state: FocusState): FocusEntry | undefined {
  return [...state.entries].reverse().find(entry => entry.root.isConnected && entry.current().active !== false && entry.current().scopeKey === entry.scopeKey)
}

function focus(element: HTMLElement): void {
  element.focus({ preventScroll: true })
}

function preferred(entry: FocusEntry): HTMLElement {
  const initial = entry.current().initialFocusRef?.current
  if (initial && entry.root.contains(initial) && visible(initial)) return initial
  return focusables(entry.root)[0] ?? entry.root
}

function createState(document: Document): FocusState {
  const state: FocusState = {
    entries: [],
    onKeyDown(event) {
      if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return
      const entry = topEntry(state)
      if (!entry) return
      const items = focusables(entry.root)
      if (items.length === 0) {
        event.preventDefault()
        focus(entry.root)
        return
      }
      const index = items.indexOf(document.activeElement as HTMLElement)
      if (index === -1 || (event.shiftKey ? index === 0 : index === items.length - 1)) {
        event.preventDefault()
        focus(event.shiftKey ? items[items.length - 1] : items[0])
      }
    },
    onFocusIn(event) {
      const entry = topEntry(state)
      if (!entry || !(event.target instanceof Node)) return
      if (entry.root.contains(event.target)) {
        if (event.target instanceof HTMLElement) entry.lastFocused = event.target
        return
      }
      const last = entry.lastFocused
      focus(last && entry.root.contains(last) && visible(last) ? last : preferred(entry))
    },
  }
  document.addEventListener('keydown', state.onKeyDown, true)
  document.addEventListener('focusin', state.onFocusIn, true)
  return state
}

function acquire(root: HTMLElement, current: () => ModalFocusOptions): () => void {
  const document = root.ownerDocument
  let state = documentFocus.get(document)
  if (!state) {
    state = createState(document)
    documentFocus.set(document, state)
  }
  const entry: FocusEntry = {
    token: Symbol('modal-focus'), root,
    previous: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    lastFocused: null, current, scopeKey: current().scopeKey,
  }
  // A parent's layout effect may run after its child's. Keep that child on top.
  const descendant = state.entries.findIndex(other => root.contains(other.root))
  if (descendant === -1) state.entries.push(entry)
  else state.entries.splice(descendant, 0, entry)
  if (topEntry(state) === entry) focus(preferred(entry))
  const acquired = state
  return () => {
    const index = acquired.entries.findIndex(other => other.token === entry.token)
    if (index === -1) return
    const wasTop = topEntry(acquired) === entry || index === acquired.entries.length - 1
    const active = document.activeElement
    const closingOwnedFocus = active === document.body || active === null || entry.root.contains(active)
    acquired.entries.splice(index, 1)
    if (acquired.entries.length === 0) {
      document.removeEventListener('keydown', acquired.onKeyDown, true)
      document.removeEventListener('focusin', acquired.onFocusIn, true)
      documentFocus.delete(document)
    }
    if (!wasTop) return
    const next = topEntry(acquired)
    if (next) {
      if (next.root.contains(document.activeElement)) return
      const previous = entry.previous
      const last = next.lastFocused
      focus(previous && next.root.contains(previous) && visible(previous)
        ? previous : last && next.root.contains(last) && visible(last) ? last : preferred(next))
      return
    }
    // Latest render identity fences DOM reused for another Run. If navigation
    // already gave its new page meaningful focus, cleanup must not steal it.
    if (!closingOwnedFocus || entry.current().scopeKey !== entry.scopeKey) return
    const previous = entry.previous
    const restore = previous && previous !== document.body && visible(previous)
      ? previous : entry.current().returnFocusRef?.current
    if (restore && restore.ownerDocument === document && visible(restore)) focus(restore)
  }
}

export function useModalFocusContainment(ref: RefObject<HTMLElement | null>, options: ModalFocusOptions): void {
  const latest = useRef(options)
  latest.current = options
  const active = options.active ?? true
  useLayoutEffect(() => {
    if (!active || !ref.current) return
    return acquire(ref.current, () => latest.current)
  }, [ref, active, options.scopeKey])
}
