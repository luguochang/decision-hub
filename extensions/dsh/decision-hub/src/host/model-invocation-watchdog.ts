/** A token owns one invocation's already-issued absolute deadline.
 * This never mints a step budget. SDK events or a different invocation cannot
 * release a token; the managed middleware releases it after actual finally.
 */
export class ModelInvocationWatchdogs {
  private readonly active = new Map<symbol, { sessionId: string; timer: ReturnType<typeof setTimeout> }>()

  acquire(sessionId: string, deadlineAtMs: number, elapsed: () => void): () => void {
    if (!Number.isFinite(deadlineAtMs) || deadlineAtMs <= Date.now()) throw new Error('model_watchdog_deadline_invalid')
    const token = Symbol('model-invocation')
    const timer = setTimeout(() => {
      if (!this.active.delete(token)) return
      elapsed()
    }, deadlineAtMs - Date.now())
    this.active.set(token, { sessionId, timer })
    return () => {
      const owned = this.active.get(token)
      if (owned === undefined) return
      clearTimeout(owned.timer)
      this.active.delete(token)
    }
  }

  clearSession(sessionId: string): void {
    for (const [token, owned] of this.active) {
      if (owned.sessionId !== sessionId) continue
      clearTimeout(owned.timer)
      this.active.delete(token)
    }
  }

  clearAll(): void {
    for (const owned of this.active.values()) clearTimeout(owned.timer)
    this.active.clear()
  }
}
