# DOCUMENT-INTELLIGENCE-DELIVERY

日期：2026-09-23  
状态：complete  
唯一目标：完成资料上传、解析复核、金融语义修正、观点审核、研究域隔离和现有 Research Run 接入，并以离线、集成和浏览器自测证明闭环。

## 产品结果

用户能够上传 PDF/图片，查看原件和原始识别结果，看到质量问题和修正文差异，确认后抽取观点并选择资料发起当前 crypto_macro Decision Hub Research Run。报告可回到资料版本和原始定位；未确认、低质量、未来时间或错误市场资料不能进入上下文。CN/跨资产资料先完成隔离存储和审核，待对应 pack 接入后再执行，不会误用 BTC pack。

## 实施范围

- P0：canonical contract、数据库迁移、Port、研究域策略；
- P1：不可变文件包、MinerU CLI 适配、文本/PDF 兜底、上传/解析 API；
- P2：版本化金融语义修正、差异、人工确认、观点候选与审核；
- P3：Research 资料子入口、Context Manifest、同一 Research Run 接入；
- P4：公开网页采集回执进入同一资料版本流程；
- P5：固定样本、隔离、回放、API、前端和端到端验收。

## 不变量

- 不新增第二套 Harness、Research Run、Evidence Ledger、Gate 或报告协议；
- DSH 不直接读上传目录或写资料状态；
- 原件、原始解析、修正文和观点是不同的不可变版本；
- 修正不能覆盖原始解析；
- 高风险数字、日期、百分比、价格、否定词和方向词修改必须人工确认；
- accepted 个人观点只作为软证据；
- 所有 Run 使用冻结的 Research Scope 和 Context Manifest；
- CI 不访问真实网络或要求真实模型。

## 验收门

1. canonical schema/codegen、Alembic upgrade 和 migration tests 通过；
2. 普通 PDF、扫描/图片低质量、表格/数字风险和重复上传 fixture 通过；
3. 原始解析、修正、差异、观点、审核和上下文 API 通过；
4. BTC/CN-equity、cutoff、版本和状态隔离测试通过；
5. Decision Desk 测试和生产构建通过；
6. 真实本地页面能完成上传到发起 Research Run 的主路径；
7. 执行记录包含实际命令、结果和已知限制。

## 停止条件

- 需要修改 DSH 上游源码；
- 需要复制 Run/Gate/账本；
- 无法保存原始解析和定位；
- 模型修正无法与原文分离；
- 资料只能靠全局 Prompt 隐式注入；
- 自测只能依赖真实外部网络。

## 交付记录

- [x] 后端主链路、网页 receipt 物化、资料研究入口和同一 Research Run 接入已实现。
- [x] `run-product.ps1` 已托管 `documents-worker`，上传后的解析、修正和观点抽取不会依赖一次性 API 请求存活。
- [x] 原件下载、原始解析块、质量闸门、修正差异和观点人工审核均有用户可见入口。
- [x] 788 个后端测试通过（4 个显式跳过的 live/外部依赖场景），前端 18 个测试和生产构建通过。
- [x] 已知限制写入执行日志：MinerU 为可选部署能力；未安装时扫描 PDF/图片会 fail-closed，不伪造文本；模型语义修正有显式 `remote_allowed` 路由和结构化人工复核，默认仍使用确定性安全基线。
