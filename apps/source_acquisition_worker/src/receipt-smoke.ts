import { spawn } from "node:child_process";
import { createServer, type Server } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { stableReceiptId, type AcquisitionReceipt } from "./worker.js";

async function listen(server: Server): Promise<number> {
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("test_server_not_started");
  return address.port;
}

async function close(server: Server): Promise<void> {
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}

const received: AcquisitionReceipt[] = [];
const hub = createServer(async (request, response) => {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(Buffer.from(chunk));
  const receipt = JSON.parse(Buffer.concat(chunks).toString("utf8")) as AcquisitionReceipt;
  received.push(receipt);
  response.statusCode = 202;
  response.setHeader("content-type", "application/json");
  response.end(JSON.stringify({
    receipt: { ...receipt, received_at: new Date().toISOString() },
    created: received.length === 1,
    receipt_hash: "a".repeat(64),
  }));
});
const source = createServer((_request, response) => {
  response.setHeader("content-type", "text/html");
  response.end("<main data-ready='true'>Audited page</main>");
});
const hubPort = await listen(hub);
const sourcePort = await listen(source);
const reserved = createServer();
const workerPort = await listen(reserved);
await close(reserved);
const dir = await mkdtemp(path.join(os.tmpdir(), "decision-hub-receipt-smoke-"));
const worker = spawn(
  process.execPath,
  [path.resolve("node_modules/tsx/dist/cli.mjs"), "src/server.ts"],
  {
    cwd: process.cwd(),
    env: {
      ...process.env,
      SOURCE_ACQUISITION_PORT: String(workerPort),
      SOURCE_HUB_URL: `http://127.0.0.1:${hubPort}`,
      SOURCE_ACQUISITION_ROOT: dir,
      SOURCE_ALLOWED_DOMAINS: "127.0.0.1",
      SOURCE_ACQUISITION_KEY: "smoke-key",
    },
    stdio: "ignore",
  },
);

try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${workerPort}/health/ready`);
      if (response.ok) { ready = true; break; }
    } catch {
      // Wait for the local worker process to bind its port.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!ready) throw new Error("source_acquisition_worker_not_ready");

  const failedUrl = "http://127.0.0.1:1/unreachable";
  const request = async (requestId: string, url: string) => {
    const response = await fetch(`http://127.0.0.1:${workerPort}/v1/acquire`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-decision-hub-source-key": "smoke-key",
      },
      body: JSON.stringify({ request_id: requestId, source_id: "fixture-browser", url }),
    });
    return { status: response.status, receipt: await response.json() as AcquisitionReceipt };
  };
  const first = await request("failure-1", failedUrl);
  const retry = await request("failure-1", failedUrl);
  const success = await request("success-1", `http://127.0.0.1:${sourcePort}/ok`);

  if (first.status !== 502 || retry.status !== 502 || success.status !== 200) {
    throw new Error("receipt_http_status_invalid");
  }
  if (received.length !== 3 || received[0]?.status !== "failed"
    || received[1]?.status !== "failed" || received[2]?.status !== "succeeded") {
    throw new Error("failed_or_successful_receipt_not_submitted");
  }
  if (first.receipt.receipt_id !== stableReceiptId("fixture-browser", failedUrl, "failure-1")
    || retry.receipt.receipt_id !== first.receipt.receipt_id
    || first.receipt.observed_at !== null
    || !first.receipt.received_at) {
    throw new Error("failed_receipt_identity_or_timeline_invalid");
  }
  const failureDisk = JSON.parse(await readFile(
    path.join(dir, "fixture-browser", first.receipt.receipt_id, "receipt.json"), "utf8",
  )) as AcquisitionReceipt;
  if (failureDisk.received_at !== null || failureDisk.observed_at !== null) {
    throw new Error("worker_fabricated_observation_or_hub_receive_time");
  }
  const started = Date.parse(success.receipt.request_started_at ?? "");
  const observed = Date.parse(success.receipt.observed_at ?? "");
  const completed = Date.parse(success.receipt.request_completed_at ?? "");
  const hubReceived = Date.parse(success.receipt.received_at ?? "");
  if (!(started <= observed && observed <= completed && completed <= hubReceived)) {
    throw new Error("successful_receipt_timeline_invalid");
  }
  console.log(JSON.stringify({ failed_receipts_submitted: 2, successful_receipts_submitted: 1 }));
} finally {
  worker.kill();
  await close(source);
  await close(hub);
  await rm(dir, { recursive: true, force: true });
}
