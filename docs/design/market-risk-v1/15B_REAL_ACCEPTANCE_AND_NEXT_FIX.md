# 15b 实际验收与下一批核心修复

2026-10-08。目标 active，唯一实施批次仍是 B2。PC 优先；33 项原需求及验收门不变。本页记录这次真实失败和下一批具体实现，八层方案见 [技术设计](TECHNICAL_DESIGN.md)，全部改造状态见 [33 项清单](IMPLEMENTATION_CHECKLIST.md)。

## 1. 已执行的真实验收

| 环节 | 真实结果 | 原件 |
|---|---|---|
| 标准构建/启动 | 15b 构建通过，UI/Host 各一次；3 个健康端点 200、7 个服务在线、schema0081。不是完整报告通过 | [构建](../../../artifacts/mr-release-b2b4-15b/product-validated-builds.json)、[启动](../../../artifacts/market-risk-implementation-20261007/B2/formal-start15b/receipt01.json) |
| BTC 普通按钮一次 | `run_4fddd3c278184b5fa5b44cb41b35026a` 失败，无最终报告；保留 12 条证据、9 次取证执行。原生输入回调超时，之后 Worker terminal conflict 是次因 | [唯一六 GET](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh15b/execution01/receipt.json)、[精确归因](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh15b/source-attribution01.json) |
| ETH 普通按钮一次 | `run_e664bc33a2614be19ca1cab6247e890e` degraded，保留 artifact；原生 emission 回调超时。质量为 untrusted/incomplete/unresolved/research_only，缺 selected synthesis 原件；context 返回 404 | 同上；HTTP200/artifact 存在不授完整研究资格 |
| PC 同 Run | 失败原因、已采纳证据、质量原件、保留全文及返回可操作；ETH 原文件实际下载 258440B，SHA 与质量记录相同。综合因果论据和时间窗口输出仍缺，完整报告门 F | [5 组动作/45 份原件](../../../artifacts/mr-pc15/actions01.json)、[下载核验](../../../artifacts/mr-pc15/eth-download-verified19.json) |
| 事件筛选 | 新 `事件类别` 控件唯一可识别，38 项筛为 10 项 FOMC，PC 实际通过 | [筛选截图](../../../artifacts/mr-pc15/calendar-filtered33.png) |

两资产未点击重试。六 GET 已执行一次，源码身份稳定。新的失败记录保留；已有未变主线复用具名历史验收。

## 2. 首因已定位到哪些代码

实际安全 phase 与 11 项正式源码 SHA 已绑定 [source-attribution01](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh15b/source-attribution01.json)，不能拿其他版本的 profile 当本次实测。

- ETH 失败 emission：source_capture 9.724518s，original_auth 6.264242s，RO source recheck 2.812704s。调用方在 15.003560s 断开，19.046529s 才返回 lifetime 409；没有进入 writer。
- BTC 失败 input：source_capture 5.944587s，original_auth 4.722168s，RO source recheck 1.390425s，再等 writer 3.379358s。15.002993s 断开，17.972552s 才返回原件校验 409；晚返回错误不是最早根因。
- `persistence/original_preparation.py`：只读准备、原件认证、SQL readset、写前/写后校验。
- `application/research_graph_binding.py` 的 `_ReadSourceGuard` 与 `research_graph_recipe.py`：源码闭包、库存、schema、runtime、loaded helper 的取证；当前存在重复纯构造验证。
- `application/provider_input_receipts.py` 的 `_prepare`：实际 V3 prepared execution、当前 recipe、V4 selection 及模型绑定核验。`native_context` 已传 `prepared=saved`；`_linked_input` 不是第二次完整 prepared 认证，不能误改。

## 3. 下一批实现与独立验收

| 项目/对应需求 | 怎样实现 | 必须保留 | 验收与当前状态 |
|---|---|---|---|
| F15-01 源码取证重复成本；MR17/O04 | 先在 DEV 固定原 manifest 做一次 cold/warm profile，拆出库存、文件 SHA、schema、runtime、selector 成本；只复用纯构造结果，验证实际闭包身份和未知 fallback。实现范围为 source guard/recipe 相关函数 | 不增加 15s 回调期限；不按 mtime/size 认源；不缓存执行权限；before/after 完整 source/schema/runtime/loaded identity 及原上下限 | 正控 manifest/源码身份相同且重复工作减少；文件/schema/runtime/helper 改变、恢复 mtime 仍失效。候选评估中，正式未晋升；最终还须真实回调 |
| F15-02 同只读事务重复认证；MR16/17 | 仅在 `_prepare` 的 original_auth 内，复用同一物理只读 snapshot 已完成的 exact recipe selection/installation 纯计算；使用现有 Session/Connection/native/root transaction、changes、clean UOW、ancestry 边界。避免为复用另加一次昂贵 source capture | 第一次完整原认证与 SQL readset；精确 ref/hash；正文、UTF8、tokens、previous 一跳；writer 当前权限/link/generation/lease/deadline/cancel/fence、完整 source/readset、postflush originals 和最终 CAS | 正控读数下降/输出不变；跨 Session/TX、dirty/flush、nested ancestry 不复用；selected/source/schema 篡改拒绝；终态、取消、changed readset 拒绝且零晚提交。候选实施中；不能以控制 P 代替真实报告 |
| F15-03 受管发布重复归档；MR02/28 | Producer 仅从已验证 prior receipt 的 component refs 重新认证旧 index/binding/每个原文件；完整声明、245 文件闭包及 runtime 相同时复用原索引；builder 按 exact ref/SHA 合并 | 未准入失败准备不得当权威；任何坏旧原件拒绝，不 fallback 隐藏；闭包真变仍新归档；2MiB 及标准 guard 不变 | 5 个准备门和实际 PS 合并门 P，2 源候选 [封签](../../../artifacts/market-risk-implementation-20261007/B2/component-archive-reuse18/source-seal18.json)。当前核心代码真变可能仍需新归档，正式未晋升 |
| F15-04 失败输出误导；MR19/32 | untrusted/reject 明确显示未通过、仅保留材料；critical_data_unavailable 不称已形成有限结论。只改两处产品文案 | 主页面原失败、全部证据/全文/质量/附件保留，不能隐藏缺项或放宽资格 | [候选封签](../../../artifacts/market-risk-implementation-20261007/B2/pc-label16/source-seal01.json)，正式未晋升；下一次标准 UI build 和同一旧失败页 PC 验证 |

这些项目各自可验收，不建立第二套任务平台，不把归档治理当报告首因。DSH 模型工具执行与 LangGraph 业务状态机的职责保持原设计。

## 4. 合并后怎样做一次真实验收

1. 审核各候选源码与正负控制；冻结合批 SHA。先保旧源码、受管旧 recipe/component 原件，核下一 receipt 实际尺寸，固定 guard 不变。
2. 标准停止、受管晋升、一次标准构建/启动。所有必要健康门通过后才提交新研究。
3. BTC/ETH 普通按钮各一次。首先单资产观察回调耗时和终态；再启动另一资产核实际运行中监控及接口，避免未定位前用并发掩盖首因。
4. 同 Run 核正文/最强反方/root review、ReviewV2 的方向/尾部/monitor/report 四影响、实际 token 和费用未知值、最终 publication/context/quality/case/actual consumer。原件 ref/hash/cutoff/known_at 必须精确；不能补造消费者引用。
5. PC 核新摘要、原全文、附件下载 SHA、返回、失败恢复和保留主线。记录 action、截图、原请求/Run、真实结果与未过门；更新清单和执行合同。
6. 首因修复并经新真实证据通过才能关闭相关门。失败保留并只修实际受影响范围，不重复测试未变 P，不同条件才允许下一次请求。

## 5. 核心报告通过后仍需完成的链路

| 剩余 | 验收方式 | 目前资格 |
|---|---|---|
| 自动复查建议→持久计划→真实子 Run | exact parent/plan/child 身份与幂等、终态、新报告/PC；manual 不替自动触发 | 尚未完整通过 |
| 经验使用与组件升级/回滚 | 实际 publication/case/消费者引用、cutoff 前检索；固定有/无记忆及方法对照 | 工程控制有限 P，真实效果未通过 |
| O04 性能 | 正式 risk summary 至少100次、含投影和索引，p95≤500ms；并发/超时/缺数全留分母 | 未达正式门；单个读取或 45s 容忍不替成绩 |
| Q05 与 U01 | 20 真实材料、模型理解及两方法真实对照；3 名非开发用户做5个任务，正确率≥80%，关键安全误解0 | 20工程控制P/93输入；full Q05、用户验证未通过 |
| 影子评价 | 已注册28日、基线/单候选、全分母/成熟标签，固定截止输出不足或无增益也结项 | 真实前瞻在采集，未证明增益 |
| 宏观、共识、仓位、黄金 | 每来源实际时点/许可/报价/历史版本及独立准入；能力不足明确记录 | 具体外部能力未满足，不借其他资产或当前历史伪造 |

固定试验北京时间11月4日08:00结束，末路径11月5日08:01，标签截止11月6日08:01；不自动延长。100 独立 UTC 日/30 事件资格与 28 日工程结项分开。工程修复期间继续可独立工作；没有预测优势也必须给真实负结论。

**当前：whole_product_accepted=false，financial_effect_qualified=false。**
