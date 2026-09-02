import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { DecisionHubHostBridge, resolveHostConfig, type HostPluginConfig } from './host/bridge.js'

export { DecisionHubHostBridge, resolveHostConfig }
export type { HostPluginConfig }

export const name = 'decision-hub'
export const inject = ['webServer', 'sessionController', 'workspaceRegistry', 'connection']

export const Config: z<HostPluginConfig> = z.object({
  hubBaseUrl: z.string().default('http://127.0.0.1:8000'),
  decisionDeskBaseUrl: z.string().default('http://127.0.0.1:8000'),
  inboundKey: z.string(),
  callbackKey: z.string(),
  defaultWorkspaceCwd: z.string(),
  allowedPermissionRefs: z.array(z.string()).default(['decision-hub://permissions/research-only']),
  clientPlugin: z.boolean().default(false),
  requireClientPlugin: z.boolean().default(false),
  maxBodyBytes: z.natural().min(1).default(1_200_000),
  operationTimeoutMs: z.natural().min(1).default(30_000),
  callbackTimeoutMs: z.natural().min(1).default(5_000),
  callbackAttempts: z.natural().min(1).max(5).default(3),
  ownerId: z.string().default('dsh-web-owner'),
  runtimeMode: z.union(['live', 'replay']).default('live'),
  sourceCommit: z.string(),
  sourceVersion: z.string(),
  pluginBuildHash: z.string(),
})

export function apply(ctx: Context, config: HostPluginConfig): void {
  const bridge = new DecisionHubHostBridge(ctx as never, resolveHostConfig(config))
  ctx.effect(() => bridge.start(), 'decision-hub.host-bridge')
}
