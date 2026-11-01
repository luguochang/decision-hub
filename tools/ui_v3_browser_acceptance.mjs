import { createRequire } from 'node:module'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

const requireFromWorker = createRequire(path.resolve('apps/source_acquisition_worker/package.json'))
const { chromium } = requireFromWorker('playwright')
const base = process.env.DECISION_HUB_UI_URL || 'http://127.0.0.1:8047/'
const out = path.resolve('data/product-local/acceptance-ui-v3')
await mkdir(out, { recursive: true })
const browser = await chromium.launch({ headless: true })
const failures = []
const observations = []
const views = [
  { route: 'home', title: '今日研究', nav: '今日研究' },
  { route: 'market', title: '市场与事件', nav: '市场与事件' },
  { route: 'research', title: '研究', nav: '研究' },
  { route: 'library', title: '资料', nav: '资料' },
  { route: 'management', title: '管理与审计', nav: '管理与审计' },
  { route: 'data-jobs', title: '数据更新记录', nav: null },
  { route: 'results', title: '结果跟踪', nav: null },
  { route: 'sources', title: '来源与运行状态', nav: null },
]

try {
  for (const viewport of [{ name: 'desktop', width: 1280, height: 800 }, { name: 'mobile', width: 375, height: 812 }]) {
    const page = await browser.newPage({ viewport, deviceScaleFactor: 1 })
    page.on('pageerror', (error) => failures.push(`${viewport.name}: ${error.message}`))
    for (const view of views) {
      const response = await page.goto(`${base}#/${view.route}`, { waitUntil: 'networkidle', timeout: 30000 })
      await page.locator('h1').first().waitFor({ timeout: 15000 })
      const h1 = (await page.locator('h1').first().innerText()).trim()
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      const apiErrors = await page.locator('.product-notice.danger, .workspace-state.danger, .error-callout').allInnerTexts()
      if (response && response.status() !== 200) failures.push(`${viewport.name} ${view.route}: HTTP ${response.status()}`)
      if (h1 !== view.title) failures.push(`${viewport.name} ${view.route}: heading ${h1}`)
      if (overflow > 1) failures.push(`${viewport.name} ${view.route}: horizontal overflow ${overflow}px`)
      await page.screenshot({ path: path.join(out, `${viewport.name}-${view.route}.png`), fullPage: !['data-jobs', 'sources'].includes(view.route) })
      observations.push({ viewport: viewport.name, route: view.route, h1, overflow, apiErrors })
    }
    for (const view of views.filter((item) => item.nav)) {
      await page.getByRole('button', { name: view.nav, exact: true }).first().click()
      await page.locator('h1').first().waitFor()
      const title = (await page.locator('h1').first().innerText()).trim()
      if (title !== view.title) failures.push(`${viewport.name} navigation ${view.nav}: ${title}`)
    }
    await page.getByRole('button', { name: '查看数据更新记录' }).click()
    if ((await page.locator('h1').first().innerText()).trim() !== '数据更新记录') failures.push(`${viewport.name}: data jobs entry`)
    await page.getByRole('button', { name: '返回管理与审计' }).click()
    if ((await page.locator('h1').first().innerText()).trim() !== '管理与审计') failures.push(`${viewport.name}: data jobs back`)
    await page.close()
  }
} finally {
  await browser.close()
}
await writeFile(path.join(out, 'result.json'), JSON.stringify({ checkedAt: new Date().toISOString(), base, observations, failures }, null, 2))
console.log(JSON.stringify({ observations, failures, screenshots: out }, null, 2))
if (failures.length) process.exitCode = 1
