import { existsSync } from "node:fs";
import { readFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import { acquireWithPlaywright, isFredSeriesUrl, isUtcTimestamp, stableReceiptId, submitReceiptToHub, withAcquisitionDeadline, type AcquisitionMode } from "./worker.js";

const port = Number(process.env.SOURCE_ACQUISITION_PORT ?? "8038");
const hubUrl = process.env.SOURCE_HUB_URL ?? "http://127.0.0.1:8047";
const artifactDir = path.resolve(process.env.SOURCE_ACQUISITION_ROOT ?? "data/source-acquisition");
const sharedKey = process.env.SOURCE_ACQUISITION_KEY ?? "";
const allowedDomains = (process.env.SOURCE_ALLOWED_DOMAINS ?? "federalreserve.gov,ecb.europa.eu,boj.or.jp")
  .split(",")
  .map((item) => item.trim().toLowerCase())
  .filter(Boolean);
let active = Promise.resolve();

type AcquireRequest = {
  request_id: string;
  source_id: string;
  url: string;
  wait_for?: string;
  modes?: AcquisitionMode[];
  deadline_at?: string;
};

function json(response: ServerResponse, status: number, payload: unknown): void {
  response.statusCode = status;
  response.setHeader("content-type", "application/json; charset=utf-8");
  response.end(JSON.stringify(payload));
}

async function body(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  if (Buffer.concat(chunks).length > 64 * 1024) throw new Error("acquisition_request_too_large");
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function validRequest(payload: unknown): payload is AcquireRequest {
  if (!payload || typeof payload !== "object") return false;
  const value = payload as Record<string, unknown>;
  if (typeof value.request_id !== "string" || !value.request_id) return false;
  if (typeof value.source_id !== "string" || !value.source_id) return false;
  if (typeof value.url !== "string" || !/^https?:\/\//i.test(value.url)) return false;
  if (value.source_id === "fred.current_macro") {
    return isFredSeriesUrl(value.url) && isUtcTimestamp(value.deadline_at)
      && value.wait_for === undefined && Array.isArray(value.modes)
      && value.modes.length === 1 && value.modes[0] === "dom";
  }
  try {
    const host = new URL(value.url).hostname.toLowerCase().replace(/\.$/, "");
    if (host === "fred.stlouisfed.org" || host.endsWith(".fred.stlouisfed.org")) return false;
    if (!allowedDomains.some((domain) => host === domain || host.endsWith(`.${domain}`))) return false;
  } catch {
    return false;
  }
  return true;
}

async function acquire(payload: AcquireRequest, requestStartedAt?: string, deadlineAt?: string) {
  if (deadlineAt && Date.now() >= Date.parse(deadlineAt)) throw new Error("acquisition_deadline_elapsed");
  const id = stableReceiptId(payload.source_id, payload.url, payload.request_id);
  const receiptPath = path.join(artifactDir, payload.source_id, id, "receipt.json");
  if (existsSync(receiptPath)) {
    const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
    return (await submitReceiptToHub(receipt, hubUrl, deadlineAt ? { deadlineAt } : undefined)).receipt;
  }
  const receipt = await acquireWithPlaywright(payload.url, {
    sourceId: payload.source_id,
    artifactDir,
    receiptId: id,
    waitFor: payload.wait_for,
    modes: payload.modes ?? ["dom", "network", "screenshot"],
    ...(deadlineAt ? { deadlineAt, requestStartedAt } : {}),
  });
  if (deadlineAt && receipt.status !== "succeeded") return receipt;
  return (await submitReceiptToHub(receipt, hubUrl, deadlineAt ? { deadlineAt } : undefined)).receipt;
}

function enqueue<T>(task: () => Promise<T>): Promise<T> {
  const next = active.then(task, task);
  active = next.then(() => undefined, () => undefined);
  return next;
}

const server = createServer(async (request, response) => {
  const requestArrival = Date.now();
  if (request.method === "GET" && request.url === "/health/ready") {
    json(response, 200, { status: "ready", service: "source-acquisition-worker" });
    return;
  }
  if (request.method !== "POST" || request.url !== "/v1/acquire") {
    json(response, 404, { detail: "not_found" });
    return;
  }
  if (sharedKey && request.headers["x-decision-hub-source-key"] !== sharedKey) {
    json(response, 403, { detail: "source_acquisition_key_invalid" });
    return;
  }
  let fredRequest = false;
  try {
    const payload = await body(request);
    if (!validRequest(payload)) {
      json(response, 400, { detail: "source_acquisition_request_invalid" });
      return;
    }
    const fred = payload.source_id === "fred.current_macro";
    fredRequest = fred;
    const deadlineAt = fred ? new Date(Math.min(Date.parse(payload.deadline_at!), requestArrival + 20_000)).toISOString() : undefined;
    const requestStartedAt = fred ? new Date(requestArrival).toISOString() : undefined;
    const receipt = deadlineAt
      ? await withAcquisitionDeadline(() => enqueue(() => acquire(payload, requestStartedAt, deadlineAt)), Date.parse(deadlineAt))
      : await enqueue(() => acquire(payload));
    json(response, receipt.status === "succeeded" ? 200 : 502, receipt);
  } catch (error) {
    const detail = fredRequest
      ? error instanceof Error && /^acquisition_[a-z_]+$/.test(error.message) ? error.message : "source_acquisition_failed"
      : error instanceof Error ? error.message : "source_acquisition_failed";
    json(response, 502, { detail });
  }
});

await mkdir(artifactDir, { recursive: true });
server.listen(port, "127.0.0.1", () => {
  console.log(`source acquisition worker: http://127.0.0.1:${port}`);
});
