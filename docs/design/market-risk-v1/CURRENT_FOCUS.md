# 当前执行入口

当前入口：[00总控](../decision-product-control-2026-10-17/00_MASTER_CONTROL.md)、[12工程文档地图与交付门](../decision-product-control-2026-10-17/12_ENGINEERING_READINESS_AND_DELIVERY.md)。review-4及OKX永续建议/纸面范围已获用户确认，随后要求补审文档；当前`review-5 / paused_for_document_review`。BTC/ETH普通市场与核心多事件双向闭环优先，黄金/指数后续。保留[11全链合同](../decision-product-control-2026-10-17/11_END_TO_END_DESIGN_CONTRACT.md)、[16项技术债](../decision-product-control-2026-10-17/10_TECHNICAL_DEBT_REGISTER.md)与[05唯一计划](../decision-product-control-2026-10-17/05_IMPLEMENTATION_PLAN.md)。本文件只作短接续摘要，不另立任务状态权威。

**2026-10-18 · 用户已明确批准review-5/OKX方案并恢复实施，目标active；当前D1真实主链恢复。当前任务以新总控execution-contract.json为准，短证据见EXECUTION_LOG.md。上段及下方10月17日暂停记录是历史，不覆盖本次恢复。**

本轮只读核对历史需求、设计、代码边界与验收证据，并保存讨论稿。release33 的候选修复和定向测试记录保留，尚未构建、启动或提交 BTC/ETH；下方实施步骤是暂停前的历史下一步，不构成恢复执行授权。2026-10-17T10:16:21Z 核对四个项目端口无监听，build/launch attempt 均未创建。评审入口：`docs/retrospectives/SOFTWARE_ENGINEERING_REVIEW_2026-10-17.md`。

## 当前结论

release32 已完成唯一正式构建、首次启动和本机 Google Chrome 单次 BTC 请求。Run `run_5bdc18a17d174045846b8f0a323d5909` 收集 8 条 evidence、4 次成功工具调用，但没有形成因果综合、独立复核、报告或同 Run 行动；11 项 core gate 仅 owner identity 与 real evidence/tools 通过，`core_passed=false`。ETH 没有提交。

真实首因是 `dsh_model_step_timeout`。公开 `host_terminal_result_binding_mismatch` 是二次遮蔽：合法的已认证失败终态没有 canonical result，`result_ref/result_hash` 同时为空，Host `/result` 却把它当成绑定破坏。

600 秒 model watchdog 从 native `step/start` 开始，覆盖 bind、retained context、provider input receipt 与真实 Provider stream。release32 中 Provider 真正开始前约耗 428.6 秒，只剩约 171.4 秒。当前证据不支持把它写成 gpt-6.1-sol 推理本身超过 600 秒，也不支持删减 386KB 上下文。

release32 已于 `2026-10-17T03:45:04.9685142Z` 通过官方 `infra/dsh/stop-product.ps1` 停止；Supervisor stopped，8047/3097/8037/8038 均 0 监听。旧请求不重放。

## 暂停前release33范围（历史计划，后续由新总控接管）

1. Host 将真实 binding 校验与结果存在性拆开：failed + strict null/null 返回 unavailable，单边空和身份/时间漂移仍 binding mismatch。
2. Runtime 对 authenticated failed + null/null 直接保留 `dsh_model_step_timeout`，跳过无意义 `/result` GET；Worker 不改写首因。
3. model watchdog 改为在真实 Provider delegate 前创建唯一 600 秒 deadline；准备阶段继续受 Task 和原有安全边界约束，同一步重入不续杯。
4. 加入无敏感数据的 step/receipt/provider/transport 分段诊断；不记录 prompt、URL、headers、token 或正文。
5. 不修改合同上限，不降低 `gpt-6.1-sol/medium/max_tokens=8192`，不删 evidence/因果链/工具 schema，不重构现有 callback proof 安全链。
6. 只跑直接覆盖两条根因的 TS/Python 窄门和受影响类型/编译检查。
7. 门通过后只构建、启动一次；本机 Chrome 只提交一个全新 BTC。BTC 11 项全过后才允许 ETH。

详细执行见 [release33 计划](../../../artifacts/decision-product-v1/core-main-flow-20261015/release33/PLAN.md)、[release33 状态](../../../artifacts/decision-product-v1/core-main-flow-20261015/release33/STATUS.md) 与 [release32 根因](../../../artifacts/decision-product-v1/core-main-flow-20261015/release32/BTC_FAILURE_ROOT_CAUSE.md)。

## 当前门状态

- 服务运行：否。
- release33 代码实施：候选修复与定向工程门已有记录；尚未正式验证。此前本入口“尚未开始”已过期，现据release33/STATUS更正。
- release33 正式构建：0。
- release33 正式启动：0。
- release33 BTC/ETH 提交：0/0。
- core whole accepted：否。
- production complete：否。
- financial effect proven：否。
- 手机端：排除。

## Kronos

- K1 已完成有限真实 shadow，状态为 `completed_shadow_not_financially_admitted`。ETH 尾部漏判、34/1536 非法 OHLC 和 CPU fixture 失败均保留；不重跑、不写成正式预测能力。
- K2 为 `planned_non_blocking`，`required_for_release33=false`，激活门是 release33 全新 BTC core 通过。
- K2 使用独立 `kronos-path-shadow.v1`、隔离 GPU Runtime 与 opt-in Worker，复用路径 shadow ledger；默认 release33 不启动，不写 Action，不自动 Promotion。
- 计划见 [Kronos K2 前瞻 Shadow](executable-product-v1/KRONOS_K2_PROSPECTIVE_SHADOW_PLAN_2026-10-17.md)。

## 仍未完成

- release33候选修复的真实组件集成与正式主链验证；
- 全新 BTC 的真实因果综合、独立复核、报告、可执行建议和同 Run 行动；
- BTC 通过后的 ETH 完整流程；
- M1–M4 PC 往返、事件/资产/方案结果回看；
- 官方安全重启和零新增 model/tool/PUT 的只读恢复证明；
- PC 截图索引、用户手册与最终可交付判定；
- Kronos K2A 接线及其后的 28 天 K2B 前瞻窗口；
- 黄金、纳指、恒科各自独立资产准入和宏观暴露验证。

黄金、纳指、恒科、FOMC 完整预判、长期胜率证明、手机和真实下单不属于 release33 有限发布门。旧 W0–W9 与 33/G/E/V/I 的完成/未完成边界继续保留。

[当前总控](../decision-product-control-2026-10-17/00_MASTER_CONTROL.md) · [暂停的旧机器状态](current-core-execution.json) · [旧任务证据](executable-product-v1/tasks.json) · [历史总计划](executable-product-v1/EXECUTION_PLAN.md) · [历史日志](executable-product-v1/EXECUTION_LOG.md)
