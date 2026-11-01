# 原链局部类型修复：完整 strict 与42项回归通过

日期：2026-10-24。此报告更新上一份 `INCREMENTAL_REPAIR.md` 中“31条诊断待处理”的状态，不覆盖原失败日志。项目最终目标仍在进行，未部署到原服务。

## 改动与提交

隔离工作区 `D:/DecisionHub-legacy-incremental`，分支 `repair/incremental-delivery`。本地源码提交：`bf73c89d03ae68e006906f36ec41821faac53a87`。

原102个入口及全部严格选项的补丁前对照，证明31条诊断与原基线逐项相同。随后只修七个消费/配置文件：

- `perpetual-synthesis-protocol.ts` 使用既有 `eventScenarioMarginalDistributionV1Schema.parse` 取得正确类型，保留原比较、binding及payload hash检查；没有修改生成契约或经济算法。
- `reviewer-model-view.ts` type-only导入现有SDK公开compaction事件声明；`tsconfig.json` 只加入对应的SDK路径，运行代码中没有新增该import。
- 四个旧测试按真实V2/V3 discriminant、SDK constructor、MessageId及完整payload适配。保留原dispatcher和所有业务断言、`auto:false`、固定prompt id以及原时钟/预算。

另按原SHA补齐两个已存在的受控输入，共727,295字节；一个来自Main、另一个来自ISO原封包。没有生成替代财务数据，也没有将它们计作真实事件或研究。已提交的18行并发补丁与原UI生产源码未改。

## 验证结果

| 验证 | 实际结果 |
| --- | --- |
| 完整native strict，原102入口 | exit 0，143.216937s，诊断日志0字节 |
| 必要业务回归，7个文件全选 | 42 passed / 42，7 passed / 7，62.71s，实际Node进程exit 0 |
| 源码独立审查 | Critical 0 / Important 0，七路径SHA钉住 |
| 非作者关键正反例复验 | 3 passed / 39未选运行，15.23s，Node exit 0；没有重跑完整42项或strict |
| 原UI基线（前一切片） | 98项通过，原`tsc -b`与Vite完整构建均exit 0 |

42项覆盖非空marginal的九分支事件方案、换件拒绝、实际独立child原件流程、大于1MB的完整独审证明及负审查handoff、真实SDK两次compaction与生命周期、完整原Harness、取消/截止、原上下文鉴真与七项竞态/负例。没有用跳过用例取得这次42项结果。

非作者另选非空marginal正例、换件拒绝、负审查handoff三项复验，七个源码SHA前后未变。原39项在这次定向复验中未选运行，不能再记作重复通过。回执：`D:/DecisionHub-v3-development/evidence/legacy-sdk-review/native-type-fresh3-702c0ea9-0ac1-4bcd-bd2c-985444df3a6a/runtime.json`。

运行解析显式使用当前D/Main contracts，以及同一现有SDK源；所有缓存与新生成的测试输出留在D。没有安装依赖或修改共享node_modules。测试允许临时`127.0.0.1`端口0服务，封禁外网；guard没有拒绝记录。合成Provider/registry、真实SDK与实际模型验收仍分别陈述，没有新的模型调用或正式库写入。

原始命令：

```text
node --max-old-space-size=8192 D:/DecisionHub-legacy-incremental/extensions/dsh/decision-hub/node_modules/typescript/bin/tsc --project D:/DecisionHub-v3-development/temp/legacy-reuse/native-type-repair/tsconfig.json
compiler_exit_code: 0

node D:/DecisionHub-legacy-incremental/extensions/dsh/decision-hub/node_modules/vitest/vitest.mjs run --config D:/DecisionHub-v3-development/temp/legacy-reuse/native-type-repair/runtime.config.mts --reporter=verbose
Test Files  7 passed (7)
Tests       42 passed (42)
Duration    62.71s
test_process_exit_code: 0
```

命令通过受控launcher注入现有只读SDK和loopback限制，没有增加原业务或测试超时。

## 保留的失败与边界

第一次launcher因Windows环境名大小写处理漏掉SYSTEMROOT，Node在JavaScript启动前CSPRNG初始化退出134；修正launcher环境筛选后，在新UUID目录运行。成功的42项结果与exit0已落盘后，外层Python打印彩色checkmark因GBK编码退出1；仅修正显示编码，没有为隐藏它重新跑42项。两次launcher问题、原日志、真正子进程退出码均分别保留，不能把外层exit1冒称0。

默认`git diff --check`对保留的CRLF报617处提示，原始非零退出保留；逐处核实没有实际尾部空格，没有为格式化改写原fixture。

证据根：`D:/DecisionHub-v3-development/temp/legacy-reuse/native-type-repair/`，包含full-strict回执、runtime UUID目录、双原件收据、根独立核验和精确九路径暂存范围；独立审查位于 `D:/DecisionHub-v3-development/evidence/legacy-sdk-review/native-type-micro-slice-review.json`。

## 剩余交付

后续继续原系统研究到报告的同Run闭环、报告历史源码依赖的局部处理，以及原PAPER的全期限/费用/退出/重启验证。生成构建身份所需的原配置与正式部署仍待核对。新的真实认证还需实际UTC健康；十四天合格率、PAPER实际成交及四周评分尚未满足，不计本地测试为这些指标。
