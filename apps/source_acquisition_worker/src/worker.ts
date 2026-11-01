import { createHash, randomUUID } from "node:crypto";
import { link, mkdir, open, readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { Configuration, PlaywrightCrawler, RequestList } from "crawlee";
import { chromium, type Browser } from "playwright";

export type AcquisitionMode = "dom" | "network" | "screenshot";

export interface AcquisitionReceipt {
  schema_version: "acquisition-receipt.v1";
  receipt_id: string;
  source_id: string;
  url: string;
  access_mode: "playwright";
  status: "succeeded" | "failed" | "partial";
  request_started_at?: string | null;
  request_completed_at?: string | null;
  observed_at: string | null;
  /** Assigned by the Hub after durable admission, never by the browser worker. */
  received_at?: string | null;
  status_code: number | null;
  artifacts: Array<{
    kind: "dom" | "network" | "screenshot" | "trace" | "download";
    path: string;
    content_hash: string;
  }>;
  error_code: string | null;
}

export interface AcquisitionOptions {
  sourceId: string;
  artifactDir: string;
  receiptId?: string;
  waitFor?: string;
  modes?: AcquisitionMode[];
  /** Server arrival includes queue time; these fields are required only for current FRED. */
  requestStartedAt?: string;
  deadlineAt?: string;
}

export interface AcquisitionSubmitResult {
  created: boolean;
  receipt_hash: string;
  receipt: AcquisitionReceipt;
}

function sha256(payload: Buffer | string): string {
  return createHash("sha256").update(payload).digest("hex");
}

function receiptId(sourceId: string, url: string, createdAt: string): string {
  return sha256(`${sourceId}\n${url}\n${createdAt}`).slice(0, 32);
}

export function stableReceiptId(sourceId: string, url: string, requestId: string): string {
  return sha256(`${sourceId}\n${url}\n${requestId}`).slice(0, 32);
}

export function isFredSeriesUrl(url: string): boolean {
  return /^https:\/\/fred\.stlouisfed\.org\/series\/(?:WRESBAL|DFII10|T10YIE)$/.test(url);
}

export function isUtcTimestamp(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString().slice(0, 19) === value.slice(0, 19);
}

function remaining(deadline: number): number {
  const duration = deadline - Date.now();
  if (duration <= 0) throw new Error("acquisition_deadline_elapsed");
  return duration;
}

export async function withAcquisitionDeadline<T>(task: () => Promise<T>, deadline: number): Promise<T> {
  const duration = remaining(deadline);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const value = await Promise.race([
      task(),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error("acquisition_deadline_elapsed")), duration);
      }),
    ]);
    remaining(deadline);
    return value;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function staticHttpsUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
    if (parsed.protocol !== "https:" || parsed.username || parsed.password
      || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")
      || host.startsWith("[") || /^\d+(?:\.\d+){3}$/.test(host)) return false;
    return true;
  } catch {
    return false;
  }
}

async function createArtifactOnce(destination: string, payload: Buffer, deadline: number): Promise<void> {
  const temporary = `${destination}.${process.pid}.${randomUUID()}.tmp`;
  const opening = open(temporary, "wx");
  void opening.then(handle => {
    if (Date.now() >= deadline) void handle.close().then(() => unlink(temporary)).catch(() => undefined);
  }, () => undefined);
  try {
    const handle = await withAcquisitionDeadline(() => opening, deadline);
    try {
      await withAcquisitionDeadline(() => handle.writeFile(payload), deadline);
      await withAcquisitionDeadline(() => handle.sync(), deadline);
    } finally {
      await handle.close();
    }
    remaining(deadline);
    try {
      await withAcquisitionDeadline(() => link(temporary, destination), deadline);
    } catch (error) {
      if (!(error instanceof Error) || !("code" in error) || error.code !== "EEXIST") throw error;
      const stored = await withAcquisitionDeadline(() => readFile(destination), deadline);
      if (!stored.equals(payload)) throw new Error("acquisition_artifact_conflict");
    }
    remaining(deadline);
  } finally {
    await unlink(temporary).catch(() => undefined);
  }
}

async function acquireFredCurrentSeries(url: string, options: AcquisitionOptions): Promise<AcquisitionReceipt> {
  if (!isFredSeriesUrl(url) || !isUtcTimestamp(options.deadlineAt)
    || !isUtcTimestamp(options.requestStartedAt) || options.waitFor !== undefined
    || options.modes?.length !== 1 || options.modes[0] !== "dom") {
    throw new Error("acquisition_fred_request_invalid");
  }
  const deadline = Date.parse(options.deadlineAt);
  const started = Date.parse(options.requestStartedAt);
  if (deadline > started + 20_000 || started > Date.now()) throw new Error("acquisition_fred_budget_invalid");
  remaining(deadline);
  const id = options.receiptId ?? receiptId(options.sourceId, url, options.requestStartedAt);
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("acquisition_receipt_id_invalid");
  const outputDir = path.resolve(options.artifactDir, options.sourceId, id);
  await withAcquisitionDeadline(() => mkdir(outputDir, { recursive: true }), deadline);
  let browser: Browser | undefined;
  let redirectDenied = false;
  let statusCode: number | null = null;
  let observedAt: string | null = null;
  let domArtifact: AcquisitionReceipt["artifacts"][number] | undefined;
  let failure: string | null = null;
  const launch = chromium.launch({ timeout: remaining(deadline) });
  // A launcher that resolves after its budget must dispose its own browser.
  void launch.then(created => {
    if (Date.now() >= deadline) void created.close().catch(() => undefined);
  }, () => undefined);
  try {
    const ownedBrowser = browser = await withAcquisitionDeadline(() => launch, deadline);
    const page = await withAcquisitionDeadline(() => ownedBrowser.newPage({ ignoreHTTPSErrors: false }), deadline);
    await withAcquisitionDeadline(() => page.route("**/*", async route => {
      const request = route.request();
      const mainNavigation = request.isNavigationRequest() && request.frame() === page.mainFrame();
      if ((mainNavigation && request.url() !== url) || (!mainNavigation && !staticHttpsUrl(request.url()))) {
        redirectDenied = true;
        await route.abort("blockedbyclient");
        return;
      }
      if (Date.now() >= deadline) {
        await route.abort("timedout");
        return;
      }
      await route.continue();
    }), deadline);
    const response = await withAcquisitionDeadline(() => page.goto(url, {
      waitUntil: "domcontentloaded", timeout: remaining(deadline),
    }), deadline);
    remaining(deadline);
    if (redirectDenied || page.url() !== url) throw new Error("acquisition_fred_redirect_denied");
    statusCode = response?.status() ?? null;
    if (statusCode !== 200) throw new Error("acquisition_fred_http_failed");
    const dom = await withAcquisitionDeadline(() => page.content(), deadline);
    observedAt = new Date().toISOString();
    const content = Buffer.from(dom, "utf8");
    if (redirectDenied || page.url() !== url) throw new Error("acquisition_fred_redirect_denied");
    if (content.length > 2 * 1024 * 1024) throw new Error("acquisition_dom_too_large");
    remaining(deadline);
    const domPath = path.join(outputDir, "dom.html");
    await createArtifactOnce(domPath, content, deadline);
    domArtifact = { kind: "dom", path: domPath, content_hash: sha256(content) };
  } catch (error) {
    failure = Date.now() >= deadline ? "acquisition_deadline_elapsed"
      : redirectDenied ? "acquisition_fred_redirect_denied"
      : error instanceof Error && /^acquisition_[a-z_]+$/.test(error.message) ? error.message
      : "acquisition_fred_browser_failed";
  } finally {
    if (browser) {
      let cleanupTimer: ReturnType<typeof setTimeout> | undefined;
      let closed = false;
      await Promise.race([
        browser.close().then(() => { closed = true; }, () => undefined),
        new Promise<void>(resolve => { cleanupTimer = setTimeout(resolve, 1000); }),
      ]);
      if (cleanupTimer !== undefined) clearTimeout(cleanupTimer);
      if (!closed) failure ??= "acquisition_browser_cleanup_pending";
    }
  }
  if (Date.now() >= deadline) failure = "acquisition_deadline_elapsed";
  const receipt: AcquisitionReceipt = {
    schema_version: "acquisition-receipt.v1", receipt_id: id, source_id: options.sourceId,
    url, access_mode: "playwright", status: failure ? "failed" : "succeeded",
    request_started_at: options.requestStartedAt, request_completed_at: new Date().toISOString(),
    observed_at: observedAt, received_at: null, status_code: statusCode,
    artifacts: failure || domArtifact === undefined ? [] : [domArtifact], error_code: failure,
  };
  // A timed-out attempt cannot create a successful receipt or reach Hub submission.
  if (failure !== null) {
    // Failure metadata alone may outlive the acquisition budget; it cannot be submitted as success.
    await createArtifactOnce(path.join(outputDir, "receipt.json"),
      Buffer.from(JSON.stringify(receipt, null, 2)), Date.now() + 250).catch(() => undefined);
    return receipt;
  }
  await createArtifactOnce(path.join(outputDir, "receipt.json"), Buffer.from(JSON.stringify(receipt, null, 2)), deadline);
  remaining(deadline);
  return receipt;
}

/** Crawlee queue/retry + Playwright DOM/network/screenshot/trace vertical slice. */
export async function acquireWithPlaywright(
  url: string,
  options: AcquisitionOptions,
): Promise<AcquisitionReceipt> {
  if (options.sourceId === "fred.current_macro") return acquireFredCurrentSeries(url, options);
  // The worker is single-job and writes its own content-addressed artifacts;
  // disabling Crawlee's cross-process filesystem persistence avoids stale
  // Windows request locks while retaining Crawlee's in-process retry queue.
  process.env.CRAWLEE_PERSIST_STORAGE ??= "false";
  Configuration.getGlobalConfig().set("persistStorage", false);
  const id = options.receiptId ?? receiptId(options.sourceId, url, new Date().toISOString());
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("acquisition_receipt_id_invalid");
  const outputDir = path.resolve(options.artifactDir, options.sourceId, id);
  await mkdir(outputDir, { recursive: true });
  const artifacts: AcquisitionReceipt["artifacts"] = [];
  let statusCode: number | null = null;
  let errorCode: string | null = null;
  let observedAt: string | null = null;
  const responses: string[] = [];
  let tracing = false;
  let pendingDownload: Promise<void> | null = null;

  // Passing an explicit in-memory RequestList avoids Crawlee's default
  // RequestQueue persistence path, which can leave stale locks on Windows.
  const requestList = await RequestList.open(null, [url]);
  const crawler = new PlaywrightCrawler({
    requestList,
    maxRequestsPerCrawl: 1,
    maxConcurrency: 1,
    maxRequestRetries: 0,
    // A receipt is the durable product artifact. Crawlee's optional runtime
    // statistics/session persistence is disabled for this one-job worker to
    // avoid writing SDK state and to keep the worker process isolated.
    useSessionPool: false,
    statisticsOptions: { persistenceOptions: { enable: false } },
    requestHandlerTimeoutSecs: 15,
    preNavigationHooks: [async ({ page }) => {
      page.on("download", (download) => {
        pendingDownload = (async () => {
          const suggested = path.basename(download.suggestedFilename() || "download.bin");
          const safeName = suggested.replace(/[^A-Za-z0-9._-]/g, "_") || "download.bin";
          const artifactPath = path.join(outputDir, safeName);
          await download.saveAs(artifactPath);
          observedAt = new Date().toISOString();
          artifacts.push({
            kind: "download",
            path: artifactPath,
            content_hash: sha256(await readFile(artifactPath)),
          });
        })();
      });
      page.on("response", async (response) => {
        const resourceType = response.request().resourceType();
        if (resourceType !== "xhr" && resourceType !== "fetch") return;
        try {
          responses.push(JSON.stringify({ url: response.url(), status: response.status(), body: await response.text() }));
        } catch {
          responses.push(JSON.stringify({ url: response.url(), status: response.status() }));
        }
      });
      await page.context().tracing.start({ screenshots: true, snapshots: true, sources: false });
      tracing = true;
    }],
    requestHandler: async ({ page, response }) => {
      // PlaywrightCrawler has already navigated the page and owns the retry
      // lifecycle. Navigating again here would race its request lock.
      statusCode = response?.status() ?? null;
      if (pendingDownload) await pendingDownload;
      if (options.waitFor) await page.waitForSelector(options.waitFor, { state: "visible", timeout: 10_000 });
      observedAt = new Date().toISOString();
      const modes = new Set(options.modes ?? ["dom", "network", "screenshot"]);
      const tracePath = path.join(outputDir, "trace.zip");
      if (modes.has("dom")) {
        const dom = await page.content();
        const artifactPath = path.join(outputDir, "dom.html");
        await writeFile(artifactPath, dom, "utf8");
        artifacts.push({ kind: "dom", path: artifactPath, content_hash: sha256(dom) });
      }
      if (modes.has("network")) {
        const network = responses.join("\n");
        const artifactPath = path.join(outputDir, "network.jsonl");
        await writeFile(artifactPath, network, "utf8");
        artifacts.push({ kind: "network", path: artifactPath, content_hash: sha256(network) });
      }
      if (modes.has("screenshot")) {
        const screenshot = await page.screenshot({ fullPage: true });
        const artifactPath = path.join(outputDir, "page.png");
        await writeFile(artifactPath, screenshot);
        artifacts.push({ kind: "screenshot", path: artifactPath, content_hash: sha256(screenshot) });
      }
      await page.context().tracing.stop({ path: tracePath });
      tracing = false;
      artifacts.push({ kind: "trace", path: tracePath, content_hash: sha256(await readFile(tracePath)) });
    },
    failedRequestHandler: async ({ request, error, page }) => {
      const capturedDownload = pendingDownload !== null;
      if (pendingDownload) {
        try {
          await pendingDownload;
          if (statusCode === null) statusCode = 200;
        } catch {
          errorCode = "download_save_failed";
        }
      }
      if (tracing && page) {
        try {
          await page.context().tracing.stop();
        } catch {
          // The failure provenance below remains authoritative.
        }
        tracing = false;
      }
      if (capturedDownload && artifacts.some((artifact) => artifact.kind === "download")) {
        // Playwright reports a navigation error when a response switches to a
        // browser download. The download artifact is the successful result;
        // do not turn this expected navigation transition into a receipt
        // failure or an alarming worker error line.
        errorCode = null;
        console.warn("acquisition download captured", request.url);
      } else {
        console.error("acquisition request failed", request.url, error);
        errorCode = `crawler_failed:${error instanceof Error ? error.name : "unknown"}`;
      }
    },
  });
  const requestStartedAt = new Date().toISOString();
  try {
    await crawler.run();
    if (pendingDownload) await pendingDownload;
  } catch (error) {
    errorCode = error instanceof Error ? error.name : "crawler_failed";
  }
  const requestCompletedAt = new Date().toISOString();
  const status = errorCode
    ? artifacts.length > 0 ? "partial" : "failed"
    : artifacts.length > 0 ? "succeeded" : "failed";
  const receipt: AcquisitionReceipt = {
    schema_version: "acquisition-receipt.v1",
    receipt_id: id,
    source_id: options.sourceId,
    url,
    access_mode: "playwright",
    status,
    request_started_at: requestStartedAt,
    request_completed_at: requestCompletedAt,
    observed_at: observedAt,
    received_at: null,
    status_code: statusCode,
    artifacts,
    error_code: status === "succeeded" ? null : errorCode ?? "no_artifacts",
  };
  await writeFile(path.join(outputDir, "receipt.json"), JSON.stringify(receipt, null, 2), "utf8");
  return receipt;
}

function fredReceiptMetadata(receipt: AcquisitionReceipt): string | null {
  const expectedKeys = ["schema_version", "receipt_id", "source_id", "url", "access_mode", "status",
    "request_started_at", "request_completed_at", "observed_at", "received_at", "status_code", "artifacts", "error_code"].sort();
  if (Object.keys(receipt).sort().join("|") !== expectedKeys.join("|") || !Array.isArray(receipt.artifacts)) return null;
  const utc = (value: unknown): string | null => {
    if (!isUtcTimestamp(value)) return null;
    const fraction = value.slice(19, -1).replace(/^\./, "");
    return `${value.slice(0, 19)}.${fraction.padEnd(6, "0")}Z`;
  };
  const started = utc(receipt.request_started_at);
  const completed = utc(receipt.request_completed_at);
  const observed = utc(receipt.observed_at);
  if (started === null || completed === null || observed === null) return null;
  for (const artifact of receipt.artifacts) {
    if (!artifact || typeof artifact !== "object"
      || Object.keys(artifact).sort().join("|") !== "content_hash|kind|path") return null;
  }
  return JSON.stringify([receipt.schema_version, receipt.receipt_id, receipt.source_id, receipt.url,
    receipt.access_mode, receipt.status, started, completed, observed, receipt.status_code,
    receipt.artifacts.map(artifact => [artifact.kind, artifact.path, artifact.content_hash]), receipt.error_code]);
}

/** Submit only the immutable receipt metadata; raw artifacts stay on disk. */
export async function submitReceiptToHub(
  receipt: AcquisitionReceipt,
  hubUrl: string,
  options?: { deadlineAt: string },
): Promise<AcquisitionSubmitResult> {
  const endpoint = `${hubUrl.replace(/\/$/, "")}/v1/source-acquisitions`;
  const fred = receipt.source_id === "fred.current_macro";
  if (fred && (!options || !isUtcTimestamp(options.deadlineAt) || receipt.status !== "succeeded")) {
    throw new Error("acquisition_fred_submit_invalid");
  }
  const deadline = fred && options ? Date.parse(options.deadlineAt) : null;
  const signal = deadline === null ? undefined : AbortSignal.timeout(remaining(deadline));
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(receipt),
    ...(signal ? { signal } : {}),
  });
  const payload = (await response.json()) as Partial<AcquisitionSubmitResult> & { detail?: string };
  if (!response.ok) {
    throw new Error(`hub_receipt_submit_failed:${response.status}:${payload.detail ?? "unknown"}`);
  }
  if (typeof payload.created !== "boolean" || typeof payload.receipt_hash !== "string"
    || !payload.receipt || payload.receipt.receipt_id !== receipt.receipt_id) {
    throw new Error("hub_receipt_submit_invalid_response");
  }
  if (deadline !== null) {
    remaining(deadline);
    const admitted = payload.receipt;
    const originalMetadata = fredReceiptMetadata(receipt);
    if (!/^[a-f0-9]{64}$/.test(payload.receipt_hash)
      || !isUtcTimestamp(admitted.received_at) || Date.parse(admitted.received_at) > deadline
      || Date.parse(admitted.received_at) < Date.parse(receipt.request_completed_at ?? "")
      || originalMetadata === null || fredReceiptMetadata(admitted) !== originalMetadata) {
      throw new Error("acquisition_fred_submit_invalid_response");
    }
  }
  return { created: payload.created, receipt_hash: payload.receipt_hash, receipt: payload.receipt };
}
