import { chromium } from "playwright";
import { createReadStream, mkdirSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const apiOrigin = process.env.DECISION_DESK_API_URL ?? "http://127.0.0.1:8047";
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const output = resolve(repoRoot, process.env.UI_HANDOFF_SCREENSHOT_DIR ?? "docs/hand-off/ui-screenshots");
const reportRun = process.env.UI_HANDOFF_REPORT_RUN ?? "run_6c34b06c2d864d699085047ad353432b";
mkdirSync(output, { recursive: true });

const dist = resolve(repoRoot, "apps/decision-desk/dist");
const mime = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
]);
const preview = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1");
  if (url.pathname.startsWith("/v1/")) {
    const upstream = await fetch(`${apiOrigin}${url.pathname}${url.search}`, {
      method: request.method,
      headers: { Accept: request.headers.accept ?? "application/json" },
    });
    response.writeHead(upstream.status, { "content-type": upstream.headers.get("content-type") ?? "application/json" });
    response.end(Buffer.from(await upstream.arrayBuffer()));
    return;
  }
  let file = url.pathname === "/" ? join(dist, "index.html") : resolve(dist, `.${url.pathname}`);
  if (!file.startsWith(dist) || !statSync(file, { throwIfNoEntry: false })?.isFile()) file = join(dist, "index.html");
  const extension = file.slice(file.lastIndexOf("."));
  response.writeHead(200, { "content-type": mime.get(extension) ?? "application/octet-stream" });
  createReadStream(file).pipe(response);
});
await new Promise((resolveReady) => preview.listen(0, "127.0.0.1", resolveReady));
const address = preview.address();
const base = `http://127.0.0.1:${address.port}/`;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
page.on("pageerror", (error) => console.error("ui pageerror", error.message));
page.on("requestfailed", (request) => console.error("ui requestfailed", request.url(), request.failure()?.errorText));

async function settle(ms = 3500) {
  await page.waitForLoadState("domcontentloaded").catch(() => {});
  await page.waitForTimeout(ms);
}

async function capture(name, url = base) {
  await page.goto(url, { waitUntil: "commit", timeout: 15000 }).catch(() => {});
  await settle();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: join(output, name), fullPage: false });
}

async function clickAndCapture(selector, name) {
  const target = page.getByRole("button", { name: selector });
  if (await target.count()) {
    await target.first().waitFor({ state: "visible", timeout: 15000 }).catch(() => {});
    await target.first().click({ timeout: 15000 });
  } else {
    const text = page.getByText(selector, { exact: false }).first();
    await text.waitFor({ state: "visible", timeout: 15000 });
    await text.click({ timeout: 15000 });
  }
  await page.waitForTimeout(500);
  await page.screenshot({ path: join(output, name), fullPage: false });
}

await capture("01-research-command-center.png");
await clickAndCapture("获取最新操作建议", "02-latest-advice-composer.png");
await page.keyboard.press("Escape").catch(() => {});
await page.goto(base, { waitUntil: "commit", timeout: 15000 }).catch(() => {});
await settle();
await clickAndCapture("历史发布", "03-calendar-history-empty.png");
await capture("04-report-user-result.png", `${base}?run_id=${reportRun}`);
await clickAndCapture("技术详情", "05-report-technical-detail.png");
await page.goto(`${base}?run_id=${reportRun}`, { waitUntil: "commit", timeout: 15000 }).catch(() => {});
await settle();
await clickAndCapture("全屏查看", "06-analysis-chain-fullscreen.png");
await page.goto(base, { waitUntil: "domcontentloaded" });
await settle();
await clickAndCapture("资料研究入口", "07-research-library.png");

const logPath = resolve(repoRoot, "data/product-local/logs/dsh-web.stdout.log");
const log = readFileSync(logPath, "utf8");
const urls = [...log.matchAll(/http:\/\/127\.0\.0\.1:3097\/\?token=[^\s"']+/g)].map((match) => match[0]);
const dshUrl = urls.at(-1) ?? "http://127.0.0.1:3097/";
await page.goto(dshUrl, { waitUntil: "commit", timeout: 15000 }).catch(() => {});
await settle(1000);
await page.getByRole("treeitem", { name: /Crypto Macro Trader/ }).first().click().catch(() => {});
await page.waitForTimeout(400);
await page.getByRole("tab", { name: "轨迹" }).click().catch(() => {});
await page.waitForTimeout(400);
await page.screenshot({ path: join(output, "08-dsh-trajectory-console.png"), fullPage: false });

await browser.close();
await new Promise((resolveClosed) => preview.close(resolveClosed));
console.log(JSON.stringify({ output, files: 8 }));
