import { useLayoutEffect, type RefObject } from 'react'

type GraphFocusReturn = {
  window: Window
  document: Document
  sourceRoot: HTMLElement
  sourceHref: string
  targetHref: string
  runId: string
  arrived: boolean
  onNavigation: () => void
  onFocusIn: (event: FocusEvent) => void
}
const pendingReturns = new WeakMap<Window, GraphFocusReturn>()

function discard(ticket: GraphFocusReturn): void {
  if (pendingReturns.get(ticket.window) !== ticket) return
  pendingReturns.delete(ticket.window)
  ticket.window.removeEventListener('popstate', ticket.onNavigation, true)
  ticket.window.removeEventListener('hashchange', ticket.onNavigation, true)
  ticket.document.removeEventListener('focusin', ticket.onFocusIn, true)
}

function meaningfulFocus(ticket: GraphFocusReturn, element: Element | null): boolean {
  return element instanceof HTMLElement && element !== ticket.document.body
    && element !== ticket.document.documentElement && element.isConnected
    && !ticket.sourceRoot.contains(element)
}

// Only an explicit graph close may arm this short-lived, same-document handoff.
// It survives the keyed route remount, not another navigation or a page reload.
export function requestGraphFocusReturn(sourceRoot: HTMLElement | null, runId: string, targetHref: string): void {
  const window = sourceRoot?.ownerDocument.defaultView
  if (!window || !sourceRoot?.isConnected || !runId) return
  const previous = pendingReturns.get(window)
  if (previous) discard(previous)
  const document = sourceRoot.ownerDocument
  const sourceHref = window.location.href
  const target = new URL(targetHref, sourceHref)
  if (target.origin !== window.location.origin || target.pathname !== window.location.pathname
    || target.search !== window.location.search || target.href === sourceHref) return
  const ticket: GraphFocusReturn = {
    window, document, sourceRoot, sourceHref, targetHref: target.href, runId, arrived: false,
    onNavigation() {
      // Every subsequent event invalidates the ticket, including A → B → A
      // and a second same-URL navigation. Never adopt the latest route.
      if (ticket.arrived || window.location.href !== ticket.targetHref) {
        discard(ticket)
        return
      }
      ticket.arrived = true
      if (meaningfulFocus(ticket, document.activeElement)) discard(ticket)
    },
    onFocusIn(event) {
      if (ticket.arrived && event.target instanceof Element && meaningfulFocus(ticket, event.target)) discard(ticket)
    },
  }
  pendingReturns.set(window, ticket)
  window.addEventListener('popstate', ticket.onNavigation, true)
  window.addEventListener('hashchange', ticket.onNavigation, true)
  document.addEventListener('focusin', ticket.onFocusIn, true)
}

export function abandonGraphFocusReturn(window: Window, runId: string): void {
  const ticket = pendingReturns.get(window)
  if (ticket?.runId === runId) discard(ticket)
}

function visibleSummary(summary: HTMLElement): boolean {
  if (summary.tagName !== 'SUMMARY' || !summary.isConnected) return false
  for (let element: HTMLElement | null = summary; element; element = element.parentElement) {
    const style = element.ownerDocument.defaultView?.getComputedStyle(element)
    if (element.hidden || element.hasAttribute('inert') || style?.display === 'none'
      || style?.visibility === 'hidden' || style?.visibility === 'collapse') return false
    if (element.tagName === 'DETAILS' && !element.hasAttribute('open')
      && !Array.from(element.children).find(child => child.tagName === 'SUMMARY')?.contains(summary)) return false
  }
  return summary.getClientRects().length > 0
}

export function useGraphFocusReturn(summaryRef: RefObject<HTMLElement | null>, runId: string): void {
  // Query readiness can mount the new report later. The native ref is the only
  // target; no timer, selector polling, route rewrite, or stored DOM adoption.
  useLayoutEffect(() => {
    const summary = summaryRef.current
    if (!summary) return
    const window = summary.ownerDocument.defaultView
    if (!window) return
    const ticket = pendingReturns.get(window)
    if (!ticket?.arrived) return
    if (ticket.runId !== runId || ticket.document !== summary.ownerDocument
      || window.location.href !== ticket.targetHref || meaningfulFocus(ticket, ticket.document.activeElement)
      || !visibleSummary(summary)) {
      discard(ticket)
      return
    }
    discard(ticket) // Consume before focusin; StrictMode/render cannot replay it.
    summary.focus({ preventScroll: true })
  })
}
