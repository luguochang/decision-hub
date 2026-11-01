import type { ResearchRunCommand, ResearchRunCommandReceipt, ResearchRunCommandResult } from '../api/types'

export const commandTime = '2026-10-01T04:00:00Z'
export function commandResult(runId: string, command: ResearchRunCommand, status: 'accepted' | 'rejected' = 'accepted'): ResearchRunCommandResult {
  return { schema_version: 'research-run-command-result.v1', request_id: command.request_id, source_run_id: runId, command: command.command,
    target_run_id: status === 'rejected' || ['cancel', 'feedback'].includes(command.command) ? null : `child-${runId}`, status, created_at: commandTime }
}
export function commandReceipt(runId: string, command: ResearchRunCommand, owner = 'owner', status: 'accepted' | 'rejected' = 'accepted'): ResearchRunCommandReceipt {
  return { schema_version: 'research-run-command-receipt.v1', request_id: command.request_id, owner_id: owner, source_run_id: runId, command: { ...command },
    result: commandResult(runId, command, status) }
}
