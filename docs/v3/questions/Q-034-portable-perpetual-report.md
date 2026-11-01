# Q-034：在原永续报告页补齐本地阅读快照

- 状态：DECIDED，2026-10-24。依据用户保留原系统并自主判断的授权，按 T-106/T-207/T-209 的可读、自包含 JSON/HTML 要求推进一个小切片；不等待外部逐项裁决。
- 实际依据：原页面已经有 `/report/source` 的 JSON 原件下载。成功的 `readSavedPerpetualReport` 已校验 run/owner、source/artifact 原文字节 SHA、正文和概率定义绑定，并把完整报告返回原页面。缺口是包含正文与概率定义的本地阅读快照和离线 HTML；不是全系统没有导出。只读定位见 `D:/DecisionHub-v3-development/temp/legacy-reuse/perpetual-v5-reading-export-feasibility.md`。
- 实现选择由执行者决定：沿用现有 React/TypeScript，不加 API 或依赖；在原报告页添加“保存阅读快照 JSON”“保存离线 HTML”。HTML 优先复用当前已核对报告的呈现内容并净化，不复制一套概率、事件或候选判断规则。

## 精确范围

隔离实现仅允许：

1. `apps/decision-desk/src/product/PerpetualSavedReport.tsx`，修改前核 Main/D 原字节；保留原导航、正文、原件下载和已有测试断言。
2. 新 `apps/decision-desk/src/product/perpetualReportExport.ts`。
3. 新 `apps/decision-desk/src/product/perpetualReportExport.test.ts`。
4. 新 `apps/decision-desk/src/product/PerpetualSavedReportExport.test.tsx`。

原查询 API/DTO、后端、contracts、旧测试、其它页面、PAPER/研究流程只读。每个新模块 ≤400 行、新函数 ≤60 行。新文件若已存在先停止本切片核对，不覆盖他人工作；不得以不完整 D 目录冒充完整 Main Python 源码。

## 行为与边界

- JSON 保存该次成功返回的原始 `source_json`、`artifact_json`、SHA、完整 probability_definitions（包括 unavailable）、run/owner/artifact/publication 身份与原保存/核对时间；价格和概率字符串保持原样。快照明确属于阅读副本。
- 保留 `financial_qualified=false`、`execution_authorized=false` 和原 `bundle_export_status=unsupported_perpetual_version`。缺失的盈利比较、首选、定义或概率仍按原报告显示未知，不据导出产生金融资格、交易权限或新的原件鉴真。
- 仅已核对成功且当前 run 的页面可导出。换 run、隐藏、读取失败或身份变化后，旧成功值不能继续作为当前可导出报告；身份变化事件立即清除旧值并按原流程重读。
- HTML 包含正文、概率和定义、事实/反证、机制、条件、事件情景及候选未知信息，附同份安全转义快照和样式；不得依赖后端、外部资源或执行脚本。去掉工作台导航、下载和其它操作控件，转义恶意文本/URL/闭合标签。原页面样式与布局沿用。
- 复用已有 Blob 下载与延迟回收对象 URL 的模式；导出失败显式反馈。原 source 下载继续存在，原“PDF 导出尚未接通”事实继续披露。
- 已保存文件可脱库阅读，不等于包含所有外部原件/会话/源码的完整审计封包，也不修复升级前未保存且当前 reader 无法读取的历史报告。此类历史读取问题须有具体失败后另做切片。

## 执行与验收

先写新测试并实际 RED，再实现；保存原始日志与精确来源。验证字符串/定义 available 与 unavailable 保真，未知不补数、两个 false/unsupported 不升级，恶意文本和 `</script>` 等净化，无网络依赖，原页成功下载与换 run/身份后的旧值拒绝。旧断言不改。

重测试单队列：当前 API-only 启动栈诊断优先，之后才运行这两份新测试和原页影响集；通过后运行相关 TypeScript 检查与必要原前端构建，不重复无关 native/Python 全批次。非作者复核、精确本地提交后再考虑 Main 源码集成，集成须原字节匹配及备份；不得直接换原服务、profile 或正式库。

最终用保存文件实际验证离线阅读，明确真实报告与合成呈现夹具的证据边界。当前原 sameRun 仍 6.0007604s 失败原 5s 门；本切片不替代该门、实际 Host/Provider、PAPER 或长期验收，项目目标 ACTIVE。


## 2026-10-24 实施续记

精确四文件提交 `203c785`，最后两新测试 13PASS、相关增量 strict0、单次 Vite-only 33产物成功，非作者 C0/I0；已备份并接回 Main 本地源码，未部署原服务/dist。旧23项在末次一行净化差量前通过，不称最终36项重跑。完整实际结果和 reading-only/无真实离线浏览器验收边界见 [Q034报告](../reports/INCREMENTAL_PERPETUAL_READING_EXPORT.md)。完整审计封包及长期资格仍未通过，不更新正式任务为 ACCEPTED。
