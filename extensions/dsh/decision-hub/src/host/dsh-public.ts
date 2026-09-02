import type { IncomingMessage, ServerResponse } from 'node:http'

export interface SessionEventLike {
  type: string
  seq: number
  time: number
  data: Record<string, unknown>
}

export interface SessionControllerPublic {
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
    meta: { id: string; createdAt?: number }
    events: SessionEventLike[]
  }>
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
  webServer: WebServerPublic
  connection: ConnectionPublic
  sessionController: SessionControllerPublic
  workspaceRegistry: WorkspaceRegistryPublic
  on(event: 'api-session/status', listener: (sessionId: string, running: boolean) => void): () => void
  on(event: 'api-session/error', listener: (sessionId: string, message: string) => void): () => void
  on(event: 'session/event', listener: (session: { id: string }, event: SessionEventLike) => void): () => void
  logger: {
    info(message: string): void
    warn(message: string | Error): void
    error(message: string | Error): void
  }
}
