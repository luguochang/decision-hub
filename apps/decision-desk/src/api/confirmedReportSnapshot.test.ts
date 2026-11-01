import { afterEach, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { createHash, webcrypto } from 'node:crypto'
import { researchQualityApi } from './researchQuality'

const original = () => JSON.parse(readFileSync(new URL(
  '../../../../artifacts/market-risk-implementation-20261007/B2/quality-pc-timeout10/fixtures/eth-quality-raw.json', import.meta.url,
), 'utf8'))
const envelope = () => {
  const quality = original()
  const report = quality.decision.full_report_json
  return { schema_version: 'research-confirmed-report-snapshot.v1', read_scope: 'confirmed_report_snapshot',
    material_audit: 'not_checked_this_read', run_id: quality.run_id, state_version: 7,
    source_bundle_sha256: 'a'.repeat(64), published_at: quality.published_at, as_of: quality.as_of,
    report_sha256: createHash('sha256').update(report).digest('hex'), report_bytes: Buffer.byteLength(report),
    quality_json: JSON.stringify(quality) }
}
const headers = { 'X-Decision-Hub-Report-Read-Mode': 'confirmed_report_snapshot',
  'X-Decision-Hub-Report-State-Version': '7', 'X-Decision-Hub-Report-Material-Audit': 'not_checked_this_read' }
const response = (body: unknown, status = 200, values = headers) => new Response(JSON.stringify(body), { status, headers: values })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('reads the explicit snapshot and verifies the actual report bytes before publishing metadata', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const body = envelope(); const metadata = vi.fn()
  const fetch = vi.fn().mockResolvedValue(response(body)); vi.stubGlobal('fetch', fetch)
  const view = await researchQualityApi.read(body.run_id, undefined, { preferSnapshot: true, onServingRead: metadata })
  expect(view).toEqual(JSON.parse(body.quality_json))
  expect(fetch.mock.calls[0][0]).toBe(`/v1/research/runs/${body.run_id}/report/snapshot`)
  expect(metadata).toHaveBeenCalledWith({ run_id: body.run_id, read_mode: 'confirmed_report_snapshot', state_version: 7, checked_at: body.as_of })
})

it('rejects changed report bytes even when the JSON still parses and the declared byte count matches', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const body = envelope(); const quality = JSON.parse(body.quality_json)
  quality.decision.full_report_json += ' '
  body.quality_json = JSON.stringify(quality); body.report_bytes += 1
  const metadata = vi.fn(); const fetch = vi.fn().mockResolvedValue(response(body)); vi.stubGlobal('fetch', fetch)
  await expect(researchQualityApi.read(body.run_id, undefined, { preferSnapshot: true, onServingRead: metadata })).rejects.toMatchObject({ kind: 'verification' })
  expect(metadata).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledTimes(1)
})

it.each(['version', 'audit', 'identity'] as const)('rejects mismatched %s without trying the full-original endpoint', async kind => {
  vi.stubGlobal('crypto', webcrypto)
  const body = envelope(); const values = { ...headers }
  if (kind === 'version') values['X-Decision-Hub-Report-State-Version'] = '8'
  if (kind === 'audit') values['X-Decision-Hub-Report-Material-Audit'] = 'checked'
  if (kind === 'identity') body.run_id = 'other-run'
  const fetch = vi.fn().mockResolvedValue(response(body, 200, values)); vi.stubGlobal('fetch', fetch)
  await expect(researchQualityApi.read(original().run_id, undefined, { preferSnapshot: true })).rejects.toMatchObject({ kind: 'verification' })
  expect(fetch).toHaveBeenCalledTimes(1)
})

it.each(['queued', 'confirmed'] as const)('handles a %s permission result without falling back to old report reads', async state => {
  const body = envelope()
  const fetch = vi.fn().mockResolvedValueOnce(response({ detail: 'research_report_snapshot_verification_failed' }, 409))
    .mockResolvedValueOnce(response({ run_id: body.run_id, state, state_version: 7,
      confirmed_at: state === 'confirmed' ? body.published_at : null, reason_code: null }))
  vi.stubGlobal('fetch', fetch)
  const pending = researchQualityApi.read(body.run_id, undefined, { preferSnapshot: true })
  if (state === 'queued') await expect(pending).rejects.toMatchObject({ servingStatus: { state: 'queued' } })
  else await expect(pending).rejects.toMatchObject({ kind: 'verification' })
  expect(fetch.mock.calls.map(call => call[0])).toEqual([
    `/v1/research/runs/${body.run_id}/report/snapshot`, `/v1/research/runs/${body.run_id}/report/status`,
  ])
})

it('uses the original reader only after both the snapshot endpoint and current status identify a legacy report', async () => {
  const quality = original(); const metadata = vi.fn()
  const fetch = vi.fn().mockResolvedValueOnce(response({ detail: 'research_report_snapshot_not_available' }, 404))
    .mockResolvedValueOnce(response({ run_id: quality.run_id, state: 'legacy_full_original', state_version: 0, confirmed_at: null, reason_code: null }))
    .mockResolvedValueOnce(response(quality, 200, { ...headers, 'X-Decision-Hub-Report-Read-Mode': 'legacy_full_original', 'X-Decision-Hub-Report-State-Version': '0' }))
  vi.stubGlobal('fetch', fetch)
  expect(await researchQualityApi.read(quality.run_id, undefined, { preferSnapshot: true, onServingRead: metadata })).toEqual(quality)
  expect(fetch.mock.calls.map(call => call[0])).toEqual([
    `/v1/research/runs/${quality.run_id}/report/snapshot`, `/v1/research/runs/${quality.run_id}/report/status`, `/v1/research/runs/${quality.run_id}/quality`,
  ])
  expect(metadata).toHaveBeenCalledWith(expect.objectContaining({ read_mode: 'legacy_full_original' }))
})
