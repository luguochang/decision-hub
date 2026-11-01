# Q034：原永续报告页阅读快照导出

2026-10-24：精确四文件本地提交 `203c785`；原页增量实现及局部验证完成，项目目标保持 ACTIVE。

沿用原 `PerpetualSavedReport.tsx` 的导航、正文、概率定义、事实/反证、事件情景与候选展示，只增加“保存阅读快照 JSON”“保存离线 HTML”。另新增 `perpetualReportExport.ts` 和两份专属测试；原 API/DTO、旧测试、source 下载及“PDF 导出尚未接通”披露保留，没有替代前端或 API 翻译层。

JSON 包含该次已核对返回的完整报告值，包括原 `source_json`、`artifact_json` 字符串及 SHA、概率定义与原保存/核对时间；字符串不转为浮点重算。HTML 克隆当前报告 section，经标签/命名空间白名单净化，移除导航、下载和操作控件、活动属性及外部资源，保留正文与展开内容，附固定 CSS、严格 CSP 和安全转义的同份 JSON，无执行脚本或后端依赖。

导出仅允许当前 ready/run/owner；身份变化同步撤销旧可读引用并重读，换 run、隐藏或错误后不能用旧成功值导出。`financial_qualified=false`、`execution_authorized=false`、`bundle_export_status=unsupported_perpetual_version` 均保留，未知不补数。这是已保存正文/定义的阅读副本，不是包含全部原件、会话和源码的完整审计封包，也不恢复升级前未保存且当前 reader 无法读取的历史报告。

## 实际验证与失败保留

证据根目录为 `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/perpetual-reading-export-repair`，下表文件均保留原始命令、exit、时间及日志摘要，未覆盖失败原件。

| 阶段 | 实际结果与边界 | 原件前缀/文件 |
| --- | --- | --- |
| 新测试 RED | 原页集成 5 FAIL（按钮缺失）；pure helper 5 FAIL（未实现） | `integration-red*`、`pure-red*` |
| 初次实现 | 31 PASS / 2 FAIL；fake timers 冻住 jsdom FileReader 的 setImmediate，两项下载测试超时 | `first-green*` |
| 测试时钟修正 | 只将自有测试 fake timers 限于 setTimeout/clearTimeout；定向 2 PASS，未提高测试或产品时限 | `clock-correction*` |
| 原页影响集 | 3 文件 / 36 PASS，11.89s；其中旧 23 项保原断言，此结果早于最后下载控件净化修正 | `final-affected*` |
| 初次相关 TS | 2 个 ImportMeta.env 诊断：临时配置遗漏原 `vite-env.d.ts`；补原 ambient 后 strict exit 0，114.184s，输出空 | `owned-strict*`、`owned-strict-final*` |
| 最后 delta | 下载控件文本残留真实 1 RED；仅一行净化修正及两条断言后，新两文件 13 PASS / 9.44s | `download-control-red*`、`last-delta-tests*` |
| 最终相关 TS | 同 `tsconfig.owned-with-ambient.json` 增量 strict exit 0 / 6.555s，stdout/stderr 空；四源码 before/after 不变 | `last-delta-strict*`、`final-delta-verification.json` |
| Vite-only | 单次 exit 0，Vite 10.53s；新私有目录 33 文件 / 6,568,511 字节，inventory 全项回读匹配 | `vite-only-2af8ff235456400488fa2faa1077e96a/`、`vite-final-verification.json` |

最终命令均在 `D:/DecisionHub-legacy-incremental/apps/decision-desk` 执行；下列变量仅缩写已记录的实际绝对路径：

```powershell
$node = 'E:/software/system/nodejs/node.exe'
$e = 'D:/DecisionHub-v3-development/evidence/legacy-sdk-review/perpetual-reading-export-repair'
& $node node_modules/vitest/vitest.mjs run src/product/perpetualReportExport.test.ts src/product/PerpetualSavedReportExport.test.tsx --config "$e/vitest.config.mts" --reporter=verbose
& $node node_modules/typescript/bin/tsc --project "$e/tsconfig.owned-with-ambient.json" --pretty false
& $node node_modules/vite/bin/vite.js build --config D:/DecisionHub-legacy-incremental/apps/decision-desk/vite.config.ts --outDir "$e/vite-only-2af8ff235456400488fa2faa1077e96a/output"
```

最终测试摘要原文为 `Test Files 2 passed (2)`、`Tests 13 passed (13)`；构建摘要为 `✓ built in 10.53s`。Vite 原有大于 500kB chunk 警告保留，未改阈值或拆包。构建前后 340 个输入观察项相同（源码根超集，并非精确模块图）；新输出 inventory SHA 为 `2e1353e255566bf405f2ce5dd6eed45d01521c29397241aea6e24bc72a3e595a`。未重复 tsc-b、旧 UI98/native556 或最后 delta 后的旧23项。

C 的非作者只读复核为 Critical 0 / Important 0，核对最终四源码、API/旧测试、净化与身份撤销逻辑及最终验证原件；复核者未重跑测试。收据为 [q034-runner-nonauthor-review.json](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/q034-runner-nonauthor-review.json)。完整四 SHA 在 `scope-final.json`，最终测试/TS 另见 `final-delta-verification.json`，不将旧36项结果冒充最后字节全量重跑。

## Main 集成与交付边界

根于 **2026-10-24T08:46:48.171652Z** 核原页旧 SHA、新文件缺席并独占备份后，将四文件精确集成到 Main 本地源码。当前 Main 页面已是新候选，不能继续称原页未改。原件见 [Main integration receipt](D:/DecisionHub-v3-development/evidence/legacy-sdk-review/perpetual-reading-export-main-integration/receipt.json)；这不是原服务、profile 或 dist 部署，未更改正式数据库。

两份新测试已实际捕获 Blob，验证 JSON/HTML 内容及安全/身份边界；没有实际浏览器下载后断网打开的验收，没有真实用户报告离线样本。按根指示停止额外 fixture/SSR 装配。此次相关 TS 零诊断不等于 whole-project strict 零诊断，阅读导出不授予金融资格或 PAPER 权限；原 sameRun 6.0007604s 仍失败原 5s 门，Host、真实 PAPER、六组合/14天及四周目标均不能据此标为通过。
