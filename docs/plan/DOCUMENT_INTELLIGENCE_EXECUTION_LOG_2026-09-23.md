# 资料智能交付执行记录

日期：2026-09-23  
目标：`DOCUMENT-INTELLIGENCE-DELIVERY`

## 里程碑

- [x] M0 契约、状态机、Research Scope 和迁移
- [x] M1 原件存储、解析 Port、MinerU/本地兜底和解析质量
- [x] M2 金融语义修正、差异和用户确认
- [x] M3 观点候选、审核和资料库查询
- [x] M4 Decision Desk 资料研究交互和现有 Run 接入
- [x] M5 公开网页导入、回放评测和完整自测

## 2026-09-23 启动审计

- 当前仓库已有内容寻址文本存储、`document.readback`、Playwright/Crawlee acquisition receipt、Research Context Manifest 和 Research 页面。
- `packages/kernel/.../research_context.py` 当前把 policy/data version 固定为 `crypto_macro`，必须先参数化。
- 当前 Python 环境没有 MinerU、Docling、PaddleOCR、PDF 或图片解析依赖；现有 HTTP document adapter 明确拒绝 PDF。
- MinerU 4.0 官方 CLI 支持 `mineru parse <file> --pages all -o <output>`，本实现使用可配置 CLI adapter，并提供轻量本地解析兜底；解析器不可用或扫描内容不可读时稳定进入 `blocked_low_quality`，不伪装成功。
- 当前代码有大量本轮前的未提交修改；本目标只在新增资料模块和必要集成点上追加，不重置或覆盖现有工作。

## 2026-09-23 M0-M4 实际落地

- [x] `contracts/schemas/research_library.schema.yaml` 已生成 Python/TypeScript DTO；`DocumentUpload`、`DocumentQuality`、`DocumentBlock`、`DocumentCorrection`、`AnalystClaim`、`ResearchRequest` 等都经同一份 canonical schema。
- [x] `0040_research_library` 已创建 `research_documents`、`research_document_versions`、`research_document_blocks`、`research_document_corrections`、`analyst_claims`、`research_run_scopes`，原件和派生内容由 `DocumentBundleStore` 内容寻址保存。
- [x] `DocumentParserRouter` 已接入可选 MinerU CLI、pypdf 文本/PDF 兜底以及无 OCR 图片的 fail-closed 占位解析；图片不会伪造文本，质量为 `blocked_low_quality`。
- [x] 资料状态链路为 `received → parsing → parsed → correcting → review → ready`；API 与共享 DB worker 均复用同一状态机，不另起队列。
- [x] 修正结果与原文分离，方向词/数字等高风险变化标记人工确认；确认后才抽取 `AnalystClaim`，观点保持候选/已审核状态并要求独立证据。
- [x] Decision Desk Research 页增加“资料研究入口”，显示原始解析块、质量信号、修正差异和观点候选；接口支持按 `research_family` 选择版本并写入 `research_run_scopes`。
- [x] DSH Research Worker 在构建上下文时读取已选资料的观点候选，作为 `document` 软证据投影进入既有 Context Manifest；没有复制 Run、Gate 或 Evidence Ledger。
- [x] 真实自测：`uv run pytest tests/research/test_library_pipeline.py -q`（3 passed）；`uv run pytest tests/contracts tests/migrations tests/e2e/test_api_flow.py tests/research/test_library_pipeline.py -q`（33 passed）；`pnpm web:test`（18 passed）；`pnpm web:build` 成功。

## 已知限制（必须在 M5 前处理或明确阻断）

- [ ] MinerU 未随默认环境安装，生产容器需要显式安装并设置 `DECISION_HUB_MINERU_BIN`；未安装时 PDF 走 pypdf，扫描 PDF 会被低质量闸门拦截。
- [ ] 当前金融语义修正是确定性安全基线，不会猜测缺失数字；接入模型修正时必须新增结构化模型 adapter、提示词版本和高风险字段复核，不能把模型输出直接覆盖原文。
- [x] 公开网页采集回执已通过 `web-import` 物化为 `research_document_versions`；仍要求上游先完成 Playwright/Crawlee acquisition receipt，API 不负责任意 URL 抓取。

## 2026-09-23 M5 完成与烟测

- [x] 新增 `POST /v1/research-library/web-import`：只接收现有 Source Acquisition receipt 的成功本地产物，校验根目录和文件后再进入同一不可变资料版本链路；API 不直接抓任意 URL。
- [x] 新增 `POST /v1/research-library/research`：在创建现有 Research Run 后写入 `research_run_scopes`，Worker 通过已选版本的观点候选构造既有 `EvidenceCandidate(kind=document)`；不复制 Harness/Run/Gate。
- [x] 新增 `documents` worker role 与 Compose 服务，heartbeat contract 扩展为 `documents_worker`，解析/修正和 API 共享同一 SQLite 状态机。
- [x] 浏览器回执 PIT 修复：回放使用 receipt 的 `observed_at` 作为 acquisition clock，避免历史 fixture 因当前墙钟漂移而被错误判定为 cutoff 过期。
- [x] 端到端烟测（API + documents worker + Vite）：上传 `smoke.txt` → `parsed/ready_for_correction` → 请求修正 → `review/ready` → 确认 → 创建带资料 scope 的 Research Run，全部返回预期状态；API `health/live=200`，前端 `5173=200`。
- [x] 全量自测（补强前基线）：`uv run pytest -q` → `788 passed, 4 skipped`；`pnpm web:test` → `18 passed`；`pnpm web:build` → 成功。

## 2026-09-23 收口补强：隐私路由与可选模型修正

- [x] `ResearchDocument` 持久化 `privacy_mode`，新增 `0041_document_privacy_mode`，默认历史资料为 `local_only`；模型修正不会因为配置打开而把本地资料发出。
- [x] 新增严格的 `FinancialSemanticCorrectionPayload` 和 `ModelFinancialSemanticCorrectionAdapter`。只有同时设置 `DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED=1`、`DECISION_HUB_LLM_ENABLED=1` 且资料声明 `remote_allowed` 时才调用现有 LangGraph/OpenAI-compatible runtime。
- [x] 模型只返回 block 级差异；服务校验 block id、原文精确匹配和结构化字段，所有模型变更默认 `requires_user_confirmation=true`，异常引用进入 unresolved，原件和原始解析永不覆盖。
- [x] 新增模型 adapter 单元测试，覆盖远程修正的审核态、原文保持不变和结构化差异。
- [x] 收口回归：`uv run pytest -q` → `789 passed, 4 skipped`；`pnpm web:test` → `18 passed`；`pnpm web:build` → 成功（仅保留 Vite 大包体积提示）。
- [x] 产品启动器补齐 `documents-worker`，与 API、Research、历史和采集 worker 一起纳入 `run-product.ps1` 的托管进程；实际 `/v1/operations` 显示 `hub-documents=online`，`8047/health/live=200`。

## 当前可交付边界

- 本地默认路径是可重复、可审计的确定性修正；需要模型语义修正时启用上述显式配置并提供 OpenAI-compatible 结构化输出模型。
- MinerU/OCR 仍是部署能力：没有安装时，文本 PDF 可用 pypdf，扫描 PDF/图片保持 `blocked_low_quality`，不会伪造结论。
- 资料契约和上下文选择支持 `crypto_macro`、`cn_equity`、`cross_asset` 三个研究域；当前可执行的 Decision Hub Research Run 仍是既有 `crypto_macro` pack，CN/跨资产资料可以上传、审核和隔离保存，但发起对应 Run 会明确返回 `research_family_not_executable`，不会误用 BTC pack 生成结论。
