// Browser acceptance for the running Windows product. Reuses the Playwright
// version already owned by the source-acquisition workspace package.
import { createRequire } from 'node:module'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const requireFromAcquisition = createRequire(path.join(root, 'apps/source_acquisition_worker/package.json'))
const { chromium } = requireFromAcquisition('playwright')
const apiBase = (process.env.DECISION_HUB_API_URL || 'http://127.0.0.1:8047').replace(/\/$/, '')
const outputDir = path.join(root, 'data/product-local/acceptance-ui')
const problems = []
const checks = []
const buttonInventory = {}

function check(name, condition, detail = '') {
  checks.push({ name, passed: Boolean(condition), detail })
  if (!condition) problems.push(`${name}: ${detail}`)
}

async function jsonGet(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${new URL(url).pathname}: HTTP ${response.status}`)
  return response.json()
}

async function main() {
  const [catalogSource, operations, runs, events, documents] = await Promise.all([
    readFile(path.join(root, 'apps/decision-desk/src/DesignStatePage.tsx'), 'utf8'),
    jsonGet(`${apiBase}/v1/operations`),
    jsonGet(`${apiBase}/v1/research/runs?limit=100`),
    jsonGet(`${apiBase}/v1/macro-events?limit=10`),
    jsonGet(`${apiBase}/v1/research-library/documents`),
  ])
  const routeIds = [...catalogSource.matchAll(/\{ id: '([^']+)', title: '[^']+', group: '[^']+'/g)].map((match) => match[1])
  check('catalog_inventory', routeIds.length === 38, `${routeIds.length} routes`)
  check('dsh_runtime', operations.runtime?.runtime_id === 'dsh-web', operations.runtime?.runtime_id)
  const serviceStates = Object.fromEntries(operations.services.map((item) => [item.service_id, item.status]))
  for (const service of ['hub-api', 'hub-realtime-worker', 'hub-research-worker', 'hub-research-priority-worker', 'hub-documents']) {
    check(`service_${service}`, serviceStates[service] === 'online', String(serviceStates[service]))
  }
  const selectedRun = (process.env.DECISION_HUB_RUN_ID && runs.find((run) => run.run_id === process.env.DECISION_HUB_RUN_ID))
    || runs.find((run) => ['completed', 'degraded', 'research_only', 'rejected'].includes(run.status) && run.artifact_id)
  if (!selectedRun) throw new Error('No committed research Run available for browser acceptance')
  const runId = selectedRun.run_id
  const detail = await jsonGet(`${apiBase}/v1/research/runs/${runId}`)
  const libraryScope = await jsonGet(`${apiBase}/v1/research-library/context/${runId}`)
  check('committed_run', Boolean(detail.run.artifact_id && detail.evidence?.length && detail.trace?.length), runId)
  const eventId = Array.isArray(events) ? events[0]?.macro_event_id : undefined
  const versionId = Array.isArray(documents) ? documents[0]?.latest_version_id : undefined

  const dshLog = await readFile(path.join(root, 'data/product-local/logs/dsh-web.stdout.log'), 'utf8')
  const authLine = dshLog.split(/\r?\n/).find((line) => line.startsWith('dsh web: '))
  if (!authLine) throw new Error('DSH launcher did not publish an authenticated URL')
  const authUrl = new URL(authLine.slice('dsh web: '.length).trim())
  if (authUrl.hostname !== '127.0.0.1' || authUrl.port !== '3097' || !authUrl.searchParams.has('token')) {
    throw new Error('DSH launcher published an unexpected authentication URL')
  }

  const browser = await chromium.launch({ headless: true })
  try {
    await mkdir(outputDir, { recursive: true })
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
    const page = await context.newPage()
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(String(error)))

    const dshResponse = await page.goto(authUrl.toString(), { waitUntil: 'domcontentloaded', timeout: 30000 })
    check('dsh_authenticated_page', dshResponse?.status() === 200, `HTTP ${dshResponse?.status()}`)
    check('dsh_page_content', (await page.locator('body').innerText()).trim().length > 20, 'empty body')

    for (const route of routeIds) {
      const params = new URLSearchParams({ run: runId })
      if (detail.evidence?.[0]) params.set('evidence', detail.evidence[0].evidence_id)
      if (eventId) params.set('event', eventId)
      if (versionId) params.set('version', versionId)
      const url = `${apiBase}/#/${route}?${params}`
      const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })
      await page.locator('#root').waitFor({ state: 'visible', timeout: 12000 })
      await page.waitForFunction((expected) => window.location.hash.startsWith(`#/${expected}`), route, { timeout: 12000 })
      await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {})
      const text = (await page.locator('#root').innerText()).trim()
      buttonInventory[route] = await page.locator('#root button').evaluateAll((buttons) => buttons.map((button) => ({
        label: button.getAttribute('aria-label') || button.innerText.trim().replace(/\s+/g, ' ').slice(0, 80),
        disabled: button.disabled,
      })))
      // A hash-only navigation stays on the loaded document, so Playwright
      // correctly returns null instead of a second HTTP response.
      check(`route_${route}`, (response === null || response.status() === 200) && text.length > 80, `HTTP ${response?.status() ?? 'same-document'}, chars=${text.length}`)
    }

    await page.goto(`${apiBase}/#/home?run=${runId}`, { waitUntil: 'domcontentloaded' })
    await page.getByText('指定的研究任务').waitFor({ timeout: 15000 })
    const homeText = await page.locator('#root').innerText()
    check('home_live_link', /来源检查/.test(homeText) && /研究执行/.test(homeText), homeText.slice(0, 160))
    await page.getByRole('button', { name: '阅读结论与依据' }).waitFor({ timeout: 15000 })
    await page.screenshot({ path: path.join(outputDir, 'home-desktop.png'), fullPage: true })
    await page.getByRole('button', { name: '阅读结论与依据' }).click()
    await page.getByText('证据来源可追溯').waitFor({ timeout: 15000 })
    check('report_evidence', await page.getByText('证据来源可追溯').count() > 0)
    check('report_run_identity', page.url().includes(runId), page.url().split('?')[0])
    const reportEvidence = page.locator('.research-report-evidence')
    check('report_audit_collapsed', !await reportEvidence.evaluate((element) => element.open))
    await reportEvidence.locator('summary').click()
    check('report_audit_expands', await reportEvidence.evaluate((element) => element.open))
    await reportEvidence.locator('summary').click()
    const trustSources = page.locator('.research-trust-sources')
    await trustSources.locator('summary').click()
    check('report_sources_expand', await trustSources.evaluate((element) => element.open))
    await trustSources.locator('summary').click()
    const actionEvidence = page.locator('.research-action-gaps').filter({ hasText: '关键依据' }).locator('a[href^="#/evidence"]').first()
    const actionEvidenceHref = await actionEvidence.getAttribute('href')
    check('report_evidence_run_link', actionEvidenceHref?.includes(`run=${runId}&evidence=`))
    await actionEvidence.click()
    await page.getByRole('dialog', { name: '证据详情' }).waitFor({ timeout: 15000 })
    check('report_evidence_drawer', !(await page.getByRole('dialog', { name: '证据详情' }).innerText()).includes('证据不可用'))
    await page.getByRole('button', { name: '关闭证据详情' }).click()
    await page.getByRole('dialog', { name: '证据详情' }).waitFor({ state: 'hidden', timeout: 15000 })
    await page.screenshot({ path: path.join(outputDir, 'report-desktop.png'), fullPage: true })
    if (libraryScope?.selected_version_ids?.length) {
      const linkedVersionId = libraryScope.selected_version_ids[0]
      const link = page.locator('[aria-label="已连接研究资料"] a').first()
      await link.waitFor({ timeout: 15000 })
      check('report_library_version_link', (await link.getAttribute('href'))?.includes(`version=${linkedVersionId}`), linkedVersionId)
      await link.click()
      await page.locator('.library-facts').waitFor({ timeout: 15000 })
      check('report_library_version_open', new URL(page.url()).hash.includes(`version=${linkedVersionId}`))
      check('report_library_version_loaded', (await page.locator('.library-facts').innerText()).includes(linkedVersionId))
    }

    // Exercise representative controls against the live backend. The route
    // inventory above also records every rendered button for later audits.
    await page.getByRole('button', { name: '收起导航' }).click()
    check('sidebar_collapse', await page.getByRole('button', { name: '展开导航' }).count() === 1)
    await page.getByRole('button', { name: '展开导航' }).click()
    check('sidebar_expand', await page.getByRole('button', { name: '收起导航' }).count() === 1)
    for (const [label, expected] of [
      ['今日研究', 'home'], ['市场与事件', 'market'], ['研究', 'research'],
      ['资料', 'library'], ['管理与审计', 'management'],
    ]) {
      await page.getByRole('button', { name: label, exact: true }).first().click()
      check(`navigation_${expected}`, new URL(page.url()).hash.startsWith(`#/${expected}`), page.url().split('#')[1])
    }

    await page.getByRole('button', { name: '今日研究', exact: true }).first().click()
    await page.getByRole('button', { name: '发起一次 BTC 快照研究' }).click()
    check('home_compose_action', new URL(page.url()).hash.startsWith('#/compose'))
    await page.getByRole('button', { name: '美元指数和 ETF 资金流' }).click()
    check('compose_focus_chip', await page.getByRole('textbox', { name: '研究关注点' }).inputValue() === '美元指数和 ETF 资金流')

    await page.goto(`${apiBase}/#/calendar-all`, { waitUntil: 'domcontentloaded' })
    await page.locator('.design-calendar-panel').waitFor()
    for (const [label, expected] of [['即将发生', 'calendar-upcoming'], ['历史发布', 'calendar-history'], ['全部', 'calendar-all']]) {
      await page.locator('.design-calendar-panel').getByRole('button', { name: label, exact: true }).click()
      check(`calendar_tab_${expected}`, new URL(page.url()).hash.startsWith(`#/${expected}`))
    }
    if (eventId) {
      await page.locator('.design-event-row').first().click()
      await page.getByRole('dialog', { name: '宏观事件详情' }).waitFor({ timeout: 15000 })
      check('calendar_event_open', await page.getByRole('dialog', { name: '宏观事件详情' }).count() === 1)
      await page.getByRole('button', { name: '关闭事件详情' }).click()
      await page.getByRole('dialog', { name: '宏观事件详情' }).waitFor({ state: 'hidden', timeout: 15000 })
      check('calendar_event_close', await page.getByRole('dialog', { name: '宏观事件详情' }).count() === 0)
    }

    await page.goto(`${apiBase}/#/runs`, { waitUntil: 'domcontentloaded' })
    await page.locator('.design-table-link').first().waitFor({ timeout: 15000 })
    await page.getByRole('textbox', { name: '搜索研究任务' }).fill(runId)
    await page.locator('.design-table-link').first().waitFor({ timeout: 15000 })
    check('runs_search', await page.locator('.design-table-link').count() >= 1)
    await page.getByRole('button', { name: '重置' }).click()
    check('runs_reset', await page.getByRole('textbox', { name: '搜索研究任务' }).inputValue() === '')

    await page.goto(`${apiBase}/#/dsh?run=${runId}`, { waitUntil: 'domcontentloaded' })
    await page.locator('.dsh-context-panel').waitFor()
    for (const [label, expected] of [['对话摘要', 'dsh-conversation'], ['主动研究', 'dsh-research'], ['原生报告', 'dsh-report'], ['技术轨迹', 'dsh']]) {
      await page.locator('.dsh-context-panel').getByRole('button', { name: label, exact: true }).click()
      check(`dsh_tab_${expected}`, new URL(page.url()).hash.startsWith(`#/${expected}`))
    }

    await page.setViewportSize({ width: 375, height: 812 })
    for (const route of ['home', 'market', 'research', 'library', 'management', 'data-jobs', 'runs', 'sources', 'report']) {
      await page.goto(`${apiBase}/#/${route}?run=${runId}`, { waitUntil: 'domcontentloaded' })
      if (route === 'home') await page.getByRole('button', { name: '阅读结论与依据' }).waitFor({ timeout: 15000 })
      const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
      check(`mobile_width_${route}`, widths.scroll <= widths.client + 2, `${widths.scroll}/${widths.client}`)
      if (route === 'home') await page.screenshot({ path: path.join(outputDir, 'home-mobile.png'), fullPage: true })
    }
    check('browser_runtime_errors', pageErrors.length === 0, pageErrors.slice(0, 5).join(' | '))
    await writeFile(path.join(outputDir, 'latest.json'), JSON.stringify({ checked_at: new Date().toISOString(), api_base: apiBase, run_id: runId, checks, button_inventory: buttonInventory, problems }, null, 2))
    if (problems.length) await page.screenshot({ path: path.join(outputDir, 'failure.png'), fullPage: true })
  } finally {
    await browser.close()
  }
  console.log(JSON.stringify({ status: problems.length ? 'failed' : 'passed', run_id: runId, checks: checks.length, route_count: routeIds.length, problems, report: path.join(outputDir, 'latest.json') }))
  if (problems.length) process.exitCode = 1
}

main().catch((error) => {
  console.error(`product UI smoke failed: ${error.message}`)
  process.exitCode = 1
})
