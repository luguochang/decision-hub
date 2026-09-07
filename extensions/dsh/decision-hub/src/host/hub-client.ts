import type {
  DshBusinessStatus,
  DshResearchIntake,
  DshRunSessionLinkView,
  DshSessionAccepted,
  DshSessionCompletion,
  DshSessionPrompt,
  DshSessionStatus,
  ResearchRunCommand,
  ResearchRunCommandResult,
  ResearchRunDetailView,
  ResearchRunQueued,
  ResearchInboxView,
  ResearchObservabilityView,
  ResearchValueEvaluation,
} from '@decision-hub/contracts-ts'
import {
  dshBusinessStatusSchema,
  dshRunSessionLinkViewSchema,
  dshSessionPromptSchema,
  researchRunCommandResultSchema,
  researchRunDetailViewSchema,
  researchRunQueuedSchema,
  researchInboxViewSchema,
  researchObservabilityViewSchema,
  researchValueEvaluationSchema,
} from '@decision-hub/contracts-ts'
import { safeErrorMessage } from './redaction.js'

export class HubClientError extends Error {
  constructor(readonly code: string, readonly retryable: boolean, cause?: unknown) {
    super(code, { cause })
  }
}

export interface HubClientOptions {
  baseUrl: string
  callbackKey: string
  timeoutMs: number
  attempts: number
  ownerId: string
  fetchImpl?: typeof fetch
}

export class HubClient {
  private readonly fetchImpl: typeof fetch
  constructor(private readonly options: HubClientOptions) {
    this.fetchImpl = options.fetchImpl ?? fetch
  }

  async readiness(): Promise<boolean> {
    try {
      const response = await this.request('/health/ready', { method: 'GET' }, 1)
      return response.ok
    } catch {
      return false
    }
  }

  async prompt(runId: string, generation: number): Promise<DshSessionPrompt> {
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}/prompt?generation=${generation}`, { method: 'GET' })
    return dshSessionPromptSchema.parse(await this.json(response))
  }

  async link(runId: string): Promise<DshRunSessionLinkView> {
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}`, { method: 'GET' })
    return dshRunSessionLinkViewSchema.parse(await this.json(response))
  }

  async linkBySession(sessionId: string): Promise<DshRunSessionLinkView> {
    const response = await this.request(`/v1/dsh/sessions/by-session/${encodeURIComponent(sessionId)}`, { method: 'GET' })
    return dshRunSessionLinkViewSchema.parse(await this.json(response))
  }

  async businessStatus(runId: string): Promise<DshBusinessStatus> {
    const response = await this.request(`/v1/dsh/sessions/${encodeURIComponent(runId)}/business-status`, { method: 'GET' })
    return dshBusinessStatusSchema.parse(await this.json(response))
  }

  async researchDetail(runId: string): Promise<ResearchRunDetailView> {
    const response = await this.request(`/v1/research/runs/${encodeURIComponent(runId)}`, { method: 'GET' })
    return researchRunDetailViewSchema.parse(await this.json(response))
  }

  async researchInbox(limit = 100): Promise<ResearchInboxView> {
    const bounded = Math.max(1, Math.min(500, Math.trunc(limit)))
    const response = await this.request(`/v1/research/inbox?limit=${bounded}`, { method: 'GET' })
    return researchInboxViewSchema.parse(await this.json(response))
  }

  async researchObservability(runId: string): Promise<ResearchObservabilityView> {
    const response = await this.request(
      `/v1/research/runs/${encodeURIComponent(runId)}/observability`,
      { method: 'GET' },
    )
    return researchObservabilityViewSchema.parse(await this.json(response))
  }

  async researchValueEvaluation(runId: string): Promise<ResearchValueEvaluation> {
    const response = await this.request(
      `/v1/research/runs/${encodeURIComponent(runId)}/value-evaluation`,
      { method: 'GET' },
    )
    return researchValueEvaluationSchema.parse(await this.json(response))
  }

  async accepted(payload: DshSessionAccepted): Promise<void> {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/accepted`, {
      method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
    }))
  }

  async status(payload: DshSessionStatus): Promise<void> {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/status`, {
      method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
    }))
  }

  async terminal(payload: DshSessionCompletion): Promise<void> {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(payload.run_id)}/terminal`, {
      method: 'PUT', body: JSON.stringify(payload), headers: { 'content-type': 'application/json' },
    }))
  }

  async researchIntake(payload: DshResearchIntake, idempotencyKey: string): Promise<ResearchRunQueued> {
    const response = await this.request('/v1/research/observations', {
      method: 'POST',
      body: JSON.stringify({
        text: payload.text,
        source_id: payload.source_id,
        source_type: 'manual',
        language: payload.language,
      }),
      headers: {
        'content-type': 'application/json',
        'idempotency-key': idempotencyKey,
      },
    })
    return researchRunQueuedSchema.parse(await this.json(response))
  }

  async researchCommand(runId: string, payload: ResearchRunCommand): Promise<ResearchRunCommandResult> {
    const response = await this.request(`/v1/research/runs/${encodeURIComponent(runId)}/commands`, {
      method: 'POST',
      body: JSON.stringify(payload),
      headers: {
        'content-type': 'application/json',
        'idempotency-key': payload.request_id,
        'x-owner-id': this.options.ownerId,
      },
    })
    return researchRunCommandResultSchema.parse(await this.json(response))
  }

  private async request(path: string, init: RequestInit, attempts = this.options.attempts): Promise<Response> {
    let last: unknown
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), this.options.timeoutMs)
      try {
        const response = await this.fetchImpl(new URL(path, this.options.baseUrl), {
          ...init,
          signal: controller.signal,
          headers: {
            'x-decision-hub-bridge-key': this.options.callbackKey,
            ...init.headers,
          },
        })
        if (response.ok) return response
        if (response.status < 500 || attempt === attempts) {
          throw new HubClientError(`host_hub_http_${response.status}`, response.status >= 500)
        }
        last = new Error(`Hub returned ${response.status}`)
      } catch (error) {
        if (error instanceof HubClientError && !error.retryable) throw error
        last = error
        if (attempt === attempts) break
      } finally {
        clearTimeout(timer)
      }
    }
    throw new HubClientError('host_callback_unreachable', true, safeErrorMessage(last))
  }

  private async json(response: Response): Promise<unknown> {
    try { return await response.json() } catch (error) {
      throw new HubClientError('host_hub_response_invalid', false, error)
    }
  }
}
