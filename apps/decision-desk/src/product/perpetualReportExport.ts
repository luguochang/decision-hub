import type { SavedPerpetualReport } from '../api/perpetualSavedReport'

const htmlNamespace = 'http://www.w3.org/1999/xhtml'
const allowed = new Set(['section', 'article', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p',
  'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'strong', 'em', 'span', 'code', 'pre', 'details', 'summary',
  'br', 'hr', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th'])
const removed = new Set(['nav', 'button', 'script', 'style', 'iframe', 'object', 'embed',
  'img', 'picture', 'source', 'video', 'audio', 'link', 'meta', 'base', 'form', 'input',
  'textarea', 'select', 'option', 'template'])
const styles = 'body{font:16px/1.65 system-ui,sans-serif;color:#183d35;background:#fff;max-width:70rem;'
  + 'margin:2rem auto;padding:0 1.25rem}section,article{margin:1.25rem 0}h1,h2,h3,h4{line-height:1.4}'
  + 'pre,code{white-space:pre-wrap;overflow-wrap:anywhere}details{border:1px solid #d4e2dc;'
  + 'padding:.7rem;margin:.7rem 0}table{border-collapse:collapse}td,th{border:1px solid #d4e2dc;padding:.4rem}'
  + '.reading-limit{padding:1rem;background:#eef5f1}p,li{overflow-wrap:anywhere}'
const policy = "default-src 'none'; script-src 'none'; connect-src 'none'; img-src 'none'; "
  + "font-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'none'; "
  + "form-action 'none'; style-src 'unsafe-inline'"

function escaped(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;')
}
function clean(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return escaped(node.textContent ?? '')
  if (!(node instanceof Element) || node.namespaceURI !== htmlNamespace) return ''
  const tag = node.localName
  if (removed.has(tag) || (tag === 'a' && node.hasAttribute('download'))) return ''
  const children = Array.from(node.childNodes, clean).join('')
  if (!allowed.has(tag)) return children
  const label = node.getAttribute('aria-label')
  const attributes = (label === null ? '' : ` aria-label="${escaped(label)}"`)
    + (tag === 'details' ? ' open' : '')
  return `<${tag}${attributes}>${children}${tag === 'br' || tag === 'hr' ? '' : `</${tag}>`}`
}

export function buildPerpetualReadingSnapshot(report: SavedPerpetualReport): string {
  return JSON.stringify({ schema_version: 'perpetual-report-reading-copy.v1',
    export_scope: 'reading_only_not_full_audit_bundle', report }, null, 2)
}

export function renderPerpetualReadingHtml(report: SavedPerpetualReport, section: HTMLElement): string {
  const content = clean(section.cloneNode(true))
  return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    + `<meta http-equiv="Content-Security-Policy" content="${escaped(policy)}">`
    + `<meta name="viewport" content="width=device-width,initial-scale=1"><title>${escaped(report.artifact.headline)}</title>`
    + `<style>${styles}</style></head><body><h1>已保存报告的阅读副本</h1>`
    + '<p class="reading-limit">本文件保留本次已核对的正文与概率定义，不是完整审计封包；'
    + '不重新核验外部原件、不确认金融资格、不授予交易权限。缺失信息保持未知。</p>'
    + `<p>原报告核对时间：${escaped(report.read_at)}</p>${content}`
    + `<pre hidden data-perpetual-reading-json>${escaped(buildPerpetualReadingSnapshot(report))}</pre>`
    + '</body></html>'
}

export function downloadPerpetualReading(content: string, runId: string, format: 'json' | 'html') {
  const type = format === 'json' ? 'application/json' : 'text/html'
  const url = URL.createObjectURL(new Blob([content], { type: `${type};charset=utf-8` }))
  const link = document.createElement('a')
  try {
    link.href = url
    link.download = `perpetual-research-${encodeURIComponent(runId)}-reading.${format}`
    document.body.appendChild(link)
    link.click()
  } finally {
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
