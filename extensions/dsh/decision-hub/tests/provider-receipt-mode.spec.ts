// Synthetic authenticated-Link fixtures only; no runtime/financial qualification.
import { expect, it, vi } from 'vitest'
import { HubClientError, type HubClient } from '../src/host/hub-client.js'
import { readProviderReceiptMode, registerProviderInputReader } from '../src/host/provider-input.js'
import type { CancellationBinding } from '../src/host/command-cancellation.js'

const binding: CancellationBinding = { runId: 'run-mode', sessionId: `dsh_${'a'.repeat(64)}`,
  requestHash: 'b'.repeat(64), requestId: 'request-mode', generation: 2 }
const link = { run_id: binding.runId, dsh_session_id: binding.sessionId, request_hash: binding.requestHash, generation: binding.generation }

it.each([true, false])('uses authenticated server Harness selection: %s', async harness => {
  const root = {}
  const reader = { linkBySession: vi.fn<HubClient['linkBySession']>().mockResolvedValue({ ...link, ...(harness ? { harness_required: true } : {}) } as Awaited<ReturnType<HubClient['linkBySession']>>), providerToolEmission: vi.fn<HubClient['providerToolEmission']>() }
  const dispose = registerProviderInputReader(root, reader)
  const signal = new AbortController().signal
  await expect(readProviderReceiptMode(root, binding, signal)).resolves.toBe(harness ? 'independent-review-receipt.v1' : 'independent-review-receipt.v2')
  expect(reader.linkBySession).toHaveBeenCalledWith(binding.sessionId, signal)
  expect(reader.providerToolEmission).not.toHaveBeenCalled()
  dispose()
})

it.each(['run_id', 'generation', 'dsh_session_id', 'request_hash'] as const)('rejects current binding drift in %s before selecting a protocol', async field => {
  const root = {}
  const reader = { linkBySession: vi.fn<HubClient['linkBySession']>().mockResolvedValue({ ...link, [field]: field === 'generation' ? 3 : 'wrong' } as Awaited<ReturnType<HubClient['linkBySession']>>), providerToolEmission: vi.fn<HubClient['providerToolEmission']>() }
  const dispose = registerProviderInputReader(root, reader)
  await expect(readProviderReceiptMode(root, binding, new AbortController().signal)).rejects.toThrow('dsh_provider_emission_identity_mismatch')
  dispose()
})

it('missing reader, callback refusal and cancellation never imply ordinary V2', async () => {
  const root = {}; const signal = new AbortController().signal
  await expect(readProviderReceiptMode(root, binding, signal)).rejects.toThrow('dsh_provider_reader_unavailable')
  const reader = { linkBySession: vi.fn<HubClient['linkBySession']>().mockRejectedValue(new HubClientError('host_hub_http_409', false)), providerToolEmission: vi.fn<HubClient['providerToolEmission']>() }
  const dispose = registerProviderInputReader(root, reader)
  await expect(readProviderReceiptMode(root, binding, signal)).rejects.toThrow('host_hub_http_409')
  const controller = new AbortController(); controller.abort()
  await expect(readProviderReceiptMode(root, binding, controller.signal)).rejects.toThrow()
  expect(reader.linkBySession).toHaveBeenCalledOnce()
  dispose()
})
