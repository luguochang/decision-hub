import { request as httpRequest } from 'node:http'
import { request as httpsRequest } from 'node:https'

// Never retain a URL, response, arbitrary Error.message, or cancellation reason.
const SAFE_CAUSES = new Set(['ECONNRESET', 'ECONNREFUSED', 'ENOTFOUND', 'EAI_AGAIN',
  'ETIMEDOUT', 'EPIPE', 'ERR_STREAM_PREMATURE_CLOSE', 'ERR_HTTP_CONTENT_LENGTH_MISMATCH',
  'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'DEPTH_ZERO_SELF_SIGNED_CERT',
  'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT',
  'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_SOCKET'])
export function baselineSafeCause(error: unknown): Readonly<{ code: string }> | undefined {
  if (!error || typeof error !== 'object') return undefined
  const item = error as { code?: unknown; cause?: { code?: unknown } }
  const code = typeof item.code === 'string' && SAFE_CAUSES.has(item.code) ? item.code
    : typeof item.cause?.code === 'string' && SAFE_CAUSES.has(item.cause.code) ? item.cause.code : undefined
  return code ? Object.freeze({ code }) : undefined
}
export type BaselineTransportCode = 'research_baseline_cancelled' | 'research_baseline_transport_timeout'
  | 'research_baseline_transport_unavailable' | 'research_baseline_http_refused' | 'research_baseline_oversize'
export type BaselineTransportPhase = 'request' | 'response_headers' | 'response_body'
export type BaselineTransportDiagnostic = Readonly<{ phase: BaselineTransportPhase; code: BaselineTransportCode; cause_code?: string }>
export class BaselineTransportError extends Error {
  constructor(readonly code: BaselineTransportCode, error?: unknown, readonly phase: BaselineTransportPhase = 'request') {
    super(code, { cause: baselineSafeCause(error) })
  }
}

/** One owned POST, no retries/redirects/proxy/global dispatcher and no hidden headers timeout.
 * Resolve/reject only after the actual request AND received body streams close.
 * The same absolute deadline covers DNS, connect, headers and the complete body.
 */
export function baselinePost(options: {
  url: URL; authKey: string; body: string; signal: AbortSignal; deadlineAtMs: number;
  method?: 'POST' | 'PUT' | 'GET'; receiptDeadlineAt?: string;
  proofKind?: 'model-binding' | 'research-proof'; harnessWitnessHash?: string;
  allowErrorResponse?: boolean; onStatus?: (status: number) => void;
}): Promise<string> {
  if (options.harnessWitnessHash !== undefined && (options.proofKind !== 'model-binding'
    || !/^[a-f0-9]{64}$/.test(options.harnessWitnessHash))) {
    return Promise.reject(new BaselineTransportError('research_baseline_http_refused'))
  }
  const remaining = options.deadlineAtMs - Date.now()
  if (!Number.isFinite(remaining) || remaining <= 0) {
    return Promise.reject(new BaselineTransportError('research_baseline_transport_timeout'))
  }
  if (options.signal.aborted) return Promise.reject(new BaselineTransportError('research_baseline_cancelled'))
  return new Promise((resolve, reject) => {
    let req: import('node:http').ClientRequest | undefined
    let res: import('node:http').IncomingMessage | undefined
    let requestClosed = false, bodyClosed = true, done = false
    let outcome: { body: string } | { error: BaselineTransportError } | undefined
    const chunks: Buffer[] = []
    let bytes = 0
    let phase: BaselineTransportPhase = 'request'
    const settle = () => {
      if (!outcome || !requestClosed || !bodyClosed || done) return
      done = true
      clearTimeout(timer)
      options.signal.removeEventListener('abort', cancel)
      if ('error' in outcome) reject(outcome.error)
      else resolve(outcome.body)
    }
    const finish = (value: NonNullable<typeof outcome>) => {
      if (outcome) return
      outcome = value
      clearTimeout(timer)
      options.signal.removeEventListener('abort', cancel)
      // destroy() is idempotent. Do not reject a race while real IO keeps running.
      res?.destroy()
      req?.destroy()
      settle()
    }
    const fail = (code: BaselineTransportCode, error?: unknown) => finish({ error: new BaselineTransportError(code, error, phase) })
    const cancel = () => fail(Date.now() >= options.deadlineAtMs
      ? 'research_baseline_transport_timeout' : 'research_baseline_cancelled')
    const timer = setTimeout(() => fail('research_baseline_transport_timeout'), remaining)
    options.signal.addEventListener('abort', cancel, { once: true })
    try {
      const request = options.url.protocol === 'https:' ? httpsRequest : httpRequest
      req = request(options.url, { method: options.method ?? 'POST', agent: false,
        headers: { 'content-type': 'application/json', 'x-decision-hub-bridge-key': options.authKey,
          ...(options.harnessWitnessHash === undefined ? {} : { 'x-decision-hub-harness-witness': options.harnessWitnessHash }),
          ...(options.receiptDeadlineAt ? {
            [options.proofKind === 'model-binding' ? 'x-decision-hub-model-binding-deadline-at'
              : options.proofKind === 'research-proof' ? 'x-decision-hub-research-proof-deadline-at'
                : 'x-decision-hub-provider-input-deadline-at']: options.receiptDeadlineAt,
            [options.proofKind === 'model-binding' ? 'x-decision-hub-model-binding-budget-ms'
              : options.proofKind === 'research-proof' ? 'x-decision-hub-research-proof-budget-ms'
                : 'x-decision-hub-provider-input-budget-ms']: String(Math.max(1, Math.floor(options.deadlineAtMs - Date.now()))),
          } : {}),
          'content-length': Buffer.byteLength(options.body, 'utf8') } }, response => {
        res = response
        phase = 'response_headers'
        bodyClosed = false
        res.once('close', () => {
          bodyClosed = true
          if (!outcome) fail('research_baseline_transport_unavailable', { code: 'ERR_STREAM_PREMATURE_CLOSE' })
          settle()
        })
        res.once('error', error => fail('research_baseline_transport_unavailable', error))
        // Never follow a redirect, even to the same host; never leak the bridge key.
        const status = res.statusCode ?? 0
        // Typed callback errors may be consumed, but a redirect is always refused.
        if (status < 200 || (status >= 300 && status < 400) || (!options.allowErrorResponse && status >= 300)) {
          fail('research_baseline_http_refused'); return
        }
        options.onStatus?.(status)
        res.on('data', (chunk: Buffer) => {
          if (outcome) return
          phase = 'response_body'
          bytes += chunk.length
          if (bytes > 1_000_000) { fail('research_baseline_oversize'); return }
          chunks.push(chunk)
        })
        res.once('end', () => {
          if (outcome) return
          if (options.signal.aborted) { cancel(); return }
          if (Date.now() >= options.deadlineAtMs) { fail('research_baseline_transport_timeout'); return }
          finish({ body: Buffer.concat(chunks, bytes).toString('utf8') })
        })
        // An abort/error may have won just before headers arrived.
        if (outcome) res.destroy()
      })
      req.once('close', () => { requestClosed = true; settle() })
      req.once('error', error => fail('research_baseline_transport_unavailable', error))
      // No event-loop yield occurred since the initial check; account for a
      // synchronous custom signal firing during request creation as well.
      if (outcome || options.signal.aborted) { cancel(); req.destroy(); return }
      req.end(options.body)
    } catch (error) {
      if (!req) requestClosed = true
      fail('research_baseline_transport_unavailable', error)
    }
  })
}
