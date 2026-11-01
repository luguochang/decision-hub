// @vitest-environment jsdom
// Reading-copy tests use an existing synthetic wire through the real DTO validator.
import { webcrypto } from 'node:crypto'
import { afterAll, beforeAll, expect, it, vi } from 'vitest'
import fixture from './__fixtures__/saved-probability-definitions-available.json'
import { settingsApi } from '../api/decisionSettings'
import { readSavedPerpetualReport, type SavedPerpetualReport } from '../api/perpetualSavedReport'
import { buildPerpetualReadingSnapshot, renderPerpetualReadingHtml } from './perpetualReportExport'

Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
let report: SavedPerpetualReport
beforeAll(async () => {
  vi.spyOn(settingsApi, 'session').mockReturnValue(null)
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(fixture)))
  const value = await readSavedPerpetualReport(fixture.run_id)
  if (!value) throw new Error('Existing presentation fixture was not applicable')
  report = value
})
afterAll(() => vi.restoreAllMocks())

function section() {
  const root = document.createElement('section')
  root.innerHTML = '<h3></h3><p></p><details><summary>概率定义</summary><pre></pre></details>'
  root.querySelector('h3')!.textContent = report.artifact.headline
  root.querySelector('p')!.textContent = report.payload.counter_thesis
  root.querySelector('pre')!.textContent = JSON.stringify(report.probability_definitions, null, 2)
  return root
}
function parsedHtml(root: HTMLElement, value = report) {
  return new DOMParser().parseFromString(renderPerpetualReadingHtml(value, root), 'text/html')
}

it('saves all returned fields and exact original strings without upgrading qualification', () => {
  const snapshot = JSON.parse(buildPerpetualReadingSnapshot(report))
  expect(snapshot.export_scope).toBe('reading_only_not_full_audit_bundle')
  expect(snapshot.report).toEqual(report)
  expect(snapshot.report.source_json).toBe(fixture.source_json)
  expect(snapshot.report.artifact_json).toBe(fixture.artifact_json)
  expect(snapshot.report.probability_definitions).toEqual(fixture.probability_definitions)
  expect(snapshot.report.read_at).toBe(fixture.read_at)
  expect(snapshot.report.financial_qualified).toBe(false)
  expect(snapshot.report.execution_authorized).toBe(false)
  expect(snapshot.report.bundle_export_status).toBe('unsupported_perpetual_version')
})

it('preserves unavailable definitions as unavailable rather than inventing a target', () => {
  const binding = report.probability_definitions.binding
  const unavailable: SavedPerpetualReport = { ...report, probability_definitions: {
    schema_version: 'perpetual-probability-definitions.v1', status: 'unavailable',
    binding: { owner_id: binding.owner_id, run_id: binding.run_id, mapped_ref: binding.mapped_ref,
      provider_emission_ref: binding.provider_emission_ref, synthesis_ref: binding.synthesis_ref },
    reason_code: 'prepared_probability_definitions_unavailable',
  } }
  const snapshot = JSON.parse(buildPerpetualReadingSnapshot(unavailable))
  expect(snapshot.report.probability_definitions).toEqual(unavailable.probability_definitions)
  expect(snapshot.report.probability_definitions).not.toHaveProperty('definitions')
})

it('retains the displayed body and definitions and expands a clone without mutating the page', () => {
  const root = section(), original = root.outerHTML
  const html = parsedHtml(root)
  expect(html.body.textContent).toContain(report.artifact.headline)
  expect(html.body.textContent).toContain(report.payload.counter_thesis)
  expect(html.querySelector('details')?.open).toBe(true)
  expect(root.outerHTML).toBe(original)
  const embedded = JSON.parse(html.querySelector('[data-perpetual-reading-json]')!.textContent!)
  expect(embedded.report).toEqual(report)
  expect(html.body.textContent).toContain('不是完整审计封包')
  expect(html.body.textContent).toContain('不授予交易权限')
})

it('removes active content, resources and actions while preserving hostile text as text', () => {
  const root = section()
  root.insertAdjacentHTML('beforeend', '<nav>工作台导航<button>立即交易</button></nav>'
    + '<button onclick="fetch(1)">下载</button><a href="javascript:alert(1)" onclick="alert(1)">原来源名</a>'
    + '<a download href="/report/source">下载原件</a>'
    + '<iframe src="https://evil.invalid"></iframe><img src="https://evil.invalid/x" onerror="alert(1)">'
    + '<svg><foreignObject><p onclick="alert(1)">活动外来节点</p></foreignObject></svg>'
    + '<style>@import "https://evil.invalid/x";</style><script>alert(1)</script>'
    + '<form action="https://evil.invalid"><input value="hidden-credential"></form>')
  const literal = document.createElement('p')
  literal.textContent = '</script><img src=x onerror=alert(9)> & 原文'
  root.appendChild(literal)
  root.setAttribute('onmouseenter', 'alert(2)')
  root.setAttribute('style', 'background:url(https://evil.invalid/x)')
  const html = parsedHtml(root)
  expect(html.body.textContent).toContain(literal.textContent)
  expect(html.body.textContent).toContain('原来源名')
  expect(html.body.textContent).not.toContain('工作台导航')
  expect(html.body.textContent).not.toContain('下载原件')
  expect(html.querySelector('script,iframe,img,svg,math,nav,button,form,input,a,link,base')).toBeNull()
  expect(html.querySelector('[href],[src],[srcset],[action],[onclick],[onerror],[style]')).toBeNull()
  const csp = html.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content')
  expect(csp).toContain("default-src 'none'")
  expect(csp).toContain("connect-src 'none'")
  expect(csp).toContain("form-action 'none'")
})

it('embeds closing tags and Unicode in the same safe JSON rather than creating executable markup', () => {
  const poisoned = { ...report, artifact: { ...report.artifact,
    headline: '</pre><script>alert("原文")</script>\u2028完整内容' } }
  const html = parsedHtml(section(), poisoned)
  expect(html.querySelectorAll('script')).toHaveLength(0)
  const embedded = JSON.parse(html.querySelector('[data-perpetual-reading-json]')!.textContent!)
  expect(embedded.report.artifact.headline).toBe(poisoned.artifact.headline)
  expect(embedded.report.source_json).toBe(report.source_json)
})
