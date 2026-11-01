import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-tools'
import type { CancellationServices } from './command-cancellation.js'

export interface SessionEventLike {
  type: string
  seq: number
  time: number
  data: Record<string, unknown>
}

/** Native SDK event payload interfaces need no string index signature.
 * JSON inspections use SessionEventLike after object validation. Preserve the
 * exact live Agent/Session object identity across this public read-only seam.
 */
export interface NativeSessionEventPublic {
  readonly type: string
  readonly seq: number
  readonly time: number
  readonly data: object
}

export function isSessionEventLike(value: unknown): value is SessionEventLike {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  if (!('type' in value) || typeof value.type !== 'string'
    || !('seq' in value) || !Number.isSafeInteger(value.seq)
    || !('time' in value) || !Number.isSafeInteger(value.time)
    || !('data' in value) || typeof value.data !== 'object'
    || value.data === null || Array.isArray(value.data)) return false
  return true
}

export interface SessionControllerPublic {
  /** Optional so older SDK/Fake clients fail closed for activity readback. */
  list?(request: Record<string, never>, signal: AbortSignal): Promise<{ items: SessionSummaryPublic[] }>
  control?(signal: AbortSignal): AsyncIterable<SessionControlFramePublic>
  create(request: {
    sessionId: string
    workspaceId?: string
    cwd?: string
    agentPreset?: string
  }): Promise<{ sessionId: string; agentPreset?: string }>
  prompt(request: {
    requestId: string
    sessionId: string
    mode: 'queue'
    content: readonly [{ type: 'text'; text: string }]
  }, signal: AbortSignal): Promise<{ accepted: true }>
  cancel(request: { sessionId: string }): { accepted: true } | Promise<{ accepted: true }>
  inspect(sessionId: string, signal?: AbortSignal): Promise<{
    meta: { id: string; createdAt?: number; parentSession?: string }
    events: SessionEventLike[]
  }>
}

export interface SessionSummaryPublic {
  sessionId: string
  running: boolean
  parentSessionId?: string
  cwd?: string
}

export interface SessionControlFramePublic {
  type: string
  value?: {
    queues: Readonly<Record<string, readonly unknown[]>>
    jobs: Readonly<Record<string, readonly { status: string; id?: string }[]>>
    projections?: Readonly<Record<string, { asOfSeq: number }>>
  }
}

export interface WorkspaceRegistryPublic {
  resolveByPath(path: string): Promise<{
    id: string
    readonly sessionIds: readonly string[]
  } | undefined>
}

export interface WebServerPublic {
  register(route: {
    kind: 'exact' | 'prefix'
    path: string
    handler: (req: IncomingMessage, res: ServerResponse) => void | Promise<void>
  }): () => void
}

export interface ConnectionPublic {
  fetch: {
    register(route: {
      path: string
      methods: readonly ('GET' | 'HEAD')[]
      fetch: (request: Request) => Promise<Response>
    }): () => Promise<void>
  }
}

export interface HostContextPublic {
  /** Optional official services, acquired through Cordis get at the runtime boundary. */
  get?<K extends keyof (CancellationServices & { tokenMeter: NativeTokenMeterPublic })>(name: K):
    (CancellationServices & { tokenMeter: NativeTokenMeterPublic })[K] | undefined
  agents?: { get(id: string): ModelAgentLineage | undefined }
  webServer: WebServerPublic
  connection: ConnectionPublic
  sessionController: SessionControllerPublic
  workspaceRegistry: WorkspaceRegistryPublic
  on(event: 'api-session/status', listener: (sessionId: string, running: boolean) => void): () => void
  on(event: 'api-session/error', listener: (sessionId: string, message: string) => void): () => void
  on(event: 'agent/pre-step', listener: (payload: { agent: ModelAgentLineage; messages: import('@deepseek-ai/dsh-session').UserMessage[];
    turn: number; step: number; signal: AbortSignal }, next: () => Promise<PreStepDecisionPublic>) => Promise<PreStepDecisionPublic>): () => void
  on(event: 'agent/request', listener: (payload: { agent: ModelAgentLineage; turn: number; step: number; signal: AbortSignal },
    next: () => Promise<import('@deepseek-ai/dsh-llm').LlmCallConfig>) => Promise<import('@deepseek-ai/dsh-llm').LlmCallConfig>): () => void
  on(event: 'agent/created' | 'agent/disposed', listener: (payload: { readonly agent: ModelAgentLineage }) => void): () => void
  on(event: 'subagent/start', listener: (info: { readonly id: string; readonly local: boolean }) => void): () => void
  on(event: 'session/event', listener: (session: NativeSessionPublic, event: SessionEventLike) => void): () => void
  on(event: 'llm/stream', listener: (options: ModelDispatchOptions, next: () => AsyncIterable<unknown>) => AsyncIterable<unknown>): () => void
  logger: {
    info(message: string): void
    warn(message: string | Error): void
    error(message: string | Error): void
  }
}

export interface ModelAgentLineage {
  /** Actual SDK scoped service; missing legacy clients cannot expose Skill. */
  readonly ctx?: Pick<Agent['ctx'], 'tools'>
  readonly id: string
  readonly status?: 'idle' | 'running'
  readonly session?: NativeSessionPublic
}

export type PreStepDecisionPublic = { kind: 'reject' } | { kind: 'enter';
  messages: import('@deepseek-ai/dsh-session').UserMessage[]; startsRequestSeries?: true }

/** Optional fields keep old clients uncertain; only full official objects qualify. */
export interface NativeSessionPublic {
  readonly id?: string
  readonly header?: { readonly parentSession?: string; readonly seedLength?: number }
  readonly firstLiveSeq?: number
  readonly seq?: number
  readonly events?: readonly NativeSessionEventPublic[]
  deriveMessages?(): import('@deepseek-ai/dsh-llm').Message[]
  append?: {
    (type: 'compaction/prune', data: { shadowedRange: { start: number; end: number };
      shadowedSeqs: number[]; shadowedTokenCount: number }): NativeSessionEventPublic
    (type: 'user/message', data: import('@deepseek-ai/dsh-session').UserMessage,
      options: { surfaceOp: { op: 'replace'; start: number; end: number }; sourceEventSeqs: number[] }): NativeSessionEventPublic
  }
}

/** Existing official SDK service; no independent token estimator. */
export interface NativeTokenMeterPublic {
  estimateMessage(message: import('@deepseek-ai/dsh-llm').Message): number
}

/** Public DSH GenerateOptions fields used for conversation controls. */
export interface ModelDispatchOptions {
  /** Public GenerateOptions model-facing bytes, read only for opted-in Harness. */
  messages?: import('@deepseek-ai/dsh-llm').GenerateOptions['messages']
  system?: import('@deepseek-ai/dsh-llm').GenerateOptions['system']
  tools?: import('@deepseek-ai/dsh-llm').GenerateOptions['tools']
  sessionId?: string
  purpose?: 'compaction' | 'session-title'
  provider: string
  model: string
  reasoningEffort?: string
  temperature?: number
  maxTokens?: number
  stop?: string[]
  signal?: AbortSignal
}
