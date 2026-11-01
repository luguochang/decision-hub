/** Exact JSON identities shared by settings and simulated-account commands. */
export function canonicalDecisionBody(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalDecisionBody).join(',')}]`
  if (value !== null && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([key, item]) => `${JSON.stringify(key)}:${canonicalDecisionBody(item)}`).join(',')}}`
  return JSON.stringify(value)
}

export async function decisionBodyHash(value: unknown): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalDecisionBody(value)))
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('')
}
