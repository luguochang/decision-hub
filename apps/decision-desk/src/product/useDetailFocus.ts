import { useLayoutEffect, useRef } from 'react'

// Route-driven detail panels must be visible when selected, without stealing
// focus again when a query refreshes. Keep the opener for collapse/keyboard use.
export function useDetailFocus(identity: string | null | undefined, ready = true) {
  const panel = useRef<HTMLElement>(null)
  const opener = useRef<HTMLElement | null>(null)
  const previous = useRef<string | null | undefined>(undefined)
  useLayoutEffect(() => {
    if ((identity && !ready) || identity === previous.current) return
    if (identity && panel.current) {
      const active = document.activeElement
      if (active instanceof HTMLElement && active !== document.body && !panel.current.contains(active)) opener.current = active
      panel.current.focus({ preventScroll: true })
      panel.current.scrollIntoView?.({ block: 'start' })
    } else if (!identity && previous.current && opener.current?.isConnected) {
      opener.current.focus({ preventScroll: true })
      opener.current.scrollIntoView?.({ block: 'nearest' })
    }
    previous.current = identity
  }, [identity, ready])
  return panel
}
