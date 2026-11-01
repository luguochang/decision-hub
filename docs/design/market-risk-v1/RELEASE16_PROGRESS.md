# 核心批次 16：已完成实际验收

16b 标准构建/启动通过；BTC 新研究输入回传超时、未生成报告；ETH 原生成功并保存完整报告，但根审核及必要上下文缺件，质量门未过。正式 O04 p95=1.329s，未达 <0.5s；30m 自动复查达到日配额后过期，无子 Run。17 核心修复在候选目录进行，尚未晋升。整产品与金融效果未验收。

## 实际发布

16b 标准构建和 Ready 已通过：[构建](../../../artifacts/mr-release-b2b4-16b/product-validated-builds.json)、[Ready](../../../artifacts/market-risk-implementation-20261007/B2/formal-start16b/receipt01.json)。16 首轮已知最小回执超2MiB的失败与两次晋升原件保留在 [promotion01](../../../artifacts/market-risk-implementation-20261007/B2/release16/promotion01.json)、[promotion02](../../../artifacts/market-risk-implementation-20261007/B2/release16/promotion02.json)。旧9475原件与250个selector版本已保留；未删除历史归档或放松原上限。

## 两个真实请求均已结束

| 资产 | Run | 真实结果 |
|---|---|---|
| BTC | `run_4343a50f67ad4fbb9e91d79ac841e09c` | native failed/input timeout；Run failed，无artifact。首因10:51:43.893 UTC，不重试该Run |
| ETH | `run_0310e29fd8534b799e58cd7fa2710787` | native completed 11:08:57.426 UTC；Run degraded，artifact `art_ab573b411ce7456f8cdd112275cdfa5d`，报告完整/上下文不完整/质量未解决 |

[唯一六 GET](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh16b/execution01/receipt.json)在11:19:11 UTC完成，总体F，源稳定。ETH publication绑定、实际synthesis、主反机制、三个窗口、sidecar同源通过；root review原件和required body不完整，不能授质量P。`eth-terminal03.json`当时Run仍running；实际终态以[phase04](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh16b/eth-phase04.json)为准，不改写旧文件。

## 实际 PC 与性能

[PC有限范围/截图](../../../artifacts/mr-pc16b/actions01.json)、[ETH首屏](../../../artifacts/mr-pc16b/eth-report-header39.png)、[BTC真实失败](../../../artifacts/mr-pc16b/btc-terminal-header23.png)。ETH浏览器原件下载300570B，SHA `55ff50b31a0600c92ee8ec3315ac94a631214c103a079cd69fc72f1521e6dd63`，与质量原件相同。实际证据弹窗/关闭、反方/完整分析/发布凭据/返回已操作；不宣全部按钮通过。

[正式 O04](../../../artifacts/market-risk-implementation-20261007/B2/formal-o04-16b/actual01/receipt.json)：100次200/严格投影通过，但p95=1.329321s，原门<0.5s未过。后续各资产一次ROprofile证明700/705原件读取和712/717 SQL，详见[瓶颈](../../../artifacts/market-risk-implementation-20261007/B2/formal-o04-16b/profile-actual01/receipt01.json)，不是新正式性能P。

## 自动复查与首因

ETH三个真实建议已落三计划。30m计划11:15:21 UTC日nonmanual计数12/限12，被延后；11:30:20 UTC过期skipped，无child。24h/72h尚计划中。来源[精确只读预算审计](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh16b/eth-auto-review-budget05.json)，SQLchanges0。计划落库P，child闭环F，未提高额度或注入时钟。

BTC精确input phase阶段和5个operation原件见[归因](../../../artifacts/market-risk-implementation-20261007/B2/formal-real-dsh16b/btc-provider-phase-attribution01/ANALYSIS01.md)。新进程纯读source profile另存[原件](../../../artifacts/market-risk-implementation-20261007/B2/source-guard-profile16b/formal-actual01/receipt01.json)，其8个旧capsule YAML缺pin的检查F须用委托代码硬SHA说明，原F不覆盖/不重跑；冷profile不冒充live warm原因。

下一批具体源、实现和验收：[16b 实际验收和批次17执行方案](16B_REAL_ACCEPTANCE_AND_NEXT_FIX.md)。15b原失败及历史证据保持；目标active，整产品未交付。
