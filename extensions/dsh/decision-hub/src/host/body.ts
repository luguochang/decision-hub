import type { IncomingMessage } from 'node:http'

export class BodyReadError extends Error {
  constructor(readonly code: 'host_body_too_large' | 'host_json_invalid' | 'host_content_type_invalid') {
    super(code)
  }
}

export async function readJsonBody(req: IncomingMessage, maxBytes: number): Promise<unknown> {
  const contentType = req.headers['content-type']?.split(';', 1)[0]?.trim().toLowerCase()
  if (contentType !== 'application/json') throw new BodyReadError('host_content_type_invalid')
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
    size += buffer.length
    if (size > maxBytes) throw new BodyReadError('host_body_too_large')
    chunks.push(buffer)
  }
  try {
    const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      throw new Error('JSON body must be an object')
    }
    return parsed
  } catch {
    throw new BodyReadError('host_json_invalid')
  }
}
