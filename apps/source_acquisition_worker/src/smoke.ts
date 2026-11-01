import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { acquireWithPlaywright } from "./worker.js";

const server = createServer((_req, res) => {
  if (_req.url === "/download.pdf") {
    res.setHeader("content-type", "application/pdf");
    res.setHeader("content-disposition", "attachment; filename=fixture.pdf");
    res.end(Buffer.from("%PDF-1.4\nfixture\n%%EOF\n"));
    return;
  }
  res.setHeader("content-type", "text/html");
  res.end("<html><body><main data-ready=\"true\">Decision Hub fixture</main></body></html>");
});
await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
if (!address || typeof address === "string") throw new Error("fixture_server_not_started");
const dir = await mkdtemp(path.join(os.tmpdir(), "decision-hub-acquisition-"));
try {
  const receipt = await acquireWithPlaywright(`http://127.0.0.1:${address.port}/`, {
    sourceId: "fixture-public-page",
    artifactDir: dir,
    waitFor: "[data-ready=true]",
  });
  if (receipt.status !== "succeeded" || receipt.artifacts.length < 3) throw new Error("acquisition_smoke_failed");
  const loaded = JSON.parse(await readFile(path.join(dir, "fixture-public-page", receipt.receipt_id, "receipt.json"), "utf8"));
  if (loaded.receipt_id !== receipt.receipt_id) throw new Error("receipt_readback_failed");
  const download = await acquireWithPlaywright(`http://127.0.0.1:${address.port}/download.pdf`, {
    sourceId: "fixture-public-pdf",
    artifactDir: dir,
    modes: [],
  });
  if (download.status !== "succeeded" || !download.artifacts.some((item) => item.kind === "download")) {
    throw new Error("acquisition_download_smoke_failed");
  }
  console.log(JSON.stringify({
    receipt_id: receipt.receipt_id,
    artifacts: receipt.artifacts.length,
    download_receipt_id: download.receipt_id,
  }));
} finally {
  server.close();
  await rm(dir, { recursive: true, force: true });
}
