# Source Acquisition Worker

This worker is the browser access layer for public, permitted sources. It uses
Crawlee for a one-item request list and retry lifecycle, and Playwright for an
isolated browser page. Each job writes a content-addressed DOM snapshot,
network JSONL, screenshot, trace, and `receipt.json` under the configured
artifact directory.

```powershell
pnpm --filter @decision-hub/source-acquisition-worker typecheck
pnpm --filter @decision-hub/source-acquisition-worker smoke
pnpm --filter @decision-hub/source-acquisition-worker smoke:receipts
pnpm --filter @decision-hub/source-acquisition-worker server
```

可选地设置 `SOURCE_HUB_URL=http://127.0.0.1:8047` 运行 `canary`，worker 会把
`receipt.json` 的元数据提交到 Hub 的 `/v1/source-acquisitions`。Hub 只保存
receipt、hash 和来源身份，原始 DOM/网络/截图/trace 仍保留在采集工件目录；提交
成功不等于 Evidence 已采纳；`browser.readback` normalizer 会在研究 capability 中验证
DOM hash、提取正文并附上 receipt，最后仍需 Evidence Admission。

`server` 提供仅监听 `127.0.0.1` 的 `POST /v1/acquire` 和 `GET /health/ready`。
调用方必须提供 `request_id`、`source_id` 和公开 URL；服务只允许配置的官方域名，按
`source_id + url + request_id` 复用同一 receipt，并将成功、部分成功、失败的 receipt
全部提交 Hub。浏览器请求前记录 `request_started_at`，完成后记录
`request_completed_at`；只有真正观察到页面或下载内容才填写 `observed_at`，未观察
到内容的失败记录为 `null`。`received_at` 由 Hub 收到并落库时填写，worker 本地的
`receipt.json` 保持为 `null`。失败时 worker 返回包含 receipt 的 HTTP 502，供调用方
查看错误来源和重试；相同 request_id 会复用并重新提交已有 receipt。

研究 MCP 的
`browser.readback` capability 会验证 DOM 工件 hash、提取可引用正文，并将
`acquisition_receipt_id` 写入候选 Evidence。

The worker does not decide whether data is trusted. The Hub Evidence Admission
layer must validate the receipt, source authority, point-in-time cutoff and
independence before accepting an Evidence candidate. Do not use it to bypass
login walls, rate limits, robots rules or access controls.
