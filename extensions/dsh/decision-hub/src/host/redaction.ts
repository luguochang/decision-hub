const SENSITIVE = /authorization|api[-_]?key|token|secret|password|prompt|content|body/i

export function redactRecord(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactRecord)
  if (typeof value !== 'object' || value === null) return value
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [
    key,
    SENSITIVE.test(key) ? '[redacted]' : redactRecord(item),
  ]))
}

export function safeErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  return message
    .replace(/(authorization|api[-_]?key|token|secret|password)\s*[:=]\s*\S+/gi, '$1=[redacted]')
    .slice(0, 1000)
}
